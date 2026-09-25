"""Email challenge transactions share AuthService's user/session locking and audit."""
import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select, update

from app.core.config import settings
from app.modules.authentication import mail, schemas, security
from app.modules.authentication.models import AuthSession, EmailChallenge, UserEmailAuth, UserMfaMethod


def digest(user_id, session_id, purpose, recipient, code):
    # Domain-separated HMAC with the existing high-entropy server signing secret.
    payload = f'saps-email-code-v1\0{user_id}\0{session_id}\0{purpose}\0{recipient}\0{code}'
    return hmac.new(settings.jwt_secret_key.get_secret_value().encode(), payload.encode(), hashlib.sha256).hexdigest()


def masked(email):
    local, domain = email.rsplit('@', 1)
    return local[:1] + '***@' + domain[:1] + '***.' + domain.rsplit('.', 1)[-1]


class EmailFlow:
    def __init__(self, auth):
        self.auth, self.db = auth, auth.db

    def send(self, user, purpose):
        now = security.utcnow()
        latest = self.db.scalar(select(EmailChallenge).where(EmailChallenge.user_id == user.id)
            .order_by(EmailChallenge.attempted_at.desc()).limit(1))
        remaining = settings.email_resend_seconds - (now - latest.attempted_at).total_seconds() if latest else 0
        count = self.db.scalar(select(func.count()).select_from(EmailChallenge).where(
            EmailChallenge.user_id == user.id, EmailChallenge.attempted_at > now - timedelta(hours=1)))
        if remaining > 0 or count >= settings.email_max_sends_per_hour:
            wait = max(1, int(remaining) + 1) if count < settings.email_max_sends_per_hour else 3600
            raise HTTPException(429, 'Please wait before requesting another email code.', headers={'Retry-After': str(wait)})
        # Invalidate every older email challenge, including other login tabs.
        self.db.execute(update(AuthSession).where(AuthSession.id.in_(select(EmailChallenge.session_id)
            .where(EmailChallenge.user_id == user.id)), AuthSession.revoked_at.is_(None)).values(revoked_at=now))
        row = AuthSession(user_id=user.id, issued_at=now, expires_at=now + timedelta(minutes=30),
            refresh_token_hash=security.hash_refresh_token(security.generate_refresh_token()),
            created_from_ip=self.auth.ip, user_agent=self.auth.user_agent)
        self.db.add(row)
        self.db.flush()
        code = f'{secrets.randbelow(1_000_000):06d}'
        record = EmailChallenge(session_id=row.id, user_id=user.id, purpose=purpose, recipient=user.email,
            code_digest=digest(user.id, row.id, purpose, user.email, code), attempted_at=now,
            code_expires_at=now + timedelta(minutes=5), delivery_state='pending')
        self.db.add(record)
        self.auth.audit('auth.email_requested', user, row.id)
        self.db.flush()
        try:
            mail.send_code(user.email, code)
        except mail.MailUnavailable:
            record.delivery_state = 'failed'
            row.revoked_at = security.utcnow()
            self.auth.audit('auth.email_submission_failed', user, row.id)
            # Preserve rate limits and the pending account even for ambiguous SMTP failure.
            self.db.commit()
            raise HTTPException(503, 'Email submission could not be confirmed. Any code from this attempt is invalid. '
                'Return to sign-in and retry after one minute; verification is still required.') from None
        record.delivery_state = 'accepted'
        self.auth.audit('auth.email_submitted', user, row.id)
        return schemas.LoginResponse(status='EMAIL_CODE_REQUIRED',
            challenge_token=security.create_token(user.id, row.id, 'email_code', timedelta(minutes=30)),
            expires_in=300, masked_recipient=masked(user.email), resend_after=settings.email_resend_seconds,
            delivery_status='accepted_by_mail_server')

    def challenge(self, token):
        user, row, _ = self.auth.validate_challenge(token, 'email_code')
        record = self.db.get(EmailChallenge, row.id, populate_existing=True)
        if record is None or record.user_id != user.id or record.recipient != user.email:
            self.auth.deny('auth.mfa_failure', user, detail='Verification session is no longer valid. Return to sign-in.')
        return user, row, record

    def resend(self, token):
        user, _, record = self.challenge(token)
        result = self.send(user, record.purpose)
        self.db.commit()
        return result

    def verify(self, token, code):
        user, row, record = self.challenge(token)
        now = security.utcnow()
        failures = self.auth.repo.recent_mfa_failures(user.id, now - timedelta(minutes=settings.account_lock_minutes))
        expected = digest(user.id, row.id, record.purpose, record.recipient, code)
        if (record.delivery_state != 'accepted' or record.code_expires_at <= now
                or not hmac.compare_digest(record.code_digest, expected)
                or failures >= settings.max_failed_login_attempts):
            if failures + 1 >= settings.max_failed_login_attempts:
                user.locked_until = now + timedelta(minutes=settings.account_lock_minutes)
                self.auth.audit('auth.account_lockout', user)
            self.auth.deny('auth.mfa_failure', user, detail='Code is incorrect or expired. Try again or request a new code.')
        legacy = self.auth.repo.active_mfa(user.id)
        if user.mfa_enabled and legacy is None and self.auth.repo.email_method(user) is None:
            self.auth.deny('auth.mfa_failure', user, detail='Account verification needs administrator-assisted identity recovery.')
        if legacy and record.purpose != 'transition':
            self.auth.deny('auth.mfa_failure', user, detail='Existing account verification is required.')
        method = self.db.get(UserEmailAuth, user.id)
        if method is None:
            method = UserEmailAuth(user_id=user.id, verified_email=user.email, verified_at=now)
            self.db.add(method)
        method.verified_email, method.verified_at = user.email, now
        user.is_verified = True
        user.mfa_enabled = True  # Compatibility flag; explicit method is also mandatory.
        user.last_login_at = now
        if legacy:
            self.db.execute(update(UserMfaMethod).where(UserMfaMethod.user_id == user.id).values(is_active=False))
            # Switching methods revokes all pre-transition sessions/challenges.
            self.db.execute(update(AuthSession).where(AuthSession.user_id == user.id,
                AuthSession.revoked_at.is_(None)).values(revoked_at=now))
            self.auth.audit('auth.email_transition_completed', user)
        response = self.auth.issue_tokens(user, row)
        self.auth.audit('auth.email_verified', user, row.id)
        self.db.commit()
        return response
