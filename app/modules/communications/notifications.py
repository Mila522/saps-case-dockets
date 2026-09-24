"""Transactional in-app delivery. Workflow callers own commit/rollback."""
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import or_, select

from app.modules.authentication.security import utcnow
from app.modules.audit.models import AuditLog
from app.modules.complainants.models import Complainant
from app.modules.communications.common import ScopedService, transactional
from app.modules.communications.models import Notification, NotificationAttempt
from app.modules.communications.schemas import NotificationOut


def notify_complainant(db, complaint, event_type, *, docket_id=None, actor_user_id=None):
    """Call once within the successful workflow mutation transaction, never after commit.

    Messages deliberately contain no case narrative. IN_APP delivery is atomic with
    the event; external delivery requires a separately configured provider/outbox.
    """
    now = utcnow()
    row = Notification(id=uuid4(), recipient_complainant_id=complaint.complainant_id,
        complaint_id=complaint.id, docket_id=docket_id, channel='IN_APP', event_type=event_type,
        subject='Case update', message='An update is available. Sign in to view your case.',
        status='DELIVERED', sent_at=now, delivered_at=now, created_at=now)
    db.add(row)
    db.add(NotificationAttempt(notification=row, attempt_number=1, provider='IN_APP',
        outcome='SENT', attempted_at=now))
    db.add(AuditLog(actor_type='USER' if actor_user_id else 'SYSTEM', actor_user_id=actor_user_id,
        action='notification.delivered', entity_type='notification', entity_id=row.id,
        station_id=complaint.station_id))
    return row


class NotificationService(ScopedService):
    def query(self, user):
        self.permissions(user)
        owners = select(Complainant.id).where(Complainant.user_id == user.id)
        return select(Notification).where(or_(Notification.recipient_user_id == user.id,
            Notification.recipient_complainant_id.in_(owners)), Notification.channel == 'IN_APP',
            Notification.status == 'DELIVERED')

    @transactional
    def list(self, user, limit, offset):
        rows = self.db.scalars(self.query(user).order_by(Notification.created_at.desc(),
            Notification.id.desc()).limit(limit).offset(offset))
        result = [NotificationOut.model_validate(row) for row in rows]
        self.audit(user, 'notification.list', 'notification')
        return result

    @transactional
    def get(self, user, notification_id):
        row = self.db.scalar(self.query(user).where(Notification.id == notification_id))
        if row is None:
            raise HTTPException(404, 'Notification not found')
        self.audit(user, 'notification.read', 'notification', row.id)
        return NotificationOut.model_validate(row)
