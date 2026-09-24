"""Charge-officer decisions on a complaint; station-scoped, transactional, audited."""
import uuid

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.complaints.models import Complaint
from app.modules.complaints.schemas import ComplaintTracking
from app.modules.refusals.models import ComplaintDecision, RefusalEscalation, RefusalReason
from app.modules.refusals.schemas import (ComplaintDecisionOut, ComplaintDecisionRequest,
                                          EscalationResolutionRequest, RefusalEscalationOut,
                                          RefusalReasonOut)
from app.modules.authentication.security import utcnow
from app.modules.access.models import Role, UserRole
from app.modules.stations.models import Officer

DECIDABLE_STATUSES = {'SUBMITTED', 'UNDER_REVIEW'}


class ComplaintDecisionService:
    def __init__(self, db: Session):
        self.db = db

    def start_review(self, user_id: uuid.UUID, complaint_id: uuid.UUID) -> ComplaintTracking:
        """Explicit review step needed by the A-to-B-to-C workflow."""
        try:
            officer = self.db.scalar(select(Officer).where(
                Officer.user_id == user_id, Officer.is_active.is_(True)))
            role = self.db.scalar(select(Role.id).join(UserRole).where(
                UserRole.user_id == user_id, Role.code == 'CHARGE_OFFICER'))
            if officer is None or role is None:
                raise HTTPException(403, 'Active charge officer required')
            complaint = self.db.scalar(select(Complaint).where(
                Complaint.id == complaint_id, Complaint.station_id == officer.station_id).with_for_update())
            if complaint is None:
                raise HTTPException(404, 'Complaint not found')
            if complaint.status != 'SUBMITTED':
                raise HTTPException(409, 'Only a submitted complaint can start review')
            complaint.status = 'UNDER_REVIEW'
            complaint.review_started_at = utcnow()
            self.db.add(AuditLog(actor_type='USER', actor_user_id=user_id,
                action='complaint.review', entity_type='complaint', entity_id=complaint.id,
                station_id=officer.station_id))
            self.db.flush()
            result = ComplaintTracking.model_validate(complaint)
            self.db.commit()
            return result
        except SQLAlchemyError:
            self.db.rollback()
            raise HTTPException(503, 'Complaint review unavailable') from None
        except Exception:
            self.db.rollback()
            raise

    def decide(self, user_id: uuid.UUID, complaint_id: uuid.UUID,
               data: ComplaintDecisionRequest) -> ComplaintDecisionOut:
        try:
            officer = self.db.scalar(select(Officer).where(
                Officer.user_id == user_id, Officer.is_active.is_(True)))
            if officer is None:
                raise HTTPException(403, 'Active officer profile required')
            is_charge_officer = self.db.scalar(select(Role.id).join(UserRole).where(
                UserRole.user_id == user_id, Role.code == 'CHARGE_OFFICER'))
            if is_charge_officer is None:
                raise HTTPException(403, 'Charge officer role required')

            # Row lock: no two officers can decide the same complaint concurrently.
            complaint = self.db.scalar(select(Complaint).where(
                Complaint.id == complaint_id).with_for_update())
            if complaint is None:
                raise HTTPException(404, 'Complaint not found')
            if complaint.station_id != officer.station_id:
                # Same response shape for missing and out-of-scope, per existing tracking pattern.
                raise HTTPException(404, 'Complaint not found')
            if complaint.status not in DECIDABLE_STATUSES:
                raise HTTPException(409, f'Complaint cannot be decided from status {complaint.status}')

            reason = None
            if data.decision == 'REFUSED':
                reason = self.db.scalar(select(RefusalReason).where(
                    RefusalReason.id == data.refusal_reason_id, RefusalReason.is_active.is_(True)))
                if reason is None:
                    raise HTTPException(404, 'Active refusal reason not found')
                if reason.requires_officer_notes and not (data.officer_notes and data.officer_notes.strip()):
                    raise HTTPException(422, 'officer_notes is required for this refusal reason')

            next_sequence = (self.db.scalar(select(func.max(ComplaintDecision.decision_sequence)).where(
                ComplaintDecision.complaint_id == complaint_id)) or 0) + 1

            now = utcnow()
            decision_row = ComplaintDecision(
                complaint_id=complaint_id,
                decision=data.decision,
                decided_by_officer_id=officer.id,
                refusal_reason_id=reason.id if reason else None,
                officer_notes=data.officer_notes,
                decision_sequence=next_sequence,
                decided_at=now,
            )
            self.db.add(decision_row)
            self.db.flush()

            if data.decision == 'ACCEPTED':
                complaint.status = 'ACCEPTED'
            else:
                must_escalate = reason.requires_escalation or reason.is_non_compliant
                complaint.status = 'ESCALATED' if must_escalate else 'REFUSED'
                if must_escalate:
                    targets = {'STATION_COMMANDER', 'NCC'} if reason.is_non_compliant else {'STATION_COMMANDER'}
                    for target in targets:
                        self.db.add(RefusalEscalation(
                            complaint_id=complaint_id,
                            complaint_decision_id=decision_row.id,
                            target=target,
                        ))

            result = ComplaintDecisionOut(
                id=decision_row.id, complaint_id=complaint_id, decision=decision_row.decision,
                refusal_reason_id=decision_row.refusal_reason_id, officer_notes=decision_row.officer_notes,
                decision_sequence=decision_row.decision_sequence, decided_at=decision_row.decided_at,
                complaint_status=complaint.status,
            )

            self.db.add(AuditLog(actor_type='USER', actor_user_id=user_id,
                                action=f'complaint.decide.{data.decision.lower()}', entity_type='complaint',
                                entity_id=complaint_id, station_id=officer.station_id))
            self.db.commit()
            return result
        except SQLAlchemyError:
            self.db.rollback()
            raise HTTPException(503, 'Complaint decision unavailable') from None
        except HTTPException:
            self.db.rollback()
            raise


