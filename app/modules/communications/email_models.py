"""Case email metadata; obsolete anonymous challenge mappings retained for data safety."""
import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.db.mixins import UUIDPrimaryKeyMixin


class CaseEmailIntent(Base):
    __tablename__ = 'case_email_intents'
    notification_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('case_mgmt.notifications.id'), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(160), unique=True)
    email_digest: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(50))


class TrackingChallenge(UUIDPrimaryKeyMixin, Base):
    __tablename__ = 'email_tracking_challenges'
    complainant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('case_mgmt.complainants.id'))
    complaint_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('case_mgmt.complaints.id'))
    email_digest: Mapped[str] = mapped_column(String(64))
    code_digest: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_state: Mapped[str] = mapped_column(String(40), default='PENDING')


class TrackingRateLimit(Base):
    __tablename__ = 'email_tracking_rate_limits'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    count: Mapped[int] = mapped_column(Integer)
