"""Historical SMS schema only. No routes or workers read/write these records."""
import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.db.mixins import UUIDPrimaryKeyMixin


class PhoneProof(Base):
    __tablename__ = 'sms_phone_proofs'
    complainant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('case_mgmt.complainants.id'), primary_key=True)
    phone_digest: Mapped[str] = mapped_column(String(64))
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SmsChallenge(UUIDPrimaryKeyMixin, Base):
    __tablename__ = 'sms_challenges'
    purpose: Mapped[str] = mapped_column(String(20))
    complainant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('case_mgmt.complainants.id'))
    complaint_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('case_mgmt.complaints.id'))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('case_mgmt.users.id'))
    phone_digest: Mapped[str] = mapped_column(String(64))
    code_digest: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_state: Mapped[str] = mapped_column(String(40), default='PENDING')


class SmsRateLimit(Base):
    __tablename__ = 'sms_rate_limits'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    count: Mapped[int] = mapped_column(Integer)


class SmsIntent(Base):
    __tablename__ = 'sms_intents'
    notification_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('case_mgmt.notifications.id'), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(160), unique=True)
    phone_digest: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(50), default='QUEUED')

    provider_message_ids: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    delivery_receipts: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
