from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.modules.access.models import User
    from app.modules.complainants.models import Complainant
    from app.modules.complaints.models import Complaint
    from app.modules.dockets.models import Docket
    from app.modules.stations.models import Officer


class CaseFeedback(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "case_feedback"
    __table_args__ = (
        CheckConstraint("feedback_type IN ('REGISTRATION', 'PROGRESS_UPDATE', 'REQUEST_FOR_INFORMATION', 'REFUSAL', 'CASE_CLOSURE', 'GENERAL')", name='feedback_type'),
        CheckConstraint('supersedes_feedback_id IS NULL OR supersedes_feedback_id <> id', name='not_self_superseding'),
    )

    docket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.dockets.id", ondelete="RESTRICT"), index=True)
    complainant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.complainants.id", ondelete="RESTRICT"), index=True)
    provided_by_officer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.officers.id", ondelete="RESTRICT"))
    feedback_type: Mapped[str] = mapped_column(String(40), index=True)
    subject: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    is_official: Mapped[bool] = mapped_column(Boolean, server_default=true())
    supersedes_feedback_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.case_feedback.id", ondelete="RESTRICT"))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    docket: Mapped[Docket] = relationship(foreign_keys=[docket_id])
    complainant: Mapped[Complainant] = relationship(foreign_keys=[complainant_id])
    provided_by_officer: Mapped[Officer] = relationship(foreign_keys=[provided_by_officer_id])
    supersedes_feedback: Mapped[CaseFeedback | None] = relationship(remote_side="CaseFeedback.id", foreign_keys=[supersedes_feedback_id])


class OfficialDocument(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "official_documents"
    __table_args__ = (
        CheckConstraint("document_type IN ('COMPLAINT_REGISTRATION_CONFIRMATION', 'REFUSAL_CONFIRMATION', 'DOCKET_REGISTRATION_CONFIRMATION', 'CASE_CLOSURE_CONFIRMATION')", name='document_type'),
        CheckConstraint('complaint_id IS NOT NULL OR docket_id IS NOT NULL', name='parent_required'),
        CheckConstraint('file_size_bytes > 0', name='positive_size'),
        CheckConstraint("sha256_hash ~ '^[0-9A-Fa-f]{64}$'", name='sha256_hash'),
        CheckConstraint("length(btrim(storage_key)) > 0 AND storage_key !~ '^[[:space:]]*([A-Za-z][A-Za-z0-9+.-]*:|/)' AND left(ltrim(storage_key), 1) <> chr(92)", name='storage_key'),
    )

    complaint_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.complaints.id", ondelete="RESTRICT"), index=True)
    docket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.dockets.id", ondelete="RESTRICT"), index=True)
    document_type: Mapped[str] = mapped_column(String(50), index=True)
    document_number: Mapped[str] = mapped_column(String(100), index=True, unique=True)
    storage_key: Mapped[str] = mapped_column(String(1024), unique=True)
    media_type: Mapped[str] = mapped_column(String(255))
    file_size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256_hash: Mapped[str] = mapped_column(String(64))
    generated_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.users.id", ondelete="RESTRICT"))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    complaint: Mapped[Complaint | None] = relationship(foreign_keys=[complaint_id])
    docket: Mapped[Docket | None] = relationship(foreign_keys=[docket_id])
    generated_by_user: Mapped[User] = relationship(foreign_keys=[generated_by_user_id])


class Notification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint("channel IN ('SMS', 'EMAIL', 'IN_APP')", name='channel'),
        CheckConstraint("status IN ('PENDING', 'PROCESSING', 'SENT', 'DELIVERED', 'FAILED', 'CANCELLED')", name='status'),
        CheckConstraint('(recipient_user_id IS NOT NULL) <> (recipient_complainant_id IS NOT NULL)', name='exactly_one_recipient'),
        Index("ix_notifications_created_at", "created_at"),
    )

    recipient_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.users.id", ondelete="RESTRICT"), index=True)
    recipient_complainant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.complainants.id", ondelete="RESTRICT"), index=True)
    complaint_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.complaints.id", ondelete="RESTRICT"))
    docket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.dockets.id", ondelete="RESTRICT"))
    channel: Mapped[str] = mapped_column(String(20), index=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    subject: Mapped[str | None] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    destination_encrypted: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), server_default=text("'PENDING'"), index=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    recipient_user: Mapped[User | None] = relationship(foreign_keys=[recipient_user_id])
    recipient_complainant: Mapped[Complainant | None] = relationship(foreign_keys=[recipient_complainant_id])
    complaint: Mapped[Complaint | None] = relationship(foreign_keys=[complaint_id])
    docket: Mapped[Docket | None] = relationship(foreign_keys=[docket_id])
    attempts: Mapped[list[NotificationAttempt]] = relationship(back_populates="notification", passive_deletes="all")


class NotificationAttempt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "notification_attempts"
    __table_args__ = (
        CheckConstraint("outcome IN ('SENT', 'FAILED', 'REJECTED', 'TIMEOUT')", name='outcome'),
        CheckConstraint('attempt_number > 0', name='positive_attempt'),
        UniqueConstraint("notification_id", "attempt_number"),
    )

    notification_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.notifications.id", ondelete="RESTRICT"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(100))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    outcome: Mapped[str] = mapped_column(String(20), index=True)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    response_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    notification: Mapped[Notification] = relationship(back_populates="attempts")
