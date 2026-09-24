"""A–C intake corrections use PostgreSQL transactions and real MFA-protected APIs."""
import uuid

import pytest
from sqlalchemy import select, func, text
from sqlalchemy.exc import SQLAlchemyError, DBAPIError

from test_decisions_dockets import workflow_context, officer_account, complaint_at_station
from test_complaint_tracking import account
from app.main import app
from app.modules.complainants.models import Complainant
from app.modules.complaints.models import Complaint, ComplaintStatement, Witness, WitnessStatement
from app.modules.dockets.models import Docket
from app.modules.investigations.models import DocketStatusHistory
from app.modules.audit.models import AuditLog
from app.modules.communications.models import Notification
from app.modules.stations.models import Station
from app.modules.system.models import IdentifierCounter


@pytest.fixture
def intake(workflow_context):
    client, db = workflow_context
    station = Station(station_code='INTAKE-'+uuid.uuid4().hex[:8], name='Intake', province='Test')
    foreign = Station(station_code='FOREIGN-'+uuid.uuid4().hex[:8], name='Other', province='Test')
    db.add_all([station, foreign]); db.commit()
    charge, actor, officer = officer_account(client, db, 'CHARGE_OFFICER', station)
    outsider, _, _ = officer_account(client, db, 'CHARGE_OFFICER', foreign)
    commander, _, _ = officer_account(client, db, 'STATION_COMMANDER', station)
    return client, db, station, charge, officer, outsider, commander


def walk_in(**overrides):
    return {'complainant':{'first_name':'Walk','last_name':'In','phone_number':'0123456789'},
        'details_confirmed_with_complainant':True, 'crime_category':'Theft',
        'incident_description':'Actual complainant statement', 'incident_location':'Reported location',
        'incident_province':'Gauteng', **overrides}


def test_review_has_one_route_one_response_and_one_audit(intake):
    client, db, station, charge, officer, *_ = intake
    path='/api/v1/complaints/{complaint_id}/review'
    def registered(router):
        for route in router.routes:
            if hasattr(route, 'original_router'):
                yield from registered(route.original_router)
            else:
                yield route
    routes=[route for route in registered(app) if getattr(route,'path','').endswith('/complaints/{complaint_id}/review') and 'POST' in route.methods]
    assert len(routes)==1
    assert app.openapi()['paths'][path]['post']['responses']['200']['content']['application/json']['schema']['$ref'].endswith('/StationComplaintOut')
    row=complaint_at_station(client,db,station)
    result=client.post(f'/api/v1/complaints/{row.id}/review',headers=charge)
    assert result.status_code==200 and result.json()['crime_category']==row.crime_category
    assert client.post(f'/api/v1/complaints/{row.id}/review',headers=charge).status_code==409
    assert list(db.scalars(select(AuditLog.action).where(AuditLog.entity_id==row.id)))==['complaint.review.start']


def test_walk_in_derives_authority_and_preserves_supplied_material(intake):
    client,db,station,charge,officer,outsider,commander=intake
    data=walk_in(witnesses=[{'first_name':'Actual','last_name':'Witness','statement_text':'Actual witness account'}])
    response=client.post('/api/v1/complaints/in-station',headers=charge,json=data)
    assert response.status_code==201,response.text
    result=response.json();row=db.get(Complaint,uuid.UUID(result['id']))
    person=db.get(Complainant,row.complainant_id)
    assert person.user_id is None and person.first_name=='Walk'
    assert row.channel=='IN_STATION' and row.station_id==station.id and row.registered_by_officer_id==officer.id
    assert row.submitted_at and result['reference_number'].startswith('CMP-'+station.station_code)
    materials=client.get(f'/api/v1/complaints/{row.id}/materials',headers=charge).json()
    assert materials['statements'][0]['statement_text']==data['incident_description']
    assert materials['statements'][0]['signed_at'] is None
    assert materials['witnesses'][0]['statements'][0]['statement_text']=='Actual witness account'
    assert client.get(f'/api/v1/complaints/{row.id}/materials',headers=outsider).status_code==404
    assert client.get(f'/api/v1/complaints/{row.id}/materials',headers=commander).json()['can_append'] is False
    assert not any(key in str(materials) for key in ['identity_number_hash','identity_number_encrypted','password_hash'])
    plain=client.post('/api/v1/complaints/in-station',headers=charge,json=walk_in()).json()
    assert plain['reference_number']!=result['reference_number']
    assert client.get(f'/api/v1/complaints/{plain["id"]}/materials',headers=charge).json()['witnesses']==[]


