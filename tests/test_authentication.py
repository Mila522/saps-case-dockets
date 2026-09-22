import json
import uuid
from datetime import timedelta

import jwt
import pyotp
import pytest
from cryptography.fernet import Fernet
from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.db.session import engine, get_db
from app.main import app
from app.modules.access.models import Permission, Role, RolePermission, User, UserRole
from app.modules.audit.models import AuditLog
from app.modules.authentication import schemas, security
from app.modules.authentication.dependencies import require_permission, require_role
from app.modules.authentication.models import AuthSession, UserMfaMethod
from app.modules.authentication.service import AuthService
from app.modules.complainants.models import Complainant


@pytest.fixture
def auth_context():
    connection = engine.connect()
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode='create_savepoint', expire_on_commit=False)
    def override_db():
        yield db
    old_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_db
    original_routes = list(app.router.routes)
    app.add_api_route('/_test/allowed', lambda user=Depends(require_permission('complaint.submit')): {'allowed': True})
    app.add_api_route('/_test/denied', lambda user=Depends(require_permission('complaint.register')): {'allowed': True})
    app.add_api_route('/_test/role', lambda user=Depends(require_role('COMPLAINANT')): {'allowed': True})
    try:
        with TestClient(app) as client:
            yield client, db
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)
        app.router.routes[:] = original_routes
        app.openapi_schema = None
        db.close()
        outer.rollback()
        connection.close()


def registration(**changes):
    unique = uuid.uuid4().hex
    return {'username': 'test_' + unique, 'email': unique + '@example.com', 'password': 'Testing-Password12!',
            'first_name': 'Test', 'last_name': 'Complainant', 'phone_number': '0000000000'} | changes


def enroll(client, data=None):
    data = data or registration()
    response = client.post('/api/v1/auth/register', json=data)
    assert response.status_code == 201
    login = response.json()
    assert login['status'] == 'MFA_SETUP_REQUIRED' and 'access_token' not in login
    setup = client.post('/api/v1/auth/mfa/setup', json={'challenge_token': login['challenge_token']})
    assert setup.status_code == 200
    setup = setup.json()
    code = pyotp.TOTP(setup['manual_entry_secret']).at(security.utcnow())
    verified = client.post('/api/v1/auth/mfa/verify-setup', json={'setup_token': setup['setup_token'], 'code': code})
    assert verified.status_code == 200
    return data, setup, verified.json()


def authorization(tokens):
    return {'Authorization': 'Bearer ' + tokens['access_token']}


def test_security_primitives():
    password = 'Strong-Password123!'
    hashed = security.hash_password(password)
    assert hashed.startswith('$argon2id$') and hashed != password
    assert security.verify_password(password, hashed)
    assert not security.verify_password('wrong', hashed)
    assert not security.verify_password(password, '!unusable')
    first, second = security.generate_refresh_token(), security.generate_refresh_token()
    assert first != second and len(first) >= 64
    assert len(security.hash_refresh_token(first)) == 64
    secret = security.generate_totp_secret()
    encrypted = security.encrypt_totp_secret(secret)
    assert encrypted != secret and security.decrypt_totp_secret(encrypted) == secret
    assert security.verify_totp_code(secret, pyotp.TOTP(secret).at(security.utcnow()))
    assert not security.verify_totp_code(secret, 'abcdef')


def test_jwt_claims_and_rejection():
    user, session = uuid.uuid4(), uuid.uuid4()
    token = security.create_access_token(user, session)
    claims = security.decode_access_token(token)
    assert claims['sub'] == str(user) and claims['sid'] == str(session) and claims['type'] == 'access'
    assert {'sub', 'sid', 'type', 'iat', 'exp', 'jti'} <= set(claims)
    assert not {'roles', 'permissions'} & set(claims)
    for wrong in (
        security.create_token(user, session, 'mfa_login', timedelta(minutes=5)),
        security.create_token(user, session, 'access', timedelta(seconds=-1)),
        token + 'corrupt',
        jwt.encode(claims, 'untrusted-key-' * 5, algorithm='HS256'),
    ):
        with pytest.raises(jwt.InvalidTokenError):
            security.decode_access_token(wrong)


@pytest.mark.parametrize('password', ['Short1!', 'lowercaseonly123!', 'UPPERCASEONLY123!', 'NoNumbersHere!', 'NoSpecialChar123', ' LeadingPass123!', 'TrailingPass123! '])
def test_weak_passwords_rejected(password):
    with pytest.raises(ValidationError):
        schemas.RegistrationRequest(**registration(password=password))


