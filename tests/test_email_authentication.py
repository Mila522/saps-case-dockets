import uuid
from datetime import timedelta
from unittest.mock import MagicMock

import pyotp
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.modules.access.models import User
from app.modules.authentication import mail, security
from app.modules.authentication.email_flow import digest
from app.modules.authentication.models import AuthSession, EmailChallenge, UserEmailAuth, UserMfaMethod
from auth_mailbox import code_for, messages
from test_authentication import auth_context, registration, enroll, authorization
from test_decisions_dockets import workflow_context


def begin(client):
    data = registration()
    result = client.post('/api/v1/auth/register', json=data)
    assert result.status_code == 201
    return data, result.json()


def record(db, challenge):
    return db.get(EmailChallenge, uuid.UUID(security.decode_token(challenge['challenge_token'], 'email_code')['sid']))


def verify(client, challenge, code):
    return client.post('/api/v1/auth/email/verify', json={'challenge_token':challenge['challenge_token'], 'code':code})


def test_expired_and_superseded_codes(auth_context):
    client, db = auth_context
    data, first = begin(client)
    old = code_for(data['email'])
    row = record(db, first)
    row.code_expires_at = security.utcnow() - timedelta(seconds=1)
    db.commit()
    assert verify(client, first, old).status_code == 401
    second = client.post('/api/v1/auth/email/resend', json={'challenge_token':first['challenge_token']})
    assert second.status_code == 200
    assert verify(client, first, old).status_code == 401
    assert verify(client, second.json(), code_for(data['email'])).status_code == 200


def test_cooldown_and_hourly_limit_cover_new_logins(auth_context, monkeypatch):
    client, db = auth_context
    monkeypatch.setattr(settings, 'email_resend_seconds', 60)
    monkeypatch.setattr(settings, 'email_max_sends_per_hour', 2)
    data, first = begin(client)
    for path, payload in [('email/resend', {'challenge_token':first['challenge_token']}),
            ('login', {'username':data['username'], 'password':data['password']})]:
        response = client.post('/api/v1/auth/'+path, json=payload)
        assert response.status_code == 429 and int(response.headers['retry-after']) > 0
    record(db, first).attempted_at -= timedelta(seconds=61)
    db.commit()
    second = client.post('/api/v1/auth/email/resend', json={'challenge_token':first['challenge_token']}).json()
    record(db, second).attempted_at -= timedelta(seconds=61)
    db.commit()
    assert client.post('/api/v1/auth/email/resend', json={'challenge_token':second['challenge_token']}).status_code == 429


def test_guess_budget_survives_resends(auth_context):
    client, db = auth_context
    data, current = begin(client)
    for attempt in range(settings.max_failed_login_attempts):
        assert verify(client, current, 'abcdef').status_code == 401
        if attempt < settings.max_failed_login_attempts - 1:
            response = client.post('/api/v1/auth/email/resend', json={'challenge_token':current['challenge_token']})
            assert response.status_code == 200
            current = response.json()
    assert verify(client, current, code_for(data['email'])).status_code == 401
    user = db.scalar(select(User).where(User.email == data['email']))
    assert not user.is_verified and user.locked_until > security.utcnow()


def test_smtp_failure_preserves_pending_account_and_rate_limit(auth_context, monkeypatch):
    client, db = auth_context
    monkeypatch.setattr(settings, 'email_resend_seconds', 60)
    def fail(*args):
        raise mail.MailUnavailable('sensitive provider detail')
    monkeypatch.setattr(mail, 'send_code', fail)
    data = registration()
    response = client.post('/api/v1/auth/register', json=data)
    assert response.status_code == 503 and 'sensitive' not in response.text
    user = db.scalar(select(User).where(User.email == data['email']))
    assert user and not user.is_verified and not user.mfa_enabled
    row = db.scalar(select(EmailChallenge).where(EmailChallenge.user_id == user.id))
    assert row.delivery_state == 'failed' and db.get(AuthSession, row.session_id).revoked_at
    assert client.post('/api/v1/auth/login', json={'username':data['username'],'password':data['password']}).status_code == 429