def test_walk_in_rejects_impersonation_unconfirmed_and_inactive_authority(intake):
    client,db,station,charge,officer,_,commander=intake
    for payload in [walk_in(station_id=str(station.id)),walk_in(registered_by_officer_id=str(officer.id)),
                    walk_in(complainant_id=str(uuid.uuid4())),walk_in(details_confirmed_with_complainant=False),
                    walk_in(complainant={'first_name':'X','last_name':'Y','phone_number':'1','user_id':str(officer.user_id)})]:
        assert client.post('/api/v1/complaints/in-station',headers=charge,json=payload).status_code==422
    assert client.post('/api/v1/complaints/in-station',json=walk_in()).status_code==401
    assert client.post('/api/v1/complaints/in-station',headers=commander,json=walk_in()).status_code==403
    officer.is_active=False;db.commit()
    assert client.post('/api/v1/complaints/in-station',headers=charge,json=walk_in()).status_code==403
    officer.is_active=True;station.is_active=False;db.commit()
    assert client.post('/api/v1/complaints/in-station',headers=charge,json=walk_in()).status_code==403


@pytest.mark.parametrize('stage',['counter','history','audit','notification'])
def test_acceptance_rolls_back_entire_operation(intake,monkeypatch,stage):
    client,db,station,charge,*_=intake
    row=complaint_at_station(client,db,station)
    def fail(*args,**kwargs):raise SQLAlchemyError('Injected transactional failure')
    if stage=='counter':
        import app.modules.dockets.service as module
        allocate=module.allocate_cas_number
        def fail_after_allocation(*args):allocate(*args);fail()
        monkeypatch.setattr(module,'allocate_cas_number',fail_after_allocation)
    elif stage=='notification':
        monkeypatch.setattr('app.modules.refusals.service.notify_complainant',fail)
    else:
        add=db.add
        def checked_add(value,*args,**kwargs):
            if stage=='history' and isinstance(value,DocketStatusHistory):fail()
            if stage=='audit' and isinstance(value,AuditLog) and value.action=='complaint.decide.accepted':fail()
            return add(value,*args,**kwargs)
        monkeypatch.setattr(db,'add',checked_add)
    result=client.post(f'/api/v1/complaints/{row.id}/decisions',headers=charge,json={'decision':'ACCEPTED'})
    assert result.status_code==503,result.text
    db.expire_all()
    assert db.get(Complaint,row.id).status=='SUBMITTED'
    assert db.scalar(select(Docket.id).where(Docket.complaint_id==row.id)) is None
    from app.modules.refusals.models import ComplaintDecision
    assert db.scalar(select(ComplaintDecision.id).where(ComplaintDecision.complaint_id==row.id)) is None
    assert db.scalar(select(IdentifierCounter.last_value).where(IdentifierCounter.counter_type=='CAS',IdentifierCounter.station_id==station.id)) is None
    assert db.scalar(select(Notification.id).where(Notification.complaint_id==row.id)) is None
    assert db.scalar(select(AuditLog.id).where(AuditLog.entity_id==row.id)) is None


def test_online_tracking_cas_and_reference_remain_owner_only(intake):
    client,db,station,charge,*_=intake
    owner,_,_=account(client,db);other,_,_=account(client,db)
    data={k:v for k,v in walk_in().items() if k not in {'complainant','details_confirmed_with_complainant'}}
    registered=client.post('/api/v1/complaints',headers=owner,json={**data,'station_id':str(station.id)})
    assert registered.status_code==201
    row=registered.json();assert row['cas_number'] is None
    accepted=client.post(f'/api/v1/complaints/{row["id"]}/decisions',headers=charge,json={'decision':'ACCEPTED'}).json()
    tracked=client.post('/api/v1/complaints/track-by-reference',headers=owner,json={'reference_number':row['reference_number']})
    assert tracked.status_code==200 and tracked.json()['cas_number']==accepted['cas_number']
    assert 'incident_description' not in tracked.json() and 'statements' not in tracked.json()
    assert client.get('/api/v1/complaints/mine',headers=owner).json()['items'][0]['cas_number']==accepted['cas_number']
    for headers,status in [({},401),(other,404)]:
        assert client.post('/api/v1/complaints/track-by-reference',headers=headers,json={'reference_number':row['reference_number']}).status_code==status
    assert client.get(f'/api/v1/complaints/{row["id"]}/materials',headers=owner).status_code==403


