"""Short-lived consent for account-linked walk-in intake."""
import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.db.mixins import UUIDPrimaryKeyMixin


class IntakeConsent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = 'intake_email_consents'
    officer_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('case_mgmt.users.id'))
    station_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('case_mgmt.stations.id'))
    complainant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('case_mgmt.complainants.id'))
    email_digest: Mapped[str] = mapped_column(String(64))
    code_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_state: Mapped[str] = mapped_column(String(30))

