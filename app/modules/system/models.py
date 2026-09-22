from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.modules.stations.models import Station


class IdentifierCounter(TimestampMixin, Base):
    __tablename__ = "identifier_counters"
    __table_args__ = (
        CheckConstraint("counter_type IN ('COMPLAINT', 'CAS', 'EVIDENCE', 'DOCUMENT')", name="counter_type"),
        CheckConstraint("calendar_year >= 2000", name="calendar_year"),
        CheckConstraint("last_value >= 0", name="last_value"),
        Index("ix_identifier_counters_station_year", "station_id", "calendar_year"),
    )

    counter_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    station_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.stations.id", ondelete="RESTRICT"), primary_key=True)
    calendar_year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_value: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))

    station: Mapped[Station] = relationship(foreign_keys=[station_id])
