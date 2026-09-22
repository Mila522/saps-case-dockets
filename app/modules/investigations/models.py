from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, false, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.modules.access.models import User
    from app.modules.dockets.models import Docket
    from app.modules.stations.models import Officer


class CaseAssignment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "case_assignments"
    __table_args__ = (
        CheckConstraint("unassigned_at IS NULL OR unassigned_at >= assigned_at", name="assignment_dates"),
        Index("uq_case_assignments_active_docket", "docket_id", unique=True,
              postgresql_where=text("unassigned_at IS NULL")),
    )

    docket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.dockets.id", ondelete="RESTRICT"), index=True)
    investigating_officer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.officers.id", ondelete="RESTRICT"), index=True)
    assigned_by_officer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.officers.id", ondelete="RESTRICT"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    unassigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assignment_reason: Mapped[str | None] = mapped_column(Text)
    unassignment_reason: Mapped[str | None] = mapped_column(Text)

    docket: Mapped[Docket] = relationship(back_populates="assignments")
    investigating_officer: Mapped[Officer] = relationship(foreign_keys=[investigating_officer_id])
    assigned_by_officer: Mapped[Officer] = relationship(foreign_keys=[assigned_by_officer_id])


class DocketStatusHistory(UUIDPrimaryKeyMixin, Base):
    """Append-only records protected by database privileges and mutation triggers."""

    __tablename__ = "docket_status_history"
    __table_args__ = (
        CheckConstraint("from_status IN ('PENDING_APPROVAL', 'APPROVED', 'ACTIVE', 'ON_HOLD', 'CLOSED', 'ARCHIVED')", name="from_status"),
        CheckConstraint("to_status IN ('PENDING_APPROVAL', 'APPROVED', 'ACTIVE', 'ON_HOLD', 'CLOSED', 'ARCHIVED')", name="to_status"),
        CheckConstraint("from_status IS NULL OR from_status <> to_status", name="status_changed"),
        Index("uq_docket_status_history_initial", "docket_id", unique=True,
              postgresql_where=text("from_status IS NULL")),
    )

    docket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.dockets.id", ondelete="RESTRICT"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(30))
    to_status: Mapped[str] = mapped_column(String(30))
    changed_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.users.id", ondelete="RESTRICT"))
    change_reason: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    docket: Mapped[Docket] = relationship(back_populates="status_history")
    changed_by_user: Mapped[User] = relationship(foreign_keys=[changed_by_user_id])


class InvestigationNote(UUIDPrimaryKeyMixin, Base):
    """Corrections are new notes; database privileges and triggers preserve history."""

    __tablename__ = "investigation_notes"
    __table_args__ = (
        CheckConstraint("note_type IN ('GENERAL', 'PROGRESS', 'LEAD', 'INTERVIEW', 'FORENSIC', 'ADMINISTRATIVE', 'CORRECTION')", name="note_type"),
    )

    docket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.dockets.id", ondelete="RESTRICT"), index=True)
    author_officer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.officers.id", ondelete="RESTRICT"), index=True)
    note_type: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    docket: Mapped[Docket] = relationship(back_populates="investigation_notes")
    author_officer: Mapped[Officer] = relationship(foreign_keys=[author_officer_id])
