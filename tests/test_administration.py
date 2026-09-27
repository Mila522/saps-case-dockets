import uuid
import pytest
from sqlalchemy import select, text, func
from sqlalchemy.exc import DBAPIError
from test_decisions_dockets import workflow_context, officer_account
from test_authentication import enroll, authorization, registration
from auth_mailbox import code_for
from app.modules.access.models import User, Role, UserRole
from app.modules.audit.models import AuditLog
from app.modules.authentication.security import decode_access_token, verify_password
from app.modules.stations.models import Station
from app.modules.administration.bootstrap import provision
from app.modules.administration.schemas import Credentials


def admin_context(client,db):
    data,_,tokens=enroll(client)
    identity=uuid.UUID(decode_access_token(tokens['access_token'])['sub'])
    db.execute(UserRole.__table__.delete().where(UserRole.user_id==identity))
    db.add(UserRole(user_id=identity,role_id=db.scalar(select(Role.id).where(Role.code=='SYSTEM_ADMINISTRATOR'))))
    station=Station(station_code='ADMIN-'+uuid.uuid4().hex[:8],name='Admin test station',province='Test')
    db.add(station);db.commit()
    return authorization(tokens),identity,station


def payload(station,role='CHARGE_OFFICER'):
    data=registration()
    return {k:data[k] for k in ('username','email','password')} | dict(role=role,station_id=str(station.id),service_number='A-'+uuid.uuid4().hex,rank='Officer')


def test_staff_provision_mfa_update_and_revocation(workflow_context):
    client,db=workflow_context
    admin,actor,station=admin_context(client,db)
    db.execute(text('SET LOCAL ROLE saps_api'));db.commit()
    for role in ('CHARGE_OFFICER','STATION_COMMANDER','INVESTIGATING_OFFICER'):
        data=payload(station,role)
        response=client.post('/api/v1/admin/users',headers=admin,json=data)
        assert response.status_code==201, response.text
        result=response.json()
        assert result['roles']==[role] and result['station_id']==str(station.id)
        assert not result['is_verified'] and 'password' not in response.text
        user=db.get(User,uuid.UUID(result['id']))
        assert verify_password(data['password'],user.password_hash) and not user.mfa_enabled
        assert client.post('/api/v1/admin/users',headers=admin,json=data).status_code==409
    login=client.post('/api/v1/auth/login',json={'username':data['username'],'password':data['password']})
    assert login.status_code==200 and login.json()['status']=='EMAIL_CODE_REQUIRED'
    tokens=client.post('/api/v1/auth/email/verify',json={'challenge_token':login.json()['challenge_token'],'code':code_for(data['email'])}).json()
    staff=authorization(tokens)
    assert client.get('/api/v1/auth/me',headers=staff).status_code==200
    assert client.get('/api/v1/admin/users',headers=staff).status_code==403
    current=client.get('/api/v1/admin/users',headers=admin,params={'q':data['username']}).json()['items'][0]
    change={k:current[k] for k in ('station_id','service_number','rank','phone_number')}
    change.update(role='STATION_COMMANDER',is_active=False,expected_updated_at=current['updated_at'])
    changed=client.patch('/api/v1/admin/users/'+result['id'],headers=admin,json=change)
    assert changed.status_code==200,changed.text
    assert changed.json()['roles']==['STATION_COMMANDER'] and not changed.json()['is_active']
    assert client.get('/api/v1/auth/me',headers=staff).status_code==401
    assert client.post('/api/v1/auth/login',json={'username':data['username'],'password':data['password']}).status_code==401
    assert client.patch('/api/v1/admin/users/'+result['id'],headers=admin,json=change).status_code==409
    change.update(is_active=True,expected_updated_at=changed.json()['updated_at'])
    assert client.patch('/api/v1/admin/users/'+result['id'],headers=admin,json=change).status_code==200
    assert client.get('/api/v1/auth/me',headers=staff).status_code==401
    assert client.get('/api/v1/admin/users',headers=admin,params={'role':'STATION_COMMANDER','station_id':str(station.id),'active':True}).json()['total']>=1
    audit=db.scalars(select(AuditLog).where(AuditLog.actor_user_id==actor,AuditLog.action=='admin.staff.update')).all()
    assert len(audit)==2 and data['password'] not in str([r.new_values for r in audit])


