"""Live authentication/RBAC checks. Feature services must also enforce case scope."""
import uuid

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.access.models import Permission, Role, User
from app.modules.authentication.models import AuthSession
from app.modules.authentication.repository import AuthRepository
from app.modules.authentication.security import decode_access_token, utcnow

bearer = HTTPBearer(auto_error=False)


def unauthenticated():
    return HTTPException(401, 'Invalid or missing authentication', headers={'WWW-Authenticate': 'Bearer'})


def get_current_session(credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
                        db: Session = Depends(get_db)) -> AuthSession:
    if credentials is None or credentials.scheme.lower() != 'bearer' or len(credentials.credentials) > 4096:
        raise unauthenticated()
    try:
        claims = decode_access_token(credentials.credentials)
    except jwt.InvalidTokenError:
        raise unauthenticated() from None
    repo = AuthRepository(db)
    row = repo.session(uuid.UUID(claims['sid']))
    if row is None or row.user_id != uuid.UUID(claims['sub']) or row.revoked_at is not None or row.expires_at <= utcnow():
        raise unauthenticated()
    user = repo.user(row.user_id)
    if user is None or not user.is_active or not user.mfa_enabled or repo.active_mfa(user.id) is None:
        raise unauthenticated()
    return row


def get_current_user(session: AuthSession = Depends(get_current_session), db: Session = Depends(get_db)) -> User:
    repo = AuthRepository(db)
    user = repo.user(session.user_id)
    if user is None or not user.is_active or not user.mfa_enabled or repo.active_mfa(user.id) is None:
        raise unauthenticated()
    return user


def get_current_active_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_active:
        raise unauthenticated()
    return user


def get_user_roles(user: User = Depends(get_current_active_user), db: Session = Depends(get_db)) -> list[Role]:
    return AuthRepository(db).roles(user.id)


def get_user_permissions(user: User = Depends(get_current_active_user), db: Session = Depends(get_db)) -> list[Permission]:
    return AuthRepository(db).permissions(user.id)


def require_role(code: str):
    def check(user: User = Depends(get_current_active_user), roles: list[Role] = Depends(get_user_roles)) -> User:
        if code not in {role.code for role in roles}:
            raise HTTPException(403, 'Insufficient permission')
        return user
    return check


def require_permission(code: str):
    def check(user: User = Depends(get_current_active_user), permissions: list[Permission] = Depends(get_user_permissions)) -> User:
        if code not in {permission.code for permission in permissions}:
            raise HTTPException(403, 'Insufficient permission')
        return user
    return check