def test_legacy_transition_requires_existing_factor_then_email(auth_context):
    client, db = auth_context
    data, pending = begin(client)
    user = db.scalar(select(User).where(User.email == data['email']))
    secret = security.generate_totp_secret()
    method = UserMfaMethod(user_id=user.id, method_type='TOTP', secret_encrypted=security.encrypt_totp_secret(secret),
        is_active=True, verified_at=security.utcnow())
    user.mfa_enabled = True
    db.add(method); db.commit()
    # Even a code issued before an existing factor was enrolled cannot replace it.
    assert verify(client, pending, code_for(data['email'])).status_code == 401
    challenge = client.post('/api/v1/auth/login', json={'username':data['username'],'password':data['password']}).json()
    assert challenge['status'] == 'TOTP_TRANSITION_REQUIRED'
    assert client.post('/api/v1/auth/email/transition', json={'challenge_token':challenge['challenge_token'],'code':'abcdef'}).status_code == 401
    response = client.post('/api/v1/auth/email/transition', json={'challenge_token':challenge['challenge_token'],'code':pyotp.TOTP(secret).now()})
    assert response.status_code == 200 and 'access_token' not in response.json()
    assert method.is_active and not user.is_verified
    tokens = verify(client, response.json(), code_for(data['email']))
    assert tokens.status_code == 200
    db.refresh(method)
    assert not method.is_active and user.is_verified and db.get(UserEmailAuth, user.id)
    assert client.get('/api/v1/auth/me', headers=authorization(tokens.json())).status_code == 200
    assert client.post('/api/v1/auth/login', json={'username':data['username'],'password':data['password']}).json()['status'] == 'EMAIL_CODE_REQUIRED'


def test_address_and_purpose_binding(auth_context):
    client, db = auth_context
    data, challenge = begin(client)
    row = record(db, challenge)
    code = code_for(data['email'])
    assert row.code_digest != digest(row.user_id, row.session_id, 'transition', row.recipient, code)
    user = db.get(User, row.user_id)
    user.email = uuid.uuid4().hex + '@example.com'
    db.commit()
    assert verify(client, challenge, code).status_code == 401


def test_email_method_required_for_access_and_refresh(auth_context):
    client, db = auth_context
    data, _, tokens = enroll(client)
    user = db.scalar(select(User).where(User.email == data['email']))
    db.get(UserEmailAuth, user.id).verified_email = 'different@example.com'
    db.commit()
    assert client.get('/api/v1/auth/me', headers=authorization(tokens)).status_code == 401
    assert client.post('/api/v1/auth/refresh', json={'refresh_token':tokens['refresh_token']}).status_code == 401
    assert client.post('/api/v1/auth/login', json={'username':data['username'],'password':data['password']}).status_code == 403


def test_enabled_flag_without_trusted_method_needs_recovery(auth_context):
    client, db = auth_context
    data, challenge = begin(client)
    user = db.scalar(select(User).where(User.email == data['email']))
    user.mfa_enabled = True
    user.is_verified = True  # Neither legacy flag is evidence of a trusted method.
    db.commit()
    assert verify(client, challenge, code_for(data['email'])).status_code == 401
    assert client.post('/api/v1/auth/login', json={'username':data['username'],'password':data['password']}).status_code == 403


def test_smtp_adapter_tls_timeout_and_sanitized_failure(monkeypatch):
    # Load the real adapter under a separate module because the suite patches send_code.
    import importlib.util
    spec = importlib.util.spec_from_file_location('smtp_adapter_test', mail.__file__)
    adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
    from pydantic import SecretStr
    for key, value in {'smtp_host':'smtp.example.invalid','smtp_port':587,'smtp_use_starttls':True,
            'smtp_username':SecretStr('test'),'smtp_password':SecretStr('test'), 'email_from':'sender@example.invalid'}.items():
        monkeypatch.setattr(settings, key, value)
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.send_message.return_value = {}
    factory = MagicMock(return_value=connection)
    monkeypatch.setattr(adapter.smtplib, 'SMTP', factory)
    adapter.send_code('recipient@example.invalid', '123456')
    assert factory.call_args.kwargs['timeout'] == 10
    context = connection.starttls.call_args.kwargs['context']
    assert context.check_hostname and context.verify_mode == 2
    connection.send_message.side_effect = OSError('private-provider-detail')
    with pytest.raises(adapter.MailUnavailable) as error:
        adapter.send_code('recipient@example.invalid', '123456')
    assert str(error.value) == ''


