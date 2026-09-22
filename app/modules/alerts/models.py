from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.modules.access.models import User
    from app.modules.complaints.models import Complaint
    from app.modules.dockets.models import Docket
    from app.modules.stations.models import Station


class Alert(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint("alert_type IN ('NON_COMPLIANT_REFUSAL', 'OVERDUE_DOCKET', 'CASE_INACTIVITY', 'UNACKNOWLEDGED_ESCALATION', 'EVIDENCE_CUSTODY_EXCEPTION', 'SYSTEM_COMPLIANCE')", name='alert_type'),
        CheckConstraint("severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')", name='severity'),
        CheckConstraint("status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED', 'DISMISSED')", name='status'),
        CheckConstraint("alert_type = 'SYSTEM_COMPLIANCE' OR complaint_id IS NOT NULL OR docket_id IS NOT NULL", name='parent_required'),
        CheckConstraint('acknowledged_at IS NULL OR acknowledged_by_user_id IS NOT NULL', name='acknowledgment_actor'),
        CheckConstraint('resolved_at IS NULL OR resolved_by_user_id IS NOT NULL', name='resolution_actor'),
    )

    station_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.stations.id", ondelete="RESTRICT"), index=True)
    complaint_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.complaints.id", ondelete="RESTRICT"))
    docket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.dockets.id", ondelete="RESTRICT"))
    alert_type: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(20), server_default=text("'OPEN'"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    assigned_to_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.users.id", ondelete="RESTRICT"))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    acknowledged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.users.id", ondelete="RESTRICT"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.users.id", ondelete="RESTRICT"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_notes: Mapped[str | None] = mapped_column(Text)

    station: Mapped[Station] = relationship(foreign_keys=[station_id])
    complaint: Mapped[Complaint | None] = relationship(foreign_keys=[complaint_id])
    docket: Mapped[Docket | None] = relationship(foreign_keys=[docket_id])
    assigned_to_user: Mapped[User | None] = relationship(foreign_keys=[assigned_to_user_id])
    acknowledged_by_user: Mapped[User | None] = relationship(foreign_keys=[acknowledged_by_user_id])
    resolved_by_user: Mapped[User | None] = relationship(foreign_keys=[resolved_by_user_id])
