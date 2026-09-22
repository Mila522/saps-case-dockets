"""Small cryptographic primitives. Never log arguments or return values."""
import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import pyotp
from cryptography.fernet import Fernet
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

from app.core.config import settings

ISSUER = 'saps-case-docket'
AUDIENCE = 'saps-api'
TOTP_ISSUER = 'SAPS Case-Docket Management'
password_hasher = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = password_hasher.hash(secrets.token_urlsafe(32))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(plain_password, password_hash)
    except (UnknownHashError, ValueError):
        # Legacy unusable hashes must not authenticate or leak implementation errors.
        password_hasher.verify(plain_password, DUMMY_PASSWORD_HASH)
        return False


def create_token(user_id: uuid.UUID, session_id: uuid.UUID, purpose: str,
                 lifetime: timedelta, method_id: uuid.UUID | None = None) -> str:
    now = utcnow()
    claims: dict[str, Any] = {'sub': str(user_id), 'sid': str(session_id), 'type': purpose,
                              'iat': now, 'exp': now + lifetime, 'jti': str(uuid.uuid4()),
                              'iss': ISSUER, 'aud': AUDIENCE}
    if method_id is not None:
        claims['mid'] = str(method_id)
    return jwt.encode(claims, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm)


def create_access_token(user_id: uuid.UUID, session_id: uuid.UUID) -> str:
    return create_token(user_id, session_id, 'access', timedelta(minutes=settings.access_token_expire_minutes))


def decode_token(token: str, purpose: str) -> dict[str, Any]:
    claims = jwt.decode(token, settings.jwt_secret_key.get_secret_value(), algorithms=[settings.jwt_algorithm],
                        issuer=ISSUER, audience=AUDIENCE,
                        options={'require': ['sub', 'sid', 'type', 'iat', 'exp', 'jti', 'iss', 'aud']})
    if claims['type'] != purpose:
        raise jwt.InvalidTokenError('Incorrect token purpose')
    try:
        for key in ('sub', 'sid', 'jti'):
            uuid.UUID(claims[key])
        if purpose == 'mfa_enroll':
            uuid.UUID(claims['mid'])
        if type(claims['iat']) is not int or type(claims['exp']) is not int or claims['exp'] <= claims['iat']:
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        raise jwt.InvalidTokenError('Invalid claims') from None
    return claims


def decode_access_token(token: str) -> dict[str, Any]:
    return decode_token(token, 'access')


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def encrypt_totp_secret(secret: str) -> str:
    return Fernet(settings.mfa_encryption_key.get_secret_value().encode()).encrypt(secret.encode()).decode()


def decrypt_totp_secret(ciphertext: str) -> str:
    return Fernet(settings.mfa_encryption_key.get_secret_value().encode()).decrypt(ciphertext.encode()).decode()


def matching_totp_step(secret: str, code: str) -> int | None:
    if not re.fullmatch(r'[0-9]{6}', code):
        return None
    totp = pyotp.TOTP(secret)
    step = int(utcnow().timestamp()) // totp.interval
    for candidate in (step, step - 1, step + 1):
        if totp.verify(code, for_time=candidate * totp.interval):
            return candidate
    return None


def verify_totp_code(secret: str, code: str) -> bool:
    return matching_totp_step(secret, code) is not None
