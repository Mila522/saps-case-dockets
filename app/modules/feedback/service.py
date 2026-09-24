"""Append-only feedback with case scope and mandatory transactional auditing."""
from datetime import datetime, timezone
from functools import wraps
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.modules.audit.models import AuditLog
from app.modules.communications.models import CaseFeedback
from app.modules.feedback.repository import FeedbackRepository
from app.modules.feedback.schemas import FeedbackPage, FeedbackResponse
from app.modules.communications.notifications import notify_complainant


def transactional(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except SQLAlchemyError:
            self.db.rollback()
            raise HTTPException(503, 'Feedback service unavailable') from None
        except Exception:
            self.db.rollback()
            raise
    return wrapped


class FeedbackService:
    def __init__(self, db):
        self.db = db
        self.repo = FeedbackRepository(db)

    def authorize(self, user, docket_id, *, write=False):
        if not user.is_active:
            raise HTTPException(403, 'Insufficient permission')
        permissions = self.repo.permissions(user.id)
        required = {'feedback.provide'} if write else {'case.track_own', 'docket.view_assigned'}
        if not required.intersection(permissions):
            raise HTTPException(403, 'Insufficient permission')
        context = self.repo.context(docket_id, lock=write)
        if context is None:
            raise HTTPException(404, 'Docket not found')
        docket, complaint = context
        if not write and 'case.track_own' in permissions and self.repo.owner(complaint.complainant_id, user.id):
            return docket, complaint, None
        officer = self.repo.officer(user.id, lock=write)
        if (write or 'docket.view_assigned' in permissions) and officer is not None and officer.station_id == complaint.station_id:
            if self.repo.assigned(docket.id, officer.id, lock=write):
                return docket, complaint, officer
        raise HTTPException(404, 'Docket not found')

    def audit(self, user, complaint, action, entity_id, entity_type='case_feedback'):
        self.db.add(AuditLog(actor_type='USER', actor_user_id=user.id, action=action,
            entity_type=entity_type, entity_id=entity_id, station_id=complaint.station_id))

    @transactional
    def publish(self, user, docket_id, data):
        docket, complaint, officer = self.authorize(user, docket_id, write=True)
        if data.supersedes_feedback_id is not None:
            previous = self.repo.get(docket.id, data.supersedes_feedback_id, complaint.complainant_id)
            if previous is None:
                raise HTTPException(404, 'Feedback not found')
            if self.repo.superseded(previous.id):
                raise HTTPException(409, 'Feedback already superseded; correct the latest record')
        now = datetime.now(timezone.utc)
        row = CaseFeedback(id=uuid4(), docket_id=docket.id, complainant_id=complaint.complainant_id,
            provided_by_officer_id=officer.id, is_official=True, published_at=now, created_at=now,
            **data.model_dump())
        self.db.add(row)
        self.db.flush()
        self.audit(user, complaint, 'feedback.published', row.id)
        notify_complainant(self.db, complaint, 'feedback.published', docket_id=docket.id, actor_user_id=user.id)
        response = FeedbackResponse.model_validate(row)
        self.db.commit()
        return response

    @transactional
    def list(self, user, docket_id, limit=50, offset=0):
        if not 1 <= limit <= 100 or offset < 0:
            raise HTTPException(422, 'Invalid pagination')
        docket, complaint, _ = self.authorize(user, docket_id)
        items = self.repo.list(docket.id, complaint.complainant_id, limit, offset)
        response = FeedbackPage(items=[FeedbackResponse.model_validate(row) for row in items], limit=limit, offset=offset)
        self.audit(user, complaint, 'feedback.listed', docket.id, 'docket')
        self.db.commit()
        return response

    @transactional
    def get(self, user, docket_id, feedback_id):
        docket, complaint, _ = self.authorize(user, docket_id)
        row = self.repo.get(docket.id, feedback_id, complaint.complainant_id)
        if row is None:
            raise HTTPException(404, 'Feedback not found')
        response = FeedbackResponse.model_validate(row)
        self.audit(user, complaint, 'feedback.viewed', row.id)
        self.db.commit()
        return response
