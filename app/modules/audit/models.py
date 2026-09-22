from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.modules.access.models import User
    from app.modules.stations.models import Station


class AuditLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint("actor_type IN ('USER', 'SYSTEM', 'DATABASE')", name='actor_type'),
        CheckConstraint("actor_type <> 'USER' OR actor_user_id IS NOT NULL", name='user_actor_required'),
        Index("ix_audit_logs_entity_type_entity_id", "entity_type", "entity_id"),
    )

    actor_type: Mapped[str] = mapped_column(String(20))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.users.id", ondelete="RESTRICT"), index=True)
    database_user: Mapped[str | None] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[uuid.UUID | None] = mapped_column()
    station_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.stations.id", ondelete="RESTRICT"), index=True)
    request_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    old_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    actor_user: Mapped[User | None] = relationship(foreign_keys=[actor_user_id])
    station: Mapped[Station | None] = relationship(foreign_keys=[station_id])
