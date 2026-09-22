from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.modules.investigations.models import CaseAssignment, DocketStatusHistory, InvestigationNote
    from app.modules.evidence.models import EvidenceItem


class Docket(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "dockets"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING_APPROVAL', 'APPROVED', 'ACTIVE', 'ON_HOLD', 'CLOSED', 'ARCHIVED')", name="status"),
        ForeignKeyConstraint(
            ["created_from_decision_id", "complaint_id"],
            ["case_mgmt.complaint_decisions.id", "case_mgmt.complaint_decisions.complaint_id"],
            ondelete="RESTRICT",
        ),
    )

    complaint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.complaints.id", ondelete="RESTRICT"), unique=True)
    cas_number: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(30), server_default=text("'PENDING_APPROVAL'"), index=True)
    created_from_decision_id: Mapped[uuid.UUID] = mapped_column(index=True)
    opened_by_officer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.officers.id", ondelete="RESTRICT"))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closure_reason: Mapped[str | None] = mapped_column(Text)

    approvals: Mapped[list[DocketApproval]] = relationship(back_populates="docket", passive_deletes="all")
    assignments: Mapped[list[CaseAssignment]] = relationship(back_populates="docket", passive_deletes="all")
    status_history: Mapped[list[DocketStatusHistory]] = relationship(back_populates="docket", passive_deletes="all")
    investigation_notes: Mapped[list[InvestigationNote]] = relationship(back_populates="docket", passive_deletes="all")
    evidence_items: Mapped[list[EvidenceItem]] = relationship(back_populates="docket", passive_deletes="all")


class DocketApproval(UUIDPrimaryKeyMixin, Base):
    """Historical entries; commander authorization belongs in the service layer."""

    __tablename__ = "docket_approvals"
    __table_args__ = (
        CheckConstraint("decision IN ('APPROVED', 'RETURNED_FOR_CORRECTION')", name="decision"),
        CheckConstraint("decision_sequence > 0", name="positive_sequence"),
        UniqueConstraint("docket_id", "decision_sequence"),
    )

    docket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.dockets.id", ondelete="RESTRICT"))
    decided_by_officer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.officers.id", ondelete="RESTRICT"))
    decision: Mapped[str] = mapped_column(String(30))
    notes: Mapped[str | None] = mapped_column(Text)
    decision_sequence: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    docket: Mapped[Docket] = relationship(back_populates="approvals")
