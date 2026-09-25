"""Transactional authentication. Audit writes are mandatory and fail closed."""
import ipaddress
import logging
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.access.models import Role, User, UserRole
from app.modules.audit.models import AuditLog
from app.modules.authentication import schemas, security
from app.modules.authentication.models import AuthSession, UserMfaMethod
from app.modules.authentication.repository import AuthRepository
from app.modules.complainants.models import Complainant

logger = logging.getLogger(__name__)


def transactional(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except HTTPException:
            # Failure counters/audits deliberately committed by deny() survive.
            self.db.rollback()
            raise
        except (SQLAlchemyError, InvalidToken):
            self.db.rollback()
            logger.error('Authentication transaction failed; operation denied')
            raise HTTPException(503, 'Authentication service unavailable') from None
    return wrapped


class AuthService:
    def __init__(self, db: Session, ip: str | None = None, user_agent: str | None = None):
        self.db = db
        self.repo = AuthRepository(db)
        try:
            self.ip = str(ipaddress.ip_address(ip)) if ip else None
        except ValueError:
            self.ip = None
        self.user_agent = user_agent[:512] if user_agent else None

    def audit(self, action: str, user: User | None = None, session_id: uuid.UUID | None = None):
        # Explicit allowlist: no request bodies, identifiers, credentials, or UA.
        self.db.add(AuditLog(actor_type='SYSTEM' if user is None else 'USER',
            actor_user_id=user.id if user else None, action=action,
            entity_type='auth_session' if session_id else 'user',
            entity_id=session_id or (user.id if user else None), ip_address=self.ip))

    def deny(self, action: str, user: User | None = None, *, detail: str = 'Invalid credentials'):
        self.audit(action, user)
        self.db.commit()
        raise HTTPException(401, detail, headers={'WWW-Authenticate': 'Bearer'})

    def eligible(self, user: User | None) -> bool:
        return bool(user and user.is_active and (user.locked_until is None or user.locked_until <= security.utcnow()))

    def challenge(self, user: User) -> schemas.LoginResponse:
        from app.modules.authentication.email_flow import EmailFlow
        if not self.repo.active_mfa(user.id):
            email_method = self.repo.email_method(user)
            if user.mfa_enabled and email_method is None:
                raise HTTPException(403, 'Account verification needs administrator-assisted identity recovery.')
            return EmailFlow(self).send(user, 'login' if email_method else 'register')
        purpose = 'mfa_login'
        now = security.utcnow()
        row = AuthSession(user_id=user.id, issued_at=now,
            # The random preimage is discarded, never returned. A challenge JWT
            # is NOT a refresh credential; its jti is independent of this hash.
            refresh_token_hash=security.hash_refresh_token(security.generate_refresh_token()),
            expires_at=now + timedelta(minutes=settings.mfa_challenge_expire_minutes),
            created_from_ip=self.ip, user_agent=self.user_agent)
        self.db.add(row)
        self.db.flush()
        token = security.create_token(user.id, row.id, purpose, timedelta(minutes=settings.mfa_challenge_expire_minutes))
        return schemas.LoginResponse(status='TOTP_TRANSITION_REQUIRED',
            challenge_token=token, expires_in=settings.mfa_challenge_expire_minutes * 60)

    def validate_challenge(self, token: str, purpose: str):
        try:
            claims = security.decode_token(token, purpose)
        except jwt.InvalidTokenError:
            self.deny('auth.mfa_failure')
        # Global lock order: user, session, MFA method. Serializes lockouts,
        # OTP consumption, refresh rotation, and logout for the same user.
        user = self.repo.user(uuid.UUID(claims['sub']), lock=True)
        if not self.eligible(user):
            self.deny('auth.mfa_failure', user)
        row = self.repo.session(uuid.UUID(claims['sid']), lock=True)
        if row is None or row.user_id != user.id or row.revoked_at is not None or row.expires_at <= security.utcnow():
            self.deny('auth.mfa_failure', user)
        return user, row, claims

    def issue_tokens(self, user: User, previous: AuthSession) -> schemas.TokenResponse:
        now = security.utcnow()
        raw_refresh = security.generate_refresh_token()
        row = AuthSession(user_id=user.id, refresh_token_hash=security.hash_refresh_token(raw_refresh),
            issued_at=now, expires_at=now + timedelta(days=settings.refresh_token_expire_days),
            created_from_ip=self.ip, user_agent=self.user_agent)
        self.db.add(row)
        self.db.flush()
        previous.revoked_at = now
        previous.last_used_at = now
        previous.replaced_by_session_id = row.id
        return schemas.TokenResponse(access_token=security.create_access_token(user.id, row.id),
            refresh_token=raw_refresh, expires_in=settings.access_token_expire_minutes * 60)

    @transactional
    def register(self, data: schemas.RegistrationRequest) -> schemas.LoginResponse:
        username, email = data.username.strip().lower(), str(data.email).strip().lower()
        exists = self.db.scalar(select(User.id).where(or_(func.lower(User.username) == username, func.lower(User.email) == email)))
        if exists:
            raise HTTPException(409, 'Username or email is unavailable')
        role = self.db.scalar(select(Role).where(Role.code == 'COMPLAINANT'))
        if role is None:
            raise HTTPException(503, 'Authentication service unavailable')
        user = User(username=username, email=email, password_hash=security.hash_password(data.password.get_secret_value()), phone_number=data.phone_number)
        self.db.add(user)
        try:
            self.db.flush()
        except IntegrityError as exc:
            if getattr(getattr(exc.orig, 'diag', None), 'constraint_name', None) in {
                'ix_users_username', 'ix_users_email', 'uq_users_username_lower', 'uq_users_email_lower'}:
                self.db.rollback()
                raise HTTPException(409, 'Username or email is unavailable') from None
            raise
        self.db.add(Complainant(user_id=user.id, first_name=data.first_name, last_name=data.last_name,
            email=email, phone_number=data.phone_number, preferred_contact_method=data.preferred_contact_method))
        self.db.add(UserRole(user_id=user.id, role_id=role.id))
        self.audit('auth.registration', user)
        response = self.challenge(user)
        self.db.commit()
        return response

    @transactional
    def login(self, data: schemas.LoginRequest) -> schemas.LoginResponse:
        user = self.repo.by_identifier(data.username.strip().lower())
        password = data.password.get_secret_value()
        if not self.eligible(user):
            security.verify_password(password, security.DUMMY_PASSWORD_HASH)
            self.deny('auth.login_failure', user)
        now = security.utcnow()
        if user.locked_until is not None and user.locked_until <= now:
            user.locked_until = None
            user.failed_login_attempts = 0
        if not security.verify_password(password, user.password_hash):
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= settings.max_failed_login_attempts:
                user.locked_until = now + timedelta(minutes=settings.account_lock_minutes)
                self.audit('auth.account_lockout', user)
            self.deny('auth.login_failure', user)
        user.failed_login_attempts = 0
        user.locked_until = None
        self.audit('auth.password_verified', user)
        response = self.challenge(user)
        self.db.commit()
        return response

    @transactional
    def setup_mfa(self, token):
        raise HTTPException(410, 'Authenticator enrollment has been replaced by email verification. Return to sign-in.')

    @transactional
    def verify_setup(self, token, code):
        raise HTTPException(410, 'Authenticator enrollment has been replaced by email verification. Return to sign-in.')

    @transactional
    def verify_email(self, token, code):
        from app.modules.authentication.email_flow import EmailFlow
        return EmailFlow(self).verify(token, code)

    @transactional
    def resend_email(self, token):
        from app.modules.authentication.email_flow import EmailFlow
        return EmailFlow(self).resend(token)

    def verify_code(self, user: User, method: UserMfaMethod, code: str):
        now = security.utcnow()
        failures = self.repo.recent_mfa_failures(user.id, now - timedelta(minutes=settings.account_lock_minutes))
        step = security.matching_totp_step(security.decrypt_totp_secret(method.secret_encrypted), code)
        previous_step = int(method.last_used_at.timestamp()) // 30 if method.last_used_at else -1
        if step is None or step <= previous_step or failures >= settings.max_failed_login_attempts:
            # Persisted user-wide audit count prevents bypass by requesting a new
            # challenge or successfully verifying the password between guesses.
            if failures + 1 >= settings.max_failed_login_attempts:
                user.locked_until = now + timedelta(minutes=settings.account_lock_minutes)
                self.audit('auth.account_lockout', user)
            self.deny('auth.mfa_failure', user)
        method.last_used_at = datetime.fromtimestamp(step * 30, timezone.utc)

    @transactional
    def verify_login(self, token: str, code: str) -> schemas.LoginResponse:
        from app.modules.authentication.email_flow import EmailFlow
        user, row, _ = self.validate_challenge(token, 'mfa_login')
        method = self.repo.active_mfa(user.id)
        if method is None or not user.mfa_enabled:
            self.deny('auth.mfa_failure', user)
        self.verify_code(user, method, code)
        response = EmailFlow(self).send(user, 'transition')
        row.revoked_at = security.utcnow()
        self.audit('auth.email_transition_authorized', user, row.id)
        self.db.commit()
        return response

    def refresh_session(self, token: str):
        candidate = self.repo.by_refresh_hash(security.hash_refresh_token(token))
        if candidate is None:
            self.deny('auth.session_rejected')
        user = self.repo.user(candidate.user_id, lock=True)
        row = self.repo.session(candidate.id, lock=True)
        return user, row

    def revoke_session_chain(self, user: User, row: AuthSession):
        # Revoke this rotation family, not unrelated logins on other devices.
        chain = select(AuthSession.id, AuthSession.replaced_by_session_id).where(
            AuthSession.id == row.id, AuthSession.user_id == user.id).cte('session_chain', recursive=True)
        chain = chain.union(select(AuthSession.id, AuthSession.replaced_by_session_id).join(
            chain, AuthSession.id == chain.c.replaced_by_session_id).where(AuthSession.user_id == user.id))
        self.db.execute(update(AuthSession).where(AuthSession.id.in_(select(chain.c.id)),
            AuthSession.revoked_at.is_(None)).values(revoked_at=security.utcnow()))

    @transactional
    def refresh(self, token: str) -> schemas.TokenResponse:
        user, row = self.refresh_session(token)
        if row.expires_at <= security.utcnow():
            self.deny('auth.session_rejected', user)
        if row.revoked_at is not None:
            if row.replaced_by_session_id is not None:
                # A reused rotated credential may indicate theft.
                self.revoke_session_chain(user, row)
                self.audit('auth.refresh_reuse', user)
            self.deny('auth.session_rejected', user)
        if not self.eligible(user) or not user.mfa_enabled or not self.repo.authenticated_method(user):
            self.deny('auth.session_rejected', user)
        response = self.issue_tokens(user, row)
        self.audit('auth.token_refresh', user, row.id)
        self.db.commit()
        return response

    @transactional
    def logout(self, token: str):
        user, row = self.refresh_session(token)
        if row.expires_at <= security.utcnow():
            self.deny('auth.session_rejected', user)
        # Repeated logout is safe; a rotated credential revokes its descendants.
        self.revoke_session_chain(user, row)
        self.audit('auth.logout', user, row.id)
        self.db.commit()
