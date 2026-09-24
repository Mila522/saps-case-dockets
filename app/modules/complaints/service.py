"""Ownership-scoped tracking with mandatory transactional read audits."""
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.complainants.models import Complainant
from app.modules.complaints.models import Complaint
from app.modules.complaints.schemas import ComplaintTracking, ComplaintTrackingPage
from app.modules.complaints.schemas import ComplaintRegistration
from app.modules.authentication.security import utcnow
from app.modules.stations.models import Station
from app.modules.system.service import allocate_complaint_reference
from app.modules.communications.notifications import notify_complainant


class ComplaintRegistrationService:
    def __init__(self, db: Session):
        self.db = db

    def register(self, user_id: uuid.UUID, data: ComplaintRegistration) -> ComplaintTracking:
        try:
            owner_id = self.db.scalar(select(Complainant.id).where(
                Complainant.user_id == user_id).with_for_update())
            if owner_id is None:
                raise HTTPException(403, 'Linked complainant profile required')
            # A shared row lock keeps the station active and its code stable until commit.
            station = self.db.scalar(select(Station).where(
                Station.id == data.station_id, Station.is_active.is_(True)).with_for_update(read=True))
            if station is None:
                raise HTTPException(404, 'Active station not found')
            now = utcnow()
            if data.incident_occurred_at is not None and data.incident_occurred_at > now:
                raise HTTPException(422, 'Incident date cannot be in the future')
            reference = allocate_complaint_reference(self.db, station, now)
            row = Complaint(**data.model_dump(), complainant_id=owner_id,
                            channel='ONLINE', status='SUBMITTED', reference_number=reference,
                            submitted_at=now)
            self.db.add(row)
            self.db.flush()
            result = ComplaintTracking.model_validate(row)
            notify_complainant(self.db, row, 'complaint.registered', actor_user_id=user_id)
            self.db.add(AuditLog(actor_type='USER', actor_user_id=user_id,
                                action='complaint.submit', entity_type='complaint',
                                entity_id=row.id, station_id=station.id))
            self.db.commit()
            return result
        except SQLAlchemyError:
            self.db.rollback()
            raise HTTPException(503, 'Complaint registration unavailable') from None
        except Exception:
            self.db.rollback()
            raise


class ComplaintTrackingService:
    def __init__(self, db: Session):
        self.db = db

    def track(self, user_id: uuid.UUID, complaint_id: uuid.UUID | None = None,
              *, limit: int = 20, offset: int = 0):
        try:
            owner_id = self.db.scalar(select(Complainant.id).where(Complainant.user_id == user_id))
            if owner_id is None:
                raise HTTPException(403, 'Linked complainant profile required')
            query = select(Complaint).where(Complaint.complainant_id == owner_id)
            if complaint_id is not None:
                row = self.db.scalar(query.where(Complaint.id == complaint_id))
                if row is None:
                    # Identical response for missing and another person's complaint.
                    raise HTTPException(404, 'Complaint not found')
                rows = [row]
                result = ComplaintTracking.model_validate(row)
            else:
                if not 1 <= limit <= 100 or offset < 0:
                    raise HTTPException(422, 'Invalid pagination')
                rows = list(self.db.scalars(query.order_by(Complaint.submitted_at.desc(), Complaint.id.desc())
                                           .limit(limit + 1).offset(offset)).all())
                has_more = len(rows) > limit
                rows = rows[:limit]
                result = ComplaintTrackingPage(items=[ComplaintTracking.model_validate(row) for row in rows],
                                               limit=limit, offset=offset, has_more=has_more)
            for row in rows:
                self.db.add(AuditLog(actor_type='USER', actor_user_id=user_id,
                                    action='complaint.track_own', entity_type='complaint',
                                    entity_id=row.id, station_id=row.station_id))
            if complaint_id is None:
                self.db.add(AuditLog(actor_type='USER', actor_user_id=user_id,
                                    action='complaint.list_own', entity_type='complainant', entity_id=owner_id))
            self.db.commit()
            return result
        except SQLAlchemyError:
            self.db.rollback()
            raise HTTPException(503, 'Complaint tracking unavailable') from None
        except Exception:
            self.db.rollback()
            raise
