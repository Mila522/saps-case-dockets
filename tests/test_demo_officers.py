import uuid
import sys

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.modules.access.models import User, Role, UserRole
from app.modules.audit.models import AuditLog
from app.modules.authentication.models import UserEmailAuth, UserMfaMethod
from app.modules.authentication.security import verify_password
from app.modules.stations.models import Officer
from app.modules.stations.seed_demo_officers import SPECS, ACTION, provision, ensure_local
from app.modules.stations import seed_demo_officers
from app.modules.stations.seed_kzn import seed
from auth_mailbox import code_for
from test_decisions_dockets import workflow_context


@pytest.fixture(autouse=True)
def isolated_demo_names(monkeypatch):
    suffix=uuid.uuid4().hex[:8]
    specs=tuple((name+'_'+suffix,role,rank,service+'-'+suffix) for name,role,rank,service in seed_demo_officers.SPECS)
    monkeypatch.setattr(seed_demo_officers,'SPECS',specs)
    monkeypatch.setattr(sys.modules[__name__],'SPECS',specs)


def addresses():
    return {spec[0]:uuid.uuid4().hex+'@example.com' for spec in SPECS}


def test_explicit_reset_preserves_identity_and_requires_new_email_login(workflow_context):
    from app.modules.stations.reset_demo_passwords import reset_passwords
    client,db=workflow_context
    seed(db);db.commit()
    emails=addresses()
    original=provision(db,emails);db.commit()
    snapshots={row.username:(row.id,row.email,row.is_verified,row.mfa_enabled)
        for row in db.scalars(select(User).where(User.username.in_(emails)))}
    results=reset_passwords(db);db.commit()
    for old,new in zip(original,results):
        user=db.scalar(select(User).where(User.username==new.username))
        assert (user.id,user.email,user.is_verified,user.mfa_enabled)==snapshots[new.username]
        assert not verify_password(old.temporary_password.get_secret_value(),user.password_hash)
        secret=new.temporary_password.get_secret_value()
        assert verify_password(secret,user.password_hash) and secret not in repr(new)
        response=client.post('/api/v1/auth/login',json={'username':new.username,'password':secret})
        assert response.status_code==200 and response.json()['status']=='EMAIL_CODE_REQUIRED'
        assert 'access_token' not in response.json()


def test_demo_creation_repeatability_and_real_email_auth_flow(workflow_context):
    client, db = workflow_context
    seed(db);db.commit()
    emails = addresses()
    results = provision(db, emails);db.commit()
    snapshots = {}
    for spec, result in zip(SPECS, results):
        user = db.scalar(select(User).where(User.username==spec[0]))
        officer = db.scalar(select(Officer).where(Officer.user_id==user.id))
        assert officer.rank==spec[2] and officer.service_number==spec[3]
        assert not user.is_verified and not user.mfa_enabled
        assert db.get(UserEmailAuth,user.id) is None
        assert db.scalar(select(UserMfaMethod.id).where(UserMfaMethod.user_id==user.id)) is None
        assert set(db.scalars(select(Role.code).join(UserRole).where(UserRole.user_id==user.id)))=={spec[1]}
        secret = result.temporary_password.get_secret_value()
        assert len(secret)>=24 and verify_password(secret,user.password_hash)
        assert secret not in repr(result)
        snapshots[spec[0]]=(user.id,user.email,user.password_hash,officer.id,officer.station_id)
        login = client.post('/api/v1/auth/login',json={'username':spec[0],'password':secret})
        assert login.status_code==200 and login.json()['status']=='EMAIL_CODE_REQUIRED'
        token=login.json()['challenge_token']
        assert client.get('/api/v1/auth/me',headers={'Authorization':'Bearer '+token}).status_code==401
        verify=client.post('/api/v1/auth/email/verify',json={'challenge_token':token,'code':code_for(emails[spec[0]])})
        assert verify.status_code==200
        headers={'Authorization':'Bearer '+verify.json()['access_token']}
        me=client.get('/api/v1/auth/me',headers=headers)
        assert {role['code'] for role in me.json()['roles']}=={spec[1]}
        url='/api/v1/investigations/dockets' if spec[1]=='INVESTIGATING_OFFICER' else '/api/v1/complaints/station'
        assert client.get(url,headers=headers).status_code==200
    again=provision(db,{});db.commit()
    assert all(result.temporary_password is None for result in again)
    for spec in SPECS:
        user=db.scalar(select(User).where(User.username==spec[0]))
        officer=db.scalar(select(Officer).where(Officer.user_id==user.id))
        assert (user.id,user.email,user.password_hash,officer.id,officer.station_id)==snapshots[spec[0]]
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action==ACTION,
        AuditLog.entity_id.in_([row[0] for row in snapshots.values()])))==3


def test_existing_email_conflict_rolls_back_all_accounts(workflow_context):
    _,db=workflow_context
    seed(db);db.commit()
    emails=addresses()
    existing=User(username='existing_'+uuid.uuid4().hex,email=emails[SPECS[1][0]],password_hash='!')
    db.add(existing);db.commit()
    with pytest.raises(ValueError,match='already registered'):
        with db.begin_nested(): provision(db,emails)
    assert db.scalar(select(User.id).where(User.username==SPECS[0][0])) is None
    assert db.get(User,existing.id).password_hash=='!'


def test_changed_roles_and_email_are_never_overwritten(workflow_context):
    _,db=workflow_context
    seed(db);db.commit()
    emails=addresses();provision(db,emails);db.commit()
    with pytest.raises(ValueError,match='different email'):
        with db.begin_nested(): provision(db,addresses())
    user=db.scalar(select(User).where(User.username==SPECS[0][0]))
    role=db.scalar(select(Role).where(Role.code=='COMPLAINANT'))
    db.add(UserRole(user_id=user.id,role_id=role.id));db.commit()
    with pytest.raises(ValueError,match='different provisioning'):
        with db.begin_nested(): provision(db,{})
    assert db.get(UserRole,(user.id,role.id))


@pytest.mark.parametrize('environment,host',[('production','localhost'),('test','localhost'),('development','remote.example.com')])
def test_not_local_development_is_refused(monkeypatch,environment,host):
    monkeypatch.setattr(settings,'environment',environment)
    monkeypatch.setattr(settings,'database_url',f'postgresql+psycopg://example@{host}/example')
    with pytest.raises(ValueError,match='loopback'):ensure_local()


def test_noninteractive_creation_is_refused(monkeypatch):
    monkeypatch.setattr(sys,'argv',['seed_demo_officers'])
    monkeypatch.setattr(sys.stdin,'isatty',lambda:False)
    with pytest.raises(SystemExit,match='interactive terminal'):
        seed_demo_officers.main()


def test_unrelated_matching_username_is_not_adopted(workflow_context):
    _,db=workflow_context
    seed(db);db.commit()
    user=User(username=SPECS[0][0],email=uuid.uuid4().hex+'@example.com',password_hash='!')
    db.add(user);db.commit()
    with pytest.raises(ValueError,match='different provisioning'):
        with db.begin_nested():provision(db,addresses())
    assert db.get(User,user.id).password_hash=='!'
    assert db.scalar(select(Officer.id).where(Officer.user_id==user.id)) is None