@pytest.mark.parametrize('failure',['connect','authentication','data','quit'])
def test_smtp_confirmation_is_not_confused_with_connection_shutdown(monkeypatch,failure):
    import importlib.util
    from pydantic import SecretStr
    spec=importlib.util.spec_from_file_location('smtp_confirmation_test',mail.__file__)
    adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
    for key,value in {'smtp_host':'smtp.example.invalid','smtp_use_starttls':True,
            'smtp_username':SecretStr('test'),'smtp_password':SecretStr('test'),
            'email_from':'sender@example.invalid'}.items():
        monkeypatch.setattr(settings,key,value)
    smtp=MagicMock();smtp.__enter__.return_value=smtp;smtp.send_message.return_value={}
    factory=MagicMock(return_value=smtp)
    if failure=='connect':factory.side_effect=PermissionError('private network detail')
    if failure=='authentication':smtp.login.side_effect=adapter.smtplib.SMTPAuthenticationError(535,b'private provider detail')
    if failure=='data':smtp.send_message.side_effect=OSError('private provider detail')
    if failure=='quit':smtp.__exit__.side_effect=adapter.smtplib.SMTPResponseException(421,b'private provider detail')
    monkeypatch.setattr(adapter.smtplib,'SMTP',factory)
    if failure=='quit':
        # A completed DATA acceptance remains issued even if QUIT fails.
        adapter.send_code('recipient@example.invalid','123456')
        smtp.send_message.assert_called_once()
    else:
        with pytest.raises(adapter.MailUnavailable) as error:
            adapter.send_code('recipient@example.invalid','123456')
        assert str(error.value)==''


@pytest.mark.parametrize('role',['CHARGE_OFFICER','STATION_COMMANDER'])
def test_staff_failed_resend_revokes_old_and_failed_codes_then_recovers(workflow_context,monkeypatch,role):
    from app.modules.access.models import Role, UserRole
    from app.modules.stations.models import Officer, Station
    from auth_mailbox import send
    client,db=workflow_context
    data,_,_=enroll(client)
    user=db.scalar(select(User).where(User.email==data['email']))
    station=Station(station_code='MAIL-'+uuid.uuid4().hex[:8],name='Test',province='Test')
    db.add(station);db.flush()
    db.execute(UserRole.__table__.delete().where(UserRole.user_id==user.id))
    db.add(UserRole(user_id=user.id,role_id=db.scalar(select(Role.id).where(Role.code==role))))
    db.add(Officer(user_id=user.id,station_id=station.id,service_number=uuid.uuid4().hex,rank='Test'))
    db.commit()
    credentials={'username':data['username'],'password':data['password']}
    first=client.post('/api/v1/auth/login',json=credentials)
    assert first.status_code==200 and first.json()['status']=='EMAIL_CODE_REQUIRED'
    old_code=code_for(data['email'])
    attempted=[]
    def fail(recipient,code):
        attempted.append(code)  # Memory-only test fixture, never emitted.
        raise mail.MailUnavailable()
    monkeypatch.setattr(mail,'send_code',fail)
    failed=client.post('/api/v1/auth/email/resend',json={'challenge_token':first.json()['challenge_token']})
    assert failed.status_code==503 and failed.headers['retry-after']=='60'
    assert 'challenge_token' not in failed.json() and 'access_token' not in failed.json()
    assert verify(client,first.json(),old_code).status_code==401
    row=db.scalar(select(EmailChallenge).where(EmailChallenge.user_id==user.id,
        EmailChallenge.delivery_state=='failed').order_by(EmailChallenge.attempted_at.desc()))
    assert db.get(AuthSession,row.session_id).revoked_at
    forged={'challenge_token':security.create_token(user.id,row.session_id,'email_code',timedelta(minutes=5))}
    assert verify(client,forged,attempted[0]).status_code==401
    monkeypatch.setattr(mail,'send_code',send)
    fresh=client.post('/api/v1/auth/login',json=credentials)
    assert fresh.status_code==200
    assert verify(client,fresh.json(),code_for(data['email'])).status_code==200