def test_secure_config_validation():
    for invalid in ({'jwt_secret_key': 'short'}, {'jwt_secret_key': 'replace-me-' * 8},
                    {'mfa_encryption_key': 'invalid'}, {'access_token_expire_minutes': 0},
                    {'mfa_encryption_key': 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='},
                    {'refresh_token_expire_days': -1}, {'jwt_algorithm': 'none'}):
        with pytest.raises(ValidationError):
            Settings(**invalid)


def test_registration_normalization_and_public_role_restriction(auth_context):
    client, db = auth_context
    data = registration()
    data['username'] = data['username'].upper()
    data['email'] = data['email'].upper()
    response = client.post('/api/v1/auth/register', json=data)
    assert response.status_code == 201
    user = db.scalar(select(User).where(User.username == data['username'].lower()))
    assert user.email == data['email'].lower() and user.password_hash.startswith('$argon2id$')
    assert db.scalar(select(Complainant).where(Complainant.user_id == user.id)) is not None
    roles = db.scalars(select(Role.code).join(UserRole).where(UserRole.user_id == user.id)).all()
    assert roles == ['COMPLAINANT']
    for other in (data | {'username': data['username'].lower()}, data | {'username': 'other_' + uuid.uuid4().hex}):
        assert client.post('/api/v1/auth/register', json=other).status_code == 409
    for field in ('roles', 'role', 'is_active', 'mfa_enabled'):
        assert client.post('/api/v1/auth/register', json=registration(**{field: 'SYSTEM_ADMINISTRATOR'})).status_code == 422
    assert client.get('/api/v1/auth/me', headers={'Authorization': 'Bearer ' + response.json()['challenge_token']}).status_code == 401


def test_validation_and_responses_do_not_echo_credentials(auth_context):
    client, _ = auth_context
    response = client.post('/api/v1/auth/register', json=registration(password='Sensitive1!'))
    assert response.status_code == 422 and 'Sensitive1!' not in response.text
    assert 'input' not in response.text
    assert response.headers['cache-control'] == 'no-store'
    assert client.get('/api/v1/auth/me').status_code == 401


def test_mfa_setup_secret_storage_and_single_use(auth_context):
    client, db = auth_context
    response = client.post('/api/v1/auth/register', json=registration()).json()
    setup = client.post('/api/v1/auth/mfa/setup', json={'challenge_token': response['challenge_token']}).json()
    assert setup['provisioning_uri'].startswith('otpauth://totp/')
    assert client.post('/api/v1/auth/mfa/setup', json={'challenge_token': response['challenge_token']}).status_code == 401
    method = db.scalar(select(UserMfaMethod).where(UserMfaMethod.secret_encrypted.is_not(None)).order_by(UserMfaMethod.created_at.desc()))
    assert method.secret_encrypted != setup['manual_entry_secret'] and not method.is_active
    assert client.post('/api/v1/auth/mfa/verify-setup', json={'setup_token': setup['setup_token'], 'code': 'abcdef'}).status_code == 401
    payload = {'setup_token': setup['setup_token'], 'code': pyotp.TOTP(setup['manual_entry_secret']).at(security.utcnow())}
    assert client.post('/api/v1/auth/mfa/verify-setup', json=payload).status_code == 200
    assert client.post('/api/v1/auth/mfa/verify-setup', json=payload).status_code == 401


def test_login_totp_replay_and_current_user(auth_context):
    client, db = auth_context
    data, setup, tokens = enroll(client)
    me = client.get('/api/v1/auth/me', headers=authorization(tokens))
    assert me.status_code == 200
    user = me.json()
    assert user['mfa_enabled'] and not user['is_verified']  # TOTP does not verify email ownership.
    assert user['complainant_id'] and user['officer_id'] is None
    assert not {'password_hash', 'failed_login_attempts', 'locked_until', 'secret_encrypted', 'refresh_token_hash'} & set(user)
    login = client.post('/api/v1/auth/login', json={'username': data['email'].upper(), 'password': data['password']})
    assert login.status_code == 200 and login.json()['status'] == 'MFA_REQUIRED'
    assert 'access_token' not in login.json()
    payload = {'challenge_token': login.json()['challenge_token'],
               'code': pyotp.TOTP(setup['manual_entry_secret']).at(security.utcnow() + timedelta(seconds=30))}
    verified = client.post('/api/v1/auth/mfa/verify', json=payload)
    assert verified.status_code == 200
    assert client.post('/api/v1/auth/mfa/verify', json=payload).status_code == 401
    next_login = client.post('/api/v1/auth/login', json={'username': data['username'], 'password': data['password']}).json()
    payload['challenge_token'] = next_login['challenge_token']
    assert client.post('/api/v1/auth/mfa/verify', json=payload).status_code == 401


def test_password_lockout_and_generic_failures(auth_context):
    client, db = auth_context
    data = registration()
    assert client.post('/api/v1/auth/register', json=data).status_code == 201
    unknown = client.post('/api/v1/auth/login', json={'username': 'missing_' + uuid.uuid4().hex, 'password': 'Wrong-password12!'})
    for _ in range(settings.max_failed_login_attempts):
        failed = client.post('/api/v1/auth/login', json={'username': data['username'], 'password': 'Wrong-password12!'})
        assert failed.status_code == 401 and failed.json() == unknown.json()
    user = db.scalar(select(User).where(User.username == data['username']))
    assert user.failed_login_attempts == settings.max_failed_login_attempts and user.locked_until > security.utcnow()
    assert client.post('/api/v1/auth/login', json={'username': data['username'], 'password': data['password']}).status_code == 401
    user.locked_until = security.utcnow() - timedelta(seconds=1)
    db.commit()
    good = client.post('/api/v1/auth/login', json={'username': data['username'], 'password': data['password']})
    assert good.status_code == 200 and good.json()['status'] == 'MFA_SETUP_REQUIRED'
    assert user.failed_login_attempts == 0


def test_mfa_lockout_cannot_be_reset_by_password_success(auth_context):
    client, db = auth_context
    data, setup, _ = enroll(client)
    for _ in range(settings.max_failed_login_attempts):
        login = client.post('/api/v1/auth/login', json={'username': data['username'], 'password': data['password']})
        assert login.status_code == 200
        assert client.post('/api/v1/auth/mfa/verify', json={'challenge_token': login.json()['challenge_token'], 'code': 'abcdef'}).status_code == 401
    assert client.post('/api/v1/auth/login', json={'username': data['username'], 'password': data['password']}).status_code == 401


def test_refresh_rotation_and_reuse_revokes_successor(auth_context):
    client, db = auth_context
    _, _, tokens = enroll(client)
    old_hash = security.hash_refresh_token(tokens['refresh_token'])
    row = db.scalar(select(AuthSession).where(AuthSession.refresh_token_hash == old_hash))
    assert row is not None and row.refresh_token_hash != tokens['refresh_token']
    independent = AuthSession(user_id=row.user_id, refresh_token_hash=security.hash_refresh_token(security.generate_refresh_token()),
        issued_at=security.utcnow(), expires_at=security.utcnow() + timedelta(days=1))
    db.add(independent)
    db.commit()
    independent_access = security.create_access_token(row.user_id, independent.id)
    rotated = client.post('/api/v1/auth/refresh', json={'refresh_token': tokens['refresh_token']})
    assert rotated.status_code == 200
    new = rotated.json()
    assert new['refresh_token'] != tokens['refresh_token']
    assert client.get('/api/v1/auth/me', headers=authorization(tokens)).status_code == 401
    assert client.get('/api/v1/auth/me', headers=authorization(new)).status_code == 200
    assert client.post('/api/v1/auth/refresh', json={'refresh_token': tokens['refresh_token']}).status_code == 401
    assert client.get('/api/v1/auth/me', headers=authorization(new)).status_code == 401
    assert row.revoked_at and row.replaced_by_session_id
    assert client.get('/api/v1/auth/me', headers={'Authorization': 'Bearer ' + independent_access}).status_code == 200


def test_logout_and_expired_session(auth_context):
    client, db = auth_context
    _, _, tokens = enroll(client)
    assert client.post('/api/v1/auth/logout', json={'refresh_token': tokens['refresh_token']}).status_code == 204
    assert client.get('/api/v1/auth/me', headers=authorization(tokens)).status_code == 401
    assert client.post('/api/v1/auth/refresh', json={'refresh_token': tokens['refresh_token']}).status_code == 401
    _, _, second = enroll(client)
    row = db.scalar(select(AuthSession).where(AuthSession.refresh_token_hash == security.hash_refresh_token(second['refresh_token'])))
    row.issued_at = security.utcnow() - timedelta(days=2)
    row.expires_at = security.utcnow() - timedelta(days=1)
    db.commit()
    assert client.post('/api/v1/auth/refresh', json={'refresh_token': second['refresh_token']}).status_code == 401


def test_permissions_live_revocation_and_inactive_user(auth_context):
    client, db = auth_context
    data, _, tokens = enroll(client)
    headers = authorization(tokens)
    assert client.get('/_test/allowed', headers=headers).status_code == 200
    assert client.get('/_test/role', headers=headers).status_code == 200
    assert client.get('/_test/denied', headers=headers).status_code == 403
    user = db.scalar(select(User).where(User.username == data['username']))
    db.execute(UserRole.__table__.delete().where(UserRole.user_id == user.id))
    db.commit()
    assert client.get('/_test/allowed', headers=headers).status_code == 403
    user.is_active = False
    db.commit()
    assert client.get('/api/v1/auth/me', headers=headers).status_code == 401
    assert client.post('/api/v1/auth/login', json={'username': data['username'], 'password': data['password']}).status_code == 401


def test_audit_sanitization_and_failure_rolls_back_registration(auth_context, monkeypatch):
    client, db = auth_context
    data, setup, tokens = enroll(client)
    rows = db.scalars(select(AuditLog).where(AuditLog.actor_user_id == uuid.UUID(security.decode_access_token(tokens['access_token'])['sub']))).all()
    assert {'auth.registration', 'auth.mfa_setup', 'auth.mfa_verified'} <= {r.action for r in rows}
    for row in rows:
        assert row.old_values is None and row.new_values is None and row.event_metadata is None
        assert row.user_agent is None
    before = db.scalar(select(func.count()).select_from(User))
    def fail_audit(*args, **kwargs):
        raise SQLAlchemyError('simulated write failure')
    monkeypatch.setattr(AuthService, 'audit', fail_audit)
    response = client.post('/api/v1/auth/register', json=registration())
    assert response.status_code == 503
    assert db.scalar(select(func.count()).select_from(User)) == before
    assert client.post('/api/v1/auth/refresh', json={'refresh_token': tokens['refresh_token']}).status_code == 503
    assert client.get('/api/v1/auth/me', headers=authorization(tokens)).status_code == 200
    old = db.scalar(select(AuthSession).where(AuthSession.refresh_token_hash == security.hash_refresh_token(tokens['refresh_token'])))
    assert old.revoked_at is None and old.replaced_by_session_id is None


def test_database_functional_indexes_and_mfa_uniqueness(auth_context):
    client, db = auth_context
    data, _, tokens = enroll(client)
    user = db.scalar(select(User).where(User.username == data['username']))
    for values in ({'username': user.username.upper(), 'email': uuid.uuid4().hex + '@example.com'},
                   {'username': 'other_' + uuid.uuid4().hex, 'email': user.email.upper()}):
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.execute(User.__table__.insert().values(**values, password_hash='!'))
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(UserMfaMethod.__table__.insert().values(user_id=user.id, method_type='TOTP',
                secret_encrypted='test', is_active=True, verified_at=security.utcnow()))


def test_docs_health_and_openapi(auth_context):
    client, _ = auth_context
    assert client.get('/docs').status_code == 200
    assert client.get('/health/database').json()['user'] == 'saps_api'
    paths = client.get('/openapi.json').json()['paths']
    assert all('/api/v1/auth/' + endpoint in paths for endpoint in ('register', 'login', 'mfa/setup', 'mfa/verify-setup', 'mfa/verify', 'refresh', 'logout', 'me'))


def test_challenge_cannot_refresh_and_expired_challenge_is_rejected(auth_context):
    client, db = auth_context
    response = client.post('/api/v1/auth/register', json=registration()).json()
    token = response['challenge_token']
    claims = security.decode_token(token, 'mfa_setup')
    for raw in (token, claims['jti']):
        assert client.post('/api/v1/auth/refresh', json={'refresh_token': raw}).status_code in (401, 422)
    row = db.get(AuthSession, uuid.UUID(claims['sid']))
    row.issued_at = security.utcnow() - timedelta(minutes=10)
    row.expires_at = security.utcnow() - timedelta(minutes=5)
    db.commit()
    assert client.post('/api/v1/auth/mfa/setup', json={'challenge_token': token}).status_code == 401


def test_production_requires_https(auth_context, monkeypatch):
    client, _ = auth_context
    monkeypatch.setattr(settings, 'environment', 'production')
    response = client.post('/api/v1/auth/login', json={'username': 'unknown', 'password': 'anything'})
    assert response.status_code == 400 and response.json()['detail'] == 'HTTPS is required'
