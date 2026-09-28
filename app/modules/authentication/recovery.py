"""Single-use high-entropy recovery credentials; no account existence disclosure."""
from datetime import timedelta
from sqlalchemy import select, update
from fastapi import HTTPException
from app.modules.authentication import security, mail
from app.modules.authentication.models import PasswordReset, AuthSession
from app.modules.authentication.service import AuthService, transactional


class RecoveryService(AuthService):
    @transactional
    def request_reset(self, email):
        result = {'message': 'If this email belongs to a verified active account, password recovery instructions will be sent. Check your inbox and spam folder.'}
        user = self.repo.by_identifier(str(email).strip().lower())
        if not user or not user.is_active or not self.repo.email_method(user):
            return result
        now = security.utcnow()
        recent = self.db.scalar(select(PasswordReset.id).where(PasswordReset.user_id == user.id,
            PasswordReset.created_at > now - timedelta(minutes=5)).limit(1))
        if recent:
            return result
        token = security.generate_refresh_token()
        row = PasswordReset(user_id=user.id, token_hash=security.hash_refresh_token(token),
            created_at=now, expires_at=now + timedelta(minutes=15))
        self.db.add(row)
        self.db.flush()
        state, _ = mail.send_case_message(user.email, 'SAPS password recovery',
            'Paste this recovery token into the Forgot password screen:\n\n' + token +
            '\n\nIt expires in 15 minutes and works once. Ignore this message if you did not request it.', row.id)
        if state != 'ACCEPTED':
            row.used_at = now
        self.audit('auth.password_reset_requested', user)
        self.db.commit()
        return result

    @transactional
    def reset(self, token, password):
        candidate = self.db.scalar(select(PasswordReset).where(
            PasswordReset.token_hash == security.hash_refresh_token(token)))
        if candidate is None:
            raise HTTPException(400, 'Recovery token is invalid or expired')
        user = self.repo.user(candidate.user_id, lock=True)
        row = self.db.scalar(select(PasswordReset).where(PasswordReset.id == candidate.id)
            .with_for_update().execution_options(populate_existing=True))
        now = security.utcnow()
        if not user.is_active or not self.repo.email_method(user) or row.used_at or row.expires_at <= now:
            raise HTTPException(400, 'Recovery token is invalid or expired')
        user.password_hash = security.hash_password(password)
        user.failed_login_attempts = 0
        user.locked_until = None
        self.db.execute(update(PasswordReset).where(PasswordReset.user_id == user.id,
            PasswordReset.used_at.is_(None)).values(used_at=now))
        self.db.execute(update(AuthSession).where(AuthSession.user_id == user.id,
            AuthSession.revoked_at.is_(None)).values(revoked_at=now))
        self.audit('auth.password_reset_completed', user)
        self.db.commit()
        return {'message': 'Password changed. Sign in with your new password.'}
