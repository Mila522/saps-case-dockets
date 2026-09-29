import re
import uuid
from datetime import timedelta
import pytest
from sqlalchemy import select, func
from test_decisions_dockets import workflow_context, officer_account
from test_complaint_tracking import account
from test_complaint_registration import payload
from test_intake_corrections import walk_in
from test_investigation_api import investigation, assign
from app.modules.authentication import mail
from app.modules.authentication.security import utcnow
from app.modules.complainants.models import Complainant
from app.modules.complaints.models import Complaint
from app.modules.complaints.intake_email_models import IntakeConsent
from app.modules.communications.models import CaseFeedback, Notification
from app.modules.communications import case_email
from app.modules.dockets.models import Docket
from app.modules.stations.models import Station


def test_assigned_contact_and_idempotent_invitation(investigation,monkeypatch):
    client,db,docket_id,commander,investigator,officer,other,*_=investigation
    assign(investigation)
    docket=db.get(Docket,uuid.UUID(docket_id))
    complaint=db.get(Complaint,docket.complaint_id)
    person=db.get(Complainant,complaint.complainant_id)
    person.address_line_1='Actual residential address';db.commit()
    path=f'/api/v1/investigations/dockets/{docket_id}'
    assert client.get(path+'/complainant',headers=other).status_code==404
    assert client.get(path+'/complainant').status_code==401
    result=client.get(path+'/complainant',headers=investigator)
    assert result.status_code==200 and result.json()['address_line_1']=='Actual residential address'
    assert 'identity_number_encrypted' not in result.json()
    data={'request_id':str(uuid.uuid4()),'purpose':'INTERVIEW','starts_at':(utcnow()+timedelta(days=2)).isoformat()}
    assert client.post(path+'/invitations',headers=other,json=data).status_code==404
    first=client.post(path+'/invitations',headers=investigator,json=data)
    assert first.status_code==201,first.text
    assert first.json()['delivery_status']=='PENDING'
    repeat=client.post(path+'/invitations',headers=investigator,json=data)
    assert repeat.json()==first.json()
    assert client.post(path+'/invitations',headers=investigator,json={**data,'purpose':'MEETING'}).status_code==409
    assert db.scalar(select(func.count()).select_from(Notification).where(Notification.docket_id==docket.id,
        Notification.event_type=='meeting.requested',Notification.channel=='EMAIL'))==1
    row=db.get(Notification,uuid.UUID(first.json()['notification_id']))
    assert 'interview' in row.message and 'SAST' in row.message and docket.cas_number in row.message
    assert 'Actual residential address' not in row.message
    assert case_email.tracking_link(complaint.id) in row.message
    assert client.post(f'/api/v1/dockets/{docket_id}/status',headers=investigator,
        json={'expected_status':'ACTIVE','status':'CLOSED','reason':'Finished'}).status_code==200
    assert client.post(path+'/invitations',headers=investigator,json={**data,'request_id':str(uuid.uuid4())}).status_code==409


def test_verified_consent_links_walkin_and_queues_emails(workflow_context,monkeypatch):
    client,db=workflow_context
    owner,person_id,_=account(client,db)
    stranger,_,_=account(client,db)
    person=db.get(Complainant,person_id)
    station=db.get(Station,uuid.UUID(payload(db)['station_id']))
    charge,_,_=officer_account(client,db,'CHARGE_OFFICER',station)
    captured=[]
    monkeypatch.setattr(mail,'send_case_message',lambda *args:(captured.append(args) or ('ACCEPTED','SMTP_ACCEPTED')))
    response=client.post('/api/v1/complaints/in-station/email-consent',headers=charge,json={'email':person.email})
    assert response.status_code==202
    code=re.search(r'\b\d{6}\b',captured[0][2]).group()
    consent=response.json()['challenge_id']
    assert code not in response.text
    data=walk_in(station_id=str(station.id),email_consent_id=consent,email_consent_code=code)
    data['complainant']['email']=person.email
    data['complainant']['address_line_1']='Residential address'
    created=client.post('/api/v1/complaints/in-station',headers=charge,json=data)
    assert created.status_code==201,created.text
    identity=created.json()['id']
    row=db.get(Complaint,uuid.UUID(identity))
    assert row.complainant_id==person_id and row.channel=='IN_STATION'
    assert row.incident_location=='Reported location'
    assert person.address_line_1=='Residential address'
    assert client.get(f'/api/v1/complaints/{identity}/tracking',headers=owner).status_code==200
    assert client.get(f'/api/v1/complaints/{identity}/tracking',headers=stranger).status_code==404
    assert client.post('/api/v1/complaints/in-station',headers=charge,json=data).status_code==400
    assert client.post(f'/api/v1/complaints/{identity}/decisions',headers=charge,json={'decision':'ACCEPTED'}).status_code==201
    email=db.scalar(select(Notification).where(Notification.complaint_id==row.id,Notification.channel=='EMAIL',Notification.event_type=='docket.created'))
    assert email.status=='PENDING' and 'approval is pending' in email.message
    assert case_email.tracking_link(row.id) in email.message
    updates=client.get(f'/api/v1/complaints/{identity}/updates',headers=owner)
    assert updates.status_code==200 and updates.json()['docket_status']=='PENDING_APPROVAL'
    assert client.get(f'/api/v1/complaints/{identity}/updates',headers=stranger).status_code==404
    assert client.get(f'/api/v1/complaints/{identity}/updates').status_code==401


def test_consent_failed_send_and_guess_limit_do_not_link(workflow_context,monkeypatch):
    client,db=workflow_context
    _,person_id,_=account(client,db)
    person=db.get(Complainant,person_id)
    station=db.get(Station,uuid.UUID(payload(db)['station_id']))
    charge,_,_=officer_account(client,db,'CHARGE_OFFICER',station)
    captured=[]
    monkeypatch.setattr(mail,'send_case_message',lambda *args:(captured.append(args) or ('UNKNOWN','SEND_OUTCOME_UNKNOWN')))
    response=client.post('/api/v1/complaints/in-station/email-consent',headers=charge,json={'email':person.email})
    identity=response.json()['challenge_id']
    code=re.search(r'\b\d{6}\b',captured[0][2]).group()
    data=walk_in(email_consent_id=identity,email_consent_code=code)
    data['complainant']['email']=person.email
    assert client.post('/api/v1/complaints/in-station',headers=charge,json=data).status_code==400
    row=db.get(IntakeConsent,uuid.UUID(identity));row.delivery_state='ACCEPTED';db.commit()
    wrong=f'{(int(code)+1)%1000000:06d}'
    for _ in range(5):
        assert client.post('/api/v1/complaints/in-station',headers=charge,json={**data,'email_consent_code':wrong}).status_code==400
    assert client.post('/api/v1/complaints/in-station',headers=charge,json=data).status_code==400
    assert row.attempts>=6 and row.used_at is None
    assert client.post('/api/v1/complaints/in-station/email-consent',headers=charge,json={'email':person.email}).status_code==429