class RefusalService:
    def __init__(self, db: Session):
        self.db = db

    def list_reasons(self) -> list[RefusalReasonOut]:
        try:
            rows = self.db.scalars(select(RefusalReason).where(
                RefusalReason.is_active.is_(True)).order_by(RefusalReason.name, RefusalReason.id)).all()
            return [RefusalReasonOut.model_validate(row) for row in rows]
        except SQLAlchemyError:
            self.db.rollback()
            raise HTTPException(503, 'Refusal reason service unavailable') from None

    def list_escalations(self, user_id: uuid.UUID) -> list[RefusalEscalationOut]:
        try:
            query = self._scoped_escalations(user_id)
            rows = self.db.scalars(query.order_by(
                RefusalEscalation.escalated_at.desc(), RefusalEscalation.id.desc())).all()
            for row in rows:
                complaint = self.db.get(Complaint, row.complaint_id)
                self.db.add(AuditLog(actor_type='USER', actor_user_id=user_id,
                                    action='refusal.escalation.view', entity_type='refusal_escalation',
                                    entity_id=row.id, station_id=complaint.station_id))
            self.db.commit()
            return [RefusalEscalationOut.model_validate(row) for row in rows]
        except SQLAlchemyError:
            self.db.rollback()
            raise HTTPException(503, 'Refusal escalation service unavailable') from None
        except Exception:
            self.db.rollback()
            raise

    def acknowledge(self, user_id: uuid.UUID, escalation_id: uuid.UUID) -> RefusalEscalationOut:
        return self._update_escalation(user_id, escalation_id, resolve=None)

    def resolve(self, user_id: uuid.UUID, escalation_id: uuid.UUID,
                data: EscalationResolutionRequest) -> RefusalEscalationOut:
        return self._update_escalation(user_id, escalation_id, resolve=data.resolution_notes)

    def _scoped_escalations(self, user_id: uuid.UUID):
        roles = set(self.db.scalars(select(Role.code).join(UserRole).where(UserRole.user_id == user_id)).all())
        officer = self.db.scalar(select(Officer).where(
            Officer.user_id == user_id, Officer.is_active.is_(True)))
        allowed = []
        if 'NCC_OFFICER' in roles:
            allowed.append(RefusalEscalation.target == 'NCC')
        if 'STATION_COMMANDER' in roles and officer is not None:
            allowed.append((RefusalEscalation.target == 'STATION_COMMANDER') &
                           (Complaint.station_id == officer.station_id))
        if not allowed:
            raise HTTPException(403, 'Authorized escalation role and scope required')
        return (select(RefusalEscalation).join(
            Complaint, Complaint.id == RefusalEscalation.complaint_id).where(or_(*allowed)))

    def _update_escalation(self, user_id: uuid.UUID, escalation_id: uuid.UUID,
                           *, resolve: str | None) -> RefusalEscalationOut:
        try:
            row = self.db.scalar(self._scoped_escalations(user_id).where(
                RefusalEscalation.id == escalation_id).with_for_update())
            if row is None:
                raise HTTPException(404, 'Refusal escalation not found')
            now = utcnow()
            if resolve is None:
                if row.status != 'OPEN':
                    raise HTTPException(409, 'Only open escalations can be acknowledged')
                row.status = 'ACKNOWLEDGED'
                row.acknowledged_by_user_id = user_id
                row.acknowledged_at = now
                action = 'refusal.escalation.acknowledge'
            else:
                if row.status == 'RESOLVED':
                    raise HTTPException(409, 'Escalation is already resolved')
                row.status = 'RESOLVED'
                row.resolved_by_user_id = user_id
                row.resolved_at = now
                row.resolution_notes = resolve
                action = 'refusal.escalation.resolve'
            complaint = self.db.get(Complaint, row.complaint_id)
            self.db.add(AuditLog(actor_type='USER', actor_user_id=user_id, action=action,
                                entity_type='refusal_escalation', entity_id=row.id,
                                station_id=complaint.station_id))
            self.db.commit()
            return RefusalEscalationOut.model_validate(row)
        except SQLAlchemyError:
            self.db.rollback()
            raise HTTPException(503, 'Refusal escalation service unavailable') from None
        except Exception:
            self.db.rollback()
            raise
