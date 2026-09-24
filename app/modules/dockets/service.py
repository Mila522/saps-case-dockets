"""Station-scoped digital-docket reads and commander approval decisions."""
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.access.models import Role, UserRole
from app.modules.authentication.security import utcnow
from app.modules.complaints.models import Complaint
from app.modules.dockets.models import Docket, DocketApproval
from app.modules.dockets.schemas import DocketApprovalOut, DocketApprovalRequest, DocketOut
from app.modules.investigations.models import DocketStatusHistory
from app.modules.refusals.models import ComplaintDecision
from app.modules.stations.models import Officer, Station
from app.modules.system.service import allocate_cas_number


class DocketService:
    def __init__(self, db: Session):
        self.db = db

    def list_for_commander(self, user_id: uuid.UUID, status: str | None,
                           *, limit: int, offset: int) -> list[DocketOut]:
        try:
            officer = self._active_officer_with_role(user_id, 'STATION_COMMANDER')
            query = select(Docket).join(Complaint, Complaint.id == Docket.complaint_id).where(
                Complaint.station_id == officer.station_id)
            if status:
                query = query.where(Docket.status == status)
            rows = list(self.db.scalars(query.order_by(
                Docket.opened_at.desc(), Docket.id.desc()).limit(limit).offset(offset)).all())
            for row in rows:
                self.db.add(AuditLog(actor_type='USER', actor_user_id=user_id,
                                    action='docket.view_station', entity_type='docket',
                                    entity_id=row.id, station_id=officer.station_id))
            self.db.add(AuditLog(actor_type='USER', actor_user_id=user_id,
                                action='docket.list_station', entity_type='station',
                                entity_id=officer.station_id, station_id=officer.station_id))
            result = [DocketOut.model_validate(row) for row in rows]
            self.db.commit()
            return result
        except SQLAlchemyError:
            self.db.rollback()
            raise HTTPException(503, 'Docket queue unavailable') from None
        except Exception:
            self.db.rollback()
            raise

    def open(self, user_id: uuid.UUID, complaint_id: uuid.UUID) -> DocketOut:
        try:
            officer = self._active_officer_with_role(user_id, 'CHARGE_OFFICER')
            complaint = self.db.scalar(select(Complaint).where(
                Complaint.id == complaint_id).with_for_update())
            if complaint is None or complaint.station_id != officer.station_id:
                raise HTTPException(404, 'Complaint not found')
            if complaint.status != 'ACCEPTED':
                raise HTTPException(409, f'Docket cannot be created from status {complaint.status}')
            existing = self.db.scalar(select(Docket.id).where(Docket.complaint_id == complaint.id))
            if existing is not None:
                raise HTTPException(409, 'A docket already exists for this complaint')
            decision = self.db.scalar(select(ComplaintDecision).where(
                ComplaintDecision.complaint_id == complaint.id,
                ComplaintDecision.decision == 'ACCEPTED').order_by(
                    ComplaintDecision.decision_sequence.desc()).limit(1))
            if decision is None:
                raise HTTPException(409, 'Accepted complaint decision not found')
            station = self.db.scalar(select(Station).where(
                Station.id == complaint.station_id, Station.is_active.is_(True)).with_for_update(read=True))
            if station is None:
                raise HTTPException(409, 'Complaint station is inactive')
            now = utcnow()
            docket = Docket(
                complaint_id=complaint.id, cas_number=allocate_cas_number(self.db, station, now),
                status='PENDING_APPROVAL', created_from_decision_id=decision.id,
                opened_by_officer_id=officer.id, opened_at=now,
            )
            self.db.add(docket)
            self.db.flush()
            self.db.add(DocketStatusHistory(
                docket_id=docket.id, from_status=None, to_status='PENDING_APPROVAL',
                changed_by_user_id=user_id, change_reason='Docket created from accepted complaint',
                changed_at=now,
            ))
            complaint.status = 'DOCKET_CREATED'
            self.db.add(AuditLog(actor_type='USER', actor_user_id=user_id,
                                action='docket.create', entity_type='docket',
                                entity_id=docket.id, station_id=officer.station_id))
            result = DocketOut.model_validate(docket)
            self.db.commit()
            return result
        except SQLAlchemyError:
            self.db.rollback()
            raise HTTPException(503, 'Docket service unavailable') from None
        except Exception:
            self.db.rollback()
            raise

    def get_for_commander(self, user_id: uuid.UUID, docket_id: uuid.UUID) -> DocketOut:
        try:
            officer = self._active_officer_with_role(user_id, 'STATION_COMMANDER')
            docket = self.db.scalar(select(Docket).join(
                Complaint, Complaint.id == Docket.complaint_id).where(
                    Docket.id == docket_id, Complaint.station_id == officer.station_id))
            if docket is None:
                raise HTTPException(404, 'Docket not found')
            self.db.add(AuditLog(actor_type='USER', actor_user_id=user_id,
                                action='docket.view_station', entity_type='docket',
                                entity_id=docket.id, station_id=officer.station_id))
            self.db.commit()
            return DocketOut.model_validate(docket)
        except SQLAlchemyError:
            self.db.rollback()
            raise HTTPException(503, 'Docket service unavailable') from None
        except Exception:
            self.db.rollback()
            raise

    def approve(self, user_id: uuid.UUID, docket_id: uuid.UUID,
                data: DocketApprovalRequest) -> DocketApprovalOut:
        try:
            officer = self._active_officer_with_role(user_id, 'STATION_COMMANDER')
            docket = self.db.scalar(select(Docket).join(
                Complaint, Complaint.id == Docket.complaint_id).where(
                    Docket.id == docket_id, Complaint.station_id == officer.station_id).with_for_update())
            if docket is None:
                raise HTTPException(404, 'Docket not found')
            if docket.status != 'PENDING_APPROVAL':
                raise HTTPException(409, f'Docket cannot be reviewed from status {docket.status}')

            sequence = (self.db.scalar(select(func.max(DocketApproval.decision_sequence)).where(
                DocketApproval.docket_id == docket.id)) or 0) + 1
            now = utcnow()
            approval = DocketApproval(
                docket_id=docket.id, decided_by_officer_id=officer.id,
                decision=data.decision, notes=data.notes,
                decision_sequence=sequence, decided_at=now,
            )
            self.db.add(approval)
            self.db.flush()
            if data.decision == 'APPROVED':
                docket.status = 'APPROVED'
                self.db.add(DocketStatusHistory(
                    docket_id=docket.id, from_status='PENDING_APPROVAL', to_status='APPROVED',
                    changed_by_user_id=user_id, change_reason=data.notes, changed_at=now,
                ))
            self.db.add(AuditLog(actor_type='USER', actor_user_id=user_id,
                                action=f'docket.review.{data.decision.lower()}', entity_type='docket',
                                entity_id=docket.id, station_id=officer.station_id))
            result = DocketApprovalOut(
                id=approval.id, docket_id=docket.id, decision=approval.decision,
                notes=approval.notes, decision_sequence=approval.decision_sequence,
                decided_at=approval.decided_at, docket_status=docket.status,
            )
            self.db.commit()
            return result
        except SQLAlchemyError:
            self.db.rollback()
            raise HTTPException(503, 'Docket service unavailable') from None
        except Exception:
            self.db.rollback()
            raise

    def _active_officer_with_role(self, user_id: uuid.UUID, role_code: str) -> Officer:
        role = self.db.scalar(select(Role.id).join(UserRole).where(
            UserRole.user_id == user_id, Role.code == role_code))
        if role is None:
            raise HTTPException(403, f'{role_code.replace("_", " ").title()} role required')
        officer = self.db.scalar(select(Officer).where(
            Officer.user_id == user_id, Officer.is_active.is_(True)))
        if officer is None:
            raise HTTPException(403, 'Active officer profile required')
        return officer