def test_versioned_statements_initial_evidence_and_investigator_handoff(intake):
    client,db,station,charge,officer,outsider,commander=intake
    row=client.post('/api/v1/complaints/in-station',headers=charge,json=walk_in()).json()
    base=f'/api/v1/complaints/{row["id"]}'
    first=client.get(base+'/materials',headers=charge).json()['statements'][0]
    new={'statement_text':'Corrected account','expected_version':1}
    assert client.post(base+'/statements',headers=charge,json=new).status_code==201
    assert client.post(base+'/statements',headers=charge,json=new).status_code==409
    rows=client.get(base+'/materials',headers=charge).json()['statements']
    assert [s['is_current'] for s in rows]==[False,True] and rows[0]['statement_text']==first['statement_text']
    witness=client.post(base+'/witnesses',headers=charge,json={'first_name':'Real','last_name':'Witness'}).json()
    assert witness['statements']==[]
    assert client.post(base+f'/witnesses/{witness["id"]}/statements',headers=charge,json={'statement_text':'Witness account'}).status_code==201
    evidence={'title':'Initial item','description':'Actual item description','evidence_type':'PHYSICAL_OBJECT','is_digital':False,'storage_location':'Locker A'}
    assert client.post(base+'/initial-evidence',headers=charge,json=evidence).status_code==409
    accepted=client.post(base+'/decisions',headers=charge,json={'decision':'ACCEPTED'}).json()
    initial=client.post(base+'/initial-evidence',headers=charge,json=evidence)
    assert initial.status_code==201,initial.text
    assert initial.json()['current_custodian_officer_id']==str(officer.id)
    assert client.post(base+'/initial-evidence',headers=outsider,json=evidence).status_code==404
    assert client.get(base+'/materials',headers=commander).json()['initial_evidence'][0]['id']==initial.json()['id']
    investigator,_,investigator_officer=officer_account(client,db,'INVESTIGATING_OFFICER',station)
    unassigned,_,_=officer_account(client,db,'INVESTIGATING_OFFICER',station)
    docket=accepted['docket_id']
    assert client.post(f'/api/v1/dockets/{docket}/approvals',headers=commander,json={'decision':'APPROVED'}).status_code==201
    assert client.post(base+'/initial-evidence',headers=charge,json=evidence).status_code==409
    assert client.post(base+'/statements',headers=charge,json={'statement_text':'Too late','expected_version':2}).status_code==409
    assert client.post(f'/api/v1/dockets/{docket}/assignments',headers=commander,json={'investigating_officer_id':str(investigator_officer.id),'reason':'Investigate'}).status_code==201
    assert client.post(base+'/witnesses',headers=unassigned,json={'first_name':'X','last_name':'Y'}).status_code==404
    assert client.post(base+'/statements',headers=investigator,json={'statement_text':'Follow-up account','expected_version':2}).status_code==201
    assert client.get(f'/api/v1/dockets/{docket}/evidence',headers=investigator).json()[0]['id']==initial.json()['id']
    db.execute(text('SET LOCAL ROLE saps_api'));db.commit()
    with pytest.raises(DBAPIError):
        with db.begin_nested():db.execute(ComplaintStatement.__table__.update().where(ComplaintStatement.id==uuid.UUID(first['id'])).values(statement_text='tampered'))


def test_walk_in_failure_rolls_back_profile_statement_and_counter(intake,monkeypatch):
    client,db,station,charge,*_=intake
    before=db.scalar(select(func.count()).select_from(Complainant))
    def fail(*args,**kwargs):raise SQLAlchemyError('Notification failure')
    monkeypatch.setattr('app.modules.complaints.intake.notify_complainant',fail)
    assert client.post('/api/v1/complaints/in-station',headers=charge,json=walk_in()).status_code==503
    assert db.scalar(select(func.count()).select_from(Complainant))==before
    assert db.scalar(select(Complaint.id).where(Complaint.station_id==station.id)) is None
    assert db.scalar(select(IdentifierCounter.last_value).where(IdentifierCounter.station_id==station.id)) is None


def test_legacy_accepted_complaint_compatibility_is_idempotent(intake):
    client,db,station,charge,officer,*_=intake
    from app.modules.refusals.models import ComplaintDecision
    row=complaint_at_station(client,db,station)
    row.status='ACCEPTED'
    db.add(ComplaintDecision(complaint_id=row.id,decided_by_officer_id=officer.id,decision='ACCEPTED'))
    db.commit()
    url=f'/api/v1/complaints/{row.id}/dockets'
    first=client.post(url,headers=charge);second=client.post(url,headers=charge)
    assert first.status_code==second.status_code==200
    assert first.json()['id']==second.json()['id']
    assert db.scalar(select(IdentifierCounter.last_value).where(IdentifierCounter.station_id==station.id,IdentifierCounter.counter_type=='CAS'))==1
