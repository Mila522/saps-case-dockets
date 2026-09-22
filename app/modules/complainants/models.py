from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.modules.access.models import User


class Complainant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "complainants"
    __table_args__ = (
        CheckConstraint("preferred_contact_method IN ('SMS', 'EMAIL', 'PHONE')", name="preferred_contact_method"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.users.id", ondelete="SET NULL"), unique=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(254))
    phone_number: Mapped[str] = mapped_column(String(30))
    preferred_contact_method: Mapped[str] = mapped_column(String(10))
    identity_type: Mapped[str | None] = mapped_column(String(50))
    identity_number_encrypted: Mapped[str | None] = mapped_column(Text)
    identity_number_hash: Mapped[str | None] = mapped_column(String(128), unique=True)
    address_line_1: Mapped[str | None] = mapped_column(String(255))
    address_line_2: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(150))
    province: Mapped[str | None] = mapped_column(String(100))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    privacy_notice_version: Mapped[str | None] = mapped_column(String(50))
    consent_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User | None] = relationship(back_populates="complainant")
