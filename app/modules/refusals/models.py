from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint, false, func, text, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class RefusalReason(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "refusal_reasons"

    code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    is_non_compliant: Mapped[bool] = mapped_column(Boolean, server_default=false())
    requires_escalation: Mapped[bool] = mapped_column(Boolean, server_default=false())
    requires_officer_notes: Mapped[bool] = mapped_column(Boolean, server_default=false())
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())


class ComplaintDecision(UUIDPrimaryKeyMixin, Base):
    """Historical entries; application credentials may only insert and select."""

    __tablename__ = "complaint_decisions"
    __table_args__ = (
        CheckConstraint("decision IN ('ACCEPTED', 'REFUSED')", name="decision"),
        CheckConstraint("(decision = 'ACCEPTED' AND refusal_reason_id IS NULL) OR (decision = 'REFUSED' AND refusal_reason_id IS NOT NULL)", name="refusal_reason"),
        CheckConstraint("decision_sequence > 0", name="positive_sequence"),
        UniqueConstraint("complaint_id", "decision_sequence"),
        UniqueConstraint("id", "complaint_id", name="uq_complaint_decisions_id_complaint"),
    )

    complaint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.complaints.id", ondelete="RESTRICT"))
    decision: Mapped[str] = mapped_column(String(20))
    decided_by_officer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.officers.id", ondelete="RESTRICT"))
    refusal_reason_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.refusal_reasons.id", ondelete="RESTRICT"), index=True)
    officer_notes: Mapped[str | None] = mapped_column(Text)
    decision_sequence: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    refusal_reason: Mapped[RefusalReason | None] = relationship()


class RefusalEscalation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "refusal_escalations"
    __table_args__ = (
        CheckConstraint("target IN ('STATION_COMMANDER', 'NCC')", name="target"),
        CheckConstraint("status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')", name="status"),
        UniqueConstraint("complaint_decision_id", "target"),
        ForeignKeyConstraint(
            ["complaint_decision_id", "complaint_id"],
            ["case_mgmt.complaint_decisions.id", "case_mgmt.complaint_decisions.complaint_id"],
            ondelete="RESTRICT",
        ),
    )

    complaint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.complaints.id", ondelete="RESTRICT"), index=True)
    complaint_decision_id: Mapped[uuid.UUID] = mapped_column()
    target: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'OPEN'"), index=True)
    escalated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    acknowledged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.users.id", ondelete="RESTRICT"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.users.id", ondelete="RESTRICT"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_notes: Mapped[str | None] = mapped_column(Text)

    complaint_decision: Mapped[ComplaintDecision] = relationship()
