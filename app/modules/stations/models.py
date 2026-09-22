from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.modules.access.models import User


class Station(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "stations"

    station_code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    province: Mapped[str] = mapped_column(String(100), index=True)
    district: Mapped[str | None] = mapped_column(String(150))
    address_line_1: Mapped[str | None] = mapped_column(String(255))
    address_line_2: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(150))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    phone_number: Mapped[str | None] = mapped_column(String(30))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())

    officers: Mapped[list[Officer]] = relationship(back_populates="station", passive_deletes="all")


class Officer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "officers"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.users.id", ondelete="RESTRICT"), unique=True)
    station_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.stations.id", ondelete="RESTRICT"), index=True)
    service_number: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    rank: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())

    user: Mapped[User] = relationship(back_populates="officer")
    station: Mapped[Station] = relationship(back_populates="officers")
