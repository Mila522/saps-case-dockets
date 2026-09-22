from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, false, func, text, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.modules.stations.models import Officer
    from app.modules.complainants.models import Complainant


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        Index('uq_users_username_lower', func.lower(text('username')), unique=True),
        Index('uq_users_email_lower', func.lower(text('email')), unique=True),
    )

    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    phone_number: Mapped[str | None] = mapped_column(String(30))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    is_verified: Mapped[bool] = mapped_column(Boolean, server_default=false())
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, server_default=false())
    failed_login_attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    role_assignments: Mapped[list[UserRole]] = relationship(
        back_populates="user", foreign_keys="UserRole.user_id", passive_deletes="all"
    )
    officer: Mapped[Officer | None] = relationship(back_populates="user", passive_deletes="all")
    complainant: Mapped[Complainant | None] = relationship(back_populates="user", passive_deletes="all")


class Role(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    is_system_role: Mapped[bool] = mapped_column(Boolean, server_default=true())

    user_assignments: Mapped[list[UserRole]] = relationship(back_populates="role", passive_deletes="all")
    permission_grants: Mapped[list[RolePermission]] = relationship(back_populates="role", passive_deletes="all")


class Permission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text)

    role_grants: Mapped[list[RolePermission]] = relationship(back_populates="permission", passive_deletes="all")


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.roles.id", ondelete="CASCADE"), primary_key=True)
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_mgmt.users.id", ondelete="SET NULL"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="role_assignments", foreign_keys=[user_id])
    role: Mapped[Role] = relationship(back_populates="user_assignments")
    assigned_by: Mapped[User | None] = relationship(foreign_keys=[assigned_by_user_id])


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_mgmt.permissions.id", ondelete="CASCADE"), primary_key=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    role: Mapped[Role] = relationship(back_populates="permission_grants")
    permission: Mapped[Permission] = relationship(back_populates="role_grants")