def test_admin_scope_validation_audit_redaction_and_immutability(workflow_context):
    client,db=workflow_context
    admin,actor,station=admin_context(client,db)
    for role in ('CHARGE_OFFICER','STATION_COMMANDER','INVESTIGATING_OFFICER','SAPS_MANAGEMENT'):
        headers,_,_=officer_account(client,db,role,station)
        for path in ('/api/v1/admin/users','/api/v1/admin/audit','/api/v1/admin/options'):
            assert client.get(path,headers=headers).status_code==403
    assert client.get('/api/v1/admin/users').status_code==401
    for path in ('/api/v1/complaints/station','/api/v1/dockets','/api/v1/investigations/dockets','/api/v1/dashboards/station'):
        assert client.get(path,headers=admin).status_code in (403,404)
    data=payload(station)
    assert client.post('/api/v1/admin/users',headers=admin,json={**data,'role':'SYSTEM_ADMINISTRATOR'}).status_code==422
    assert client.post('/api/v1/admin/users',headers=admin,json={**data,'password':'short'}).status_code==422
    assert client.post('/api/v1/admin/users',headers=admin,json={**data,'station_id':str(uuid.uuid4())}).status_code==422
    station.is_active=False;db.commit()
    assert client.post('/api/v1/admin/users',headers=admin,json=data).status_code==422
    event=AuditLog(actor_type='USER',actor_user_id=actor,action='case.private.test',entity_type='docket',entity_id=uuid.uuid4(),new_values={'password':'sensitive-test','content':'private-note'},event_metadata={'code':'private-code'})
    db.add(event);db.commit()
    db.execute(text('SET LOCAL ROLE saps_api'));db.commit()
    page=client.get('/api/v1/admin/audit',headers=admin,params={'actor':str(actor),'action':event.action,'entity_id':str(event.entity_id),'limit':1}).json()
    assert page['total']==1 and len(page['items'])==1
    detail=client.get('/api/v1/admin/audit/'+str(event.id),headers=admin)
    assert detail.status_code==200 and 'private-note' not in detail.text and 'sensitive-test' not in detail.text and 'private-code' not in detail.text
    for method in (client.patch,client.delete):
        assert method('/api/v1/admin/audit/'+str(event.id),headers=admin).status_code==405
    for sql in ('UPDATE case_mgmt.audit_logs SET action=:action WHERE id=:id','DELETE FROM case_mgmt.audit_logs WHERE id=:id'):
        with pytest.raises(DBAPIError):
            with db.begin_nested():db.execute(text(sql),{'id':event.id,'action':'changed'})
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.actor_user_id==actor,AuditLog.action=='admin.audit.view'))==1


def test_first_admin_bootstrap_is_idempotent_and_keeps_verification(workflow_context):
    _,db=workflow_context
    data=registration()
    credentials=Credentials(**{k:data[k] for k in ('username','email','password')})
    # Existing real administrators must never be removed to make setup testing pass.
    existing=db.scalar(select(UserRole.user_id).join(Role).where(Role.code=='SYSTEM_ADMINISTRATOR').limit(1))
    if existing:
        with pytest.raises(ValueError):provision(db,credentials)
        return
    assert provision(db,credentials)
    db.commit()
    assert not provision(db,credentials)
    user=db.scalar(select(User).where(User.username==data['username']))
    assert not user.is_verified and not user.mfa_enabled
    assert verify_password(data['password'],user.password_hash)
    with pytest.raises(ValueError):provision(db,Credentials(username='different_'+uuid.uuid4().hex,email=uuid.uuid4().hex+'@example.com',password=data['password']))


def test_station_transfer_preserves_ids_and_blocks_open_assignments(workflow_context):
    from test_decisions_dockets import complaint_at_station
    client,db=workflow_context
    admin,_,station=admin_context(client,db)
    other=Station(station_code='TRANSFER-'+uuid.uuid4().hex[:8],name='Transfer',province='Test')
    db.add(other);db.commit()
    charge,_,_=officer_account(client,db,'CHARGE_OFFICER',station)
    commander,_,_=officer_account(client,db,'STATION_COMMANDER',station)
    _,identity,officer=officer_account(client,db,'INVESTIGATING_OFFICER',station)
    row=complaint_at_station(client,db,station)
    accepted=client.post(f'/api/v1/complaints/{row.id}/decisions',headers=charge,json={'decision':'ACCEPTED'}).json()
    docket_id=accepted['docket_id']
    assert client.post(f'/api/v1/dockets/{docket_id}/approvals',headers=commander,json={'decision':'APPROVED'}).status_code==201
    assert client.post(f'/api/v1/dockets/{docket_id}/assignments',headers=commander,json={'investigating_officer_id':str(officer.id),'reason':'Assignment test'}).status_code==201
    current=next(r for r in client.get('/api/v1/admin/users',headers=admin,params={'station_id':str(station.id)}).json()['items'] if r['id']==str(identity))
    change={k:current[k] for k in ('service_number','rank','phone_number')}
    change.update(role='INVESTIGATING_OFFICER',station_id=str(other.id),is_active=True,expected_updated_at=current['updated_at'])
    assert client.patch('/api/v1/admin/users/'+str(identity),headers=admin,json=change).status_code==409
    # Access can still be removed immediately, without deleting any assignment.
    change.update(station_id=str(station.id),is_active=False)
    assert client.patch('/api/v1/admin/users/'+str(identity),headers=admin,json=change).status_code==200
    # Staff without open cases can transfer while retaining user/officer identity.
    created=client.post('/api/v1/admin/users',headers=admin,json=payload(station)).json()
    change={k:created[k] for k in ('service_number','rank','phone_number')}
    change.update(role='CHARGE_OFFICER',station_id=str(other.id),is_active=True,expected_updated_at=created['updated_at'])
    moved=client.patch('/api/v1/admin/users/'+created['id'],headers=admin,json=change)
    assert moved.status_code==200 and moved.json()['id']==created['id'] and moved.json()['station_id']==str(other.id)
