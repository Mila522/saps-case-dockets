from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.modules.access.models import User
    from app.modules.dockets.models import Docket
    from app.modules.stations.models import Officer


class EvidenceItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evidence_items"
    __table_args__ = (
        CheckConstraint("evidence_type IN ('DOCUMENT', 'IMAGE', 'VIDEO', 'AUDIO', 'PHYSICAL_OBJECT', 'DIGITAL_DEVICE', 'OTHER')", name="evidence_type"),
        CheckConstraint("status IN ('REGISTERED', 'IN_CUSTODY', 'TRANSFERRED', 'UNDER_ANALYSIS', 'RELEASED', 'DISPOSED')", name="status"),
    )

    docket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.dockets.id", ondelete="RESTRICT"), index=True)
    evidence_reference: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    evidence_type: Mapped[str] = mapped_column(String(30), index=True)
    is_digital: Mapped[bool] = mapped_column(Boolean)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_by_officer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.officers.id", ondelete="RESTRICT"))
    collection_location: Mapped[str | None] = mapped_column(Text)
    registered_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.users.id", ondelete="RESTRICT"))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    status: Mapped[str] = mapped_column(String(30), server_default=text("'REGISTERED'"), index=True)
    current_custodian_officer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.officers.id", ondelete="RESTRICT"))
    current_storage_location: Mapped[str | None] = mapped_column(String(255))

    docket: Mapped[Docket] = relationship(back_populates="evidence_items")
    collected_by_officer: Mapped[Officer | None] = relationship(foreign_keys=[collected_by_officer_id])
    registered_by_user: Mapped[User] = relationship(foreign_keys=[registered_by_user_id])
    current_custodian_officer: Mapped[Officer | None] = relationship(foreign_keys=[current_custodian_officer_id])
    files: Mapped[list[EvidenceFile]] = relationship(back_populates="evidence_item", passive_deletes="all")
    custody_events: Mapped[list[EvidenceCustodyEvent]] = relationship(back_populates="evidence_item", passive_deletes="all")


class EvidenceFile(UUIDPrimaryKeyMixin, Base):
    """Protected-object metadata preserved by append-only database protections."""

    __tablename__ = "evidence_files"
    __table_args__ = (
        UniqueConstraint("evidence_item_id", "file_version"),
        CheckConstraint("file_version > 0", name="positive_version"),
        CheckConstraint("file_size_bytes > 0", name="positive_size"),
        CheckConstraint("sha256_hash ~ '^[0-9A-Fa-f]{64}$'", name="sha256_hash"),
        # Opaque object keys, not URLs, absolute paths, or whitespace-only values.
        # Storage access policy must still be enforced by the future storage service.
        CheckConstraint("length(btrim(storage_key)) > 0 AND storage_key !~ '^[[:space:]]*([A-Za-z][A-Za-z0-9+.-]*:|/)' AND left(ltrim(storage_key), 1) <> chr(92)", name="storage_key"),
    )

    evidence_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.evidence_items.id", ondelete="RESTRICT"), index=True)
    file_version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(1024), unique=True)
    media_type: Mapped[str] = mapped_column(String(255))
    file_size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256_hash: Mapped[str] = mapped_column(String(64), index=True)
    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.users.id", ondelete="RESTRICT"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    evidence_item: Mapped[EvidenceItem] = relationship(back_populates="files")
    uploaded_by_user: Mapped[User] = relationship(foreign_keys=[uploaded_by_user_id])


class EvidenceCustodyEvent(UUIDPrimaryKeyMixin, Base):
    """Append-only records protected by database privileges and mutation triggers."""

    __tablename__ = "evidence_custody_events"
    __table_args__ = (
        CheckConstraint("event_type IN ('REGISTERED', 'COLLECTED', 'RECEIVED', 'TRANSFERRED', 'ACCESSED', 'ANALYSIS_STARTED', 'ANALYSIS_COMPLETED', 'RELEASED', 'DISPOSED')", name="event_type"),
    )

    evidence_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.evidence_items.id", ondelete="RESTRICT"), index=True)
    event_type: Mapped[str] = mapped_column(String(30), index=True)
    performed_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.users.id", ondelete="RESTRICT"), index=True)
    from_custodian_officer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.officers.id", ondelete="RESTRICT"))
    to_custodian_officer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.officers.id", ondelete="RESTRICT"))
    from_location: Mapped[str | None] = mapped_column(String(255))
    to_location: Mapped[str | None] = mapped_column(String(255))
    event_notes: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    evidence_item: Mapped[EvidenceItem] = relationship(back_populates="custody_events")
    performed_by_user: Mapped[User] = relationship(foreign_keys=[performed_by_user_id])
    from_custodian_officer: Mapped[Officer | None] = relationship(foreign_keys=[from_custodian_officer_id])
    to_custodian_officer: Mapped[Officer | None] = relationship(foreign_keys=[to_custodian_officer_id])
