from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Complaint(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "complaints"
    __table_args__ = (
        CheckConstraint("channel IN ('ONLINE', 'IN_STATION')", name="channel"),
        CheckConstraint("status IN ('SUBMITTED', 'UNDER_REVIEW', 'ACCEPTED', 'REFUSED', 'ESCALATED', 'DOCKET_CREATED')", name="status"),
        CheckConstraint("channel <> 'IN_STATION' OR registered_by_officer_id IS NOT NULL", name="in_station_officer"),
    )

    reference_number: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    complainant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.complainants.id", ondelete="RESTRICT"), index=True)
    station_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.stations.id", ondelete="RESTRICT"), index=True)
    registered_by_officer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.officers.id", ondelete="RESTRICT"))
    channel: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), server_default=text("'SUBMITTED'"), index=True)
    crime_category: Mapped[str] = mapped_column(String(150))
    incident_description: Mapped[str] = mapped_column(Text)
    incident_occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    incident_location: Mapped[str] = mapped_column(String(255))
    incident_city: Mapped[str | None] = mapped_column(String(150))
    incident_province: Mapped[str] = mapped_column(String(100))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    review_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    statements: Mapped[list[ComplaintStatement]] = relationship(back_populates="complaint", passive_deletes="all")
    witnesses: Mapped[list[Witness]] = relationship(back_populates="complaint", passive_deletes="all")


class ComplaintStatement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "complaint_statements"
    __table_args__ = (
        UniqueConstraint("complaint_id", "statement_version"),
        CheckConstraint("statement_version > 0", name="positive_version"),
        Index("uq_complaint_statements_current", "complaint_id", unique=True, postgresql_where=text("is_current")),
    )

    complaint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.complaints.id", ondelete="RESTRICT"))
    statement_text: Mapped[str] = mapped_column(Text)
    recorded_by_officer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.officers.id", ondelete="RESTRICT"))
    statement_version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    is_current: Mapped[bool] = mapped_column(Boolean, server_default=true())
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    complaint: Mapped[Complaint] = relationship(back_populates="statements")


class Witness(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "witnesses"

    complaint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.complaints.id", ondelete="RESTRICT"), index=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    phone_number: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(254))
    identity_number_encrypted: Mapped[str | None] = mapped_column(Text)
    identity_number_hash: Mapped[str | None] = mapped_column(String(128))
    address: Mapped[str | None] = mapped_column(Text)

    complaint: Mapped[Complaint] = relationship(back_populates="witnesses")
    statements: Mapped[list[WitnessStatement]] = relationship(back_populates="witness", passive_deletes="all")


class WitnessStatement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "witness_statements"
    __table_args__ = (
        UniqueConstraint("witness_id", "statement_version"),
        CheckConstraint("statement_version > 0", name="positive_version"),
        Index("uq_witness_statements_current", "witness_id", unique=True, postgresql_where=text("is_current")),
    )

    witness_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.witnesses.id", ondelete="RESTRICT"))
    statement_text: Mapped[str] = mapped_column(Text)
    recorded_by_officer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.officers.id", ondelete="RESTRICT"))
    statement_version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    is_current: Mapped[bool] = mapped_column(Boolean, server_default=true())
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    witness: Mapped[Witness] = relationship(back_populates="statements")
