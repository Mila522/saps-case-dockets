from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, false, func, text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.modules.access.models import User


class AuthSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = 'auth_sessions'
    __table_args__ = (
        CheckConstraint("refresh_token_hash ~ '^[0-9a-f]{64}$'", name='refresh_hash'),
        CheckConstraint('expires_at > issued_at', name='expiration'),
        CheckConstraint('replaced_by_session_id IS NULL OR replaced_by_session_id <> id', name='not_self_replacing'),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('case_mgmt.users.id', ondelete='RESTRICT'), index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    replaced_by_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('case_mgmt.auth_sessions.id', ondelete='RESTRICT'))
    created_from_ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    replacement: Mapped[AuthSession | None] = relationship(remote_side='AuthSession.id', foreign_keys=[replaced_by_session_id])


class UserEmailAuth(Base):
    """Explicit email method, bound to the address actually verified. No backfill."""
    __tablename__ = 'user_email_auth'
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('case_mgmt.users.id', ondelete='RESTRICT'), primary_key=True)
    verified_email: Mapped[str] = mapped_column(String(254))
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EmailChallenge(Base):
    __tablename__ = 'email_auth_challenges'
    __table_args__ = (
        CheckConstraint("purpose IN ('register', 'login', 'transition')", name='purpose'),
        CheckConstraint("delivery_state IN ('pending', 'accepted', 'failed')", name='delivery_state'),
        CheckConstraint("code_digest ~ '^[0-9a-f]{64}$'", name='code_digest'),
    )
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('case_mgmt.auth_sessions.id', ondelete='RESTRICT'), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('case_mgmt.users.id', ondelete='RESTRICT'), index=True)
    purpose: Mapped[str] = mapped_column(String(20))
    recipient: Mapped[str] = mapped_column(String(254))
    code_digest: Mapped[str] = mapped_column(String(64))
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    code_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivery_state: Mapped[str] = mapped_column(String(20))


class UserMfaMethod(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = 'user_mfa_methods'
    __table_args__ = (
        CheckConstraint("method_type = 'TOTP'", name='method_type'),
        CheckConstraint('NOT is_active OR verified_at IS NOT NULL', name='active_verified'),
        Index('uq_user_mfa_methods_active_totp', 'user_id', unique=True, postgresql_where=text("is_active AND method_type = 'TOTP'")),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('case_mgmt.users.id', ondelete='RESTRICT'), index=True)
    method_type: Mapped[str] = mapped_column(String(20))
    secret_encrypted: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=false())
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(foreign_keys=[user_id])
