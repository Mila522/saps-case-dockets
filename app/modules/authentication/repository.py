"""Database queries; transaction decisions belong to the authentication service."""
import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.access.models import Permission, Role, RolePermission, User, UserRole
from app.modules.audit.models import AuditLog
from app.modules.authentication.models import AuthSession, UserMfaMethod


class AuthRepository:
    def __init__(self, db: Session):
        self.db = db

    def user(self, user_id: uuid.UUID, *, lock: bool = False) -> User | None:
        query = select(User).where(User.id == user_id).execution_options(populate_existing=True)
        return self.db.scalar(query.with_for_update() if lock else query)

    def by_identifier(self, identifier: str) -> User | None:
        # Public usernames cannot contain @, so email/username namespaces do not overlap.
        column = User.email if '@' in identifier else User.username
        return self.db.scalar(select(User).where(func.lower(column) == identifier).with_for_update())

    def session(self, session_id: uuid.UUID, *, lock: bool = False) -> AuthSession | None:
        query = select(AuthSession).where(AuthSession.id == session_id).execution_options(populate_existing=True)
        return self.db.scalar(query.with_for_update() if lock else query)

    def by_refresh_hash(self, token_hash: str) -> AuthSession | None:
        return self.db.scalar(select(AuthSession).where(AuthSession.refresh_token_hash == token_hash))

    def active_mfa(self, user_id: uuid.UUID) -> UserMfaMethod | None:
        return self.db.scalar(select(UserMfaMethod).where(UserMfaMethod.user_id == user_id,
            UserMfaMethod.method_type == 'TOTP', UserMfaMethod.is_active.is_(True), UserMfaMethod.verified_at.is_not(None)))

    def roles(self, user_id: uuid.UUID) -> list[Role]:
        return list(self.db.scalars(select(Role).join(UserRole).where(UserRole.user_id == user_id).order_by(Role.code)))

    def permissions(self, user_id: uuid.UUID) -> list[Permission]:
        return list(self.db.scalars(select(Permission).join(RolePermission).join(UserRole, UserRole.role_id == RolePermission.role_id)
            .where(UserRole.user_id == user_id).distinct().order_by(Permission.code)))

    def recent_mfa_failures(self, user_id: uuid.UUID, since: datetime) -> int:
        return self.db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.actor_user_id == user_id,
            AuditLog.action == 'auth.mfa_failure', AuditLog.occurred_at >= since)) or 0
