"""Real PostgreSQL workflows and authenticated case links; SMTP is always mocked."""
import importlib.util
import uuid
from unittest.mock import MagicMock
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from pydantic import SecretStr
from test_decisions_dockets import workflow_context, officer_account
from test_complaint_tracking import account, complaint
from test_complaint_registration import payload
from test_intake_corrections import walk_in
from app.core.config import settings
from app.modules.authentication import mail
from app.modules.complainants.models import Complainant
from app.modules.complaints.models import Complaint
from app.modules.communications import case_email as delivery
from app.modules.communications.email_models import CaseEmailIntent
from app.modules.communications.models import Notification, NotificationAttempt
from app.modules.refusals.models import RefusalReason
from app.modules.stations.models import Station

BASE = '/api/v1/case-email'


@pytest.fixture(autouse=True)
def isolated_outbox(monkeypatch):
    # Local development may already contain real pending messages. Tests must
    # neither inspect nor process those, even through a mocked SMTP adapter.
    identities = set()
    enqueue, process = delivery.enqueue, delivery.process_one
    def capture(*args, **kwargs):
        identity = enqueue(*args, **kwargs)
        identities.add(identity)
        return identity
    monkeypatch.setattr(delivery, 'enqueue', capture)
    monkeypatch.setattr(delivery, 'process_one', lambda db: process(db, notification_ids=identities))


def emails(db, complaint_id):
    return list(db.scalars(select(Notification).join(CaseEmailIntent).where(
        Notification.complaint_id == complaint_id).order_by(Notification.created_at, Notification.id)))


@pytest.fixture
def sent(monkeypatch):
    messages = []
    def send(*args):
        messages.append(args)
        return 'ACCEPTED', 'SMTP_ACCEPTED'
    monkeypatch.setattr(mail, 'send_case_message', send)
    monkeypatch.setattr(settings, 'case_portal_url', 'http://127.0.0.1:8000/portal/')
    return messages


def test_all_case_event_hooks_and_approval_boundary(workflow_context, sent):
    client, db = workflow_context
    headers, owner, _ = account(client, db)
    data = payload(db)
    station = db.get(Station, uuid.UUID(data['station_id']))
    charge, _, _ = officer_account(client, db, 'CHARGE_OFFICER', station)
    commander, _, _ = officer_account(client, db, 'STATION_COMMANDER', station)
    investigator, _, officer = officer_account(client, db, 'INVESTIGATING_OFFICER', station)
    # Exercise new tables with the actual runtime grants too.
    db.execute(text('SET LOCAL ROLE saps_api'))
    response = client.post('/api/v1/complaints', headers=headers, json=data)
    assert response.status_code == 201, response.text
    identity = uuid.UUID(response.json()['id'])
    row = db.get(Complaint, identity)
    assert [m.event_type for m in emails(db, identity)] == ['complaint.registered']
    assert not sent
    submitted = emails(db, identity)[0]
    assert submitted.status == 'PENDING'
    assert row.reference_number in submitted.message and station.name in submitted.message
    assert 'SUBMITTED' in submitted.message and data['incident_location'] not in submitted.message
    decision = client.post(f'/api/v1/complaints/{identity}/decisions', headers=charge, json={'decision':'ACCEPTED'})
    assert decision.status_code == 201
    docket_id = decision.json()['docket_id']
    assert {m.event_type for m in emails(db, identity)} == {'complaint.registered', 'docket.created'}
    created = next(m for m in emails(db, identity) if m.event_type == 'docket.created')
    assert 'approval is pending' in created.message and 'PENDING_APPROVAL' in created.message
    assert decision.json()['cas_number'] in created.message
    assert client.post(f'/api/v1/complaints/{identity}/dockets',headers=charge).status_code == 200
    assert len(emails(db, identity)) == 2
    assert client.post(f'/api/v1/dockets/{docket_id}/approvals', headers=commander,
        json={'decision':'APPROVED'}).status_code == 201
    assert client.post(f'/api/v1/dockets/{docket_id}/assignments', headers=commander,
        json={'investigating_officer_id':str(officer.id),'reason':'Investigate'}).status_code == 201
    assert client.post(f'/api/v1/dockets/{docket_id}/feedback', headers=investigator,
        json={'feedback_type':'GENERAL','subject':'Official update','message':'Published official detail'}).status_code == 201
    rows = emails(db, identity)
    assert {m.event_type for m in rows} == {'complaint.registered','docket.created','docket.approved','docket.assigned','feedback.published'}
    assert all('Published official detail' not in m.message and data['incident_description'] not in m.message for m in rows)
    changed=client.post(f'/api/v1/dockets/{docket_id}/status',headers=investigator,
        json={'expected_status':'ACTIVE','status':'ON_HOLD','reason':'PRIVATE STATUS REASON'})
    assert changed.status_code==200
    assert client.post(f'/api/v1/dockets/{docket_id}/status',headers=investigator,
        json={'expected_status':'ACTIVE','status':'ON_HOLD','reason':'PRIVATE STATUS REASON'}).status_code==409
    rows=emails(db,identity)
    status_mail=next(m for m in rows if m.event_type=='case.on_hold')
    assert 'ON_HOLD' in status_mail.message and 'PRIVATE STATUS REASON' not in status_mail.message
    for message in rows:
        assert delivery.tracking_link(identity) in message.message
        assert 'Sign in' in message.message
        if message.docket_id: assert decision.json()['cas_number'] in message.message
    for _ in rows:
        assert delivery.process_one(db)
    assert len(sent) == 6 and not delivery.process_one(db)
    assert all(db.get(Notification, m.id).status == 'SENT' and m.delivered_at is None for m in rows)
    assert all(address == db.get(Complainant, owner).email for address, *_ in sent)
    with pytest.raises(ValueError):
        delivery.retry(db, submitted.id)
    # Refusal mail uses only a fixed public outcome, never officer free text.
    refused = client.post('/api/v1/complaints', headers=headers, json=data).json()
    reason = db.scalar(select(RefusalReason).where(RefusalReason.code == 'SUSPECT_UNKNOWN'))
    response = client.post(f"/api/v1/complaints/{refused['id']}/decisions", headers=charge,
        json={'decision':'REFUSED','refusal_reason_id':str(reason.id),'officer_notes':'PRIVATE NOTES'})
    assert response.status_code == 201
    refusal = next(m for m in emails(db, uuid.UUID(refused['id'])) if m.event_type == 'complaint.refused')
    assert 'escalated for review' in refusal.message and 'PRIVATE NOTES' not in refusal.message


def test_walkin_is_not_claimed_verified_and_status_is_station_scoped(workflow_context, sent):
    client, db = workflow_context
    headers, owner, _ = account(client, db)
    station = db.get(Station, uuid.UUID(payload(db)['station_id']))
    charge, _, _ = officer_account(client, db, 'CHARGE_OFFICER', station)
    other = db.get(Station, uuid.UUID(payload(db)['station_id']))
    foreign, _, _ = officer_account(client, db, 'CHARGE_OFFICER', other)
    data = walk_in(station_id=str(station.id))
    data['complainant']['email'] = db.get(Complainant, owner).email
    result = client.post('/api/v1/complaints/in-station', headers=charge, json=data)
    assert result.status_code == 201
    identity = uuid.UUID(result.json()['id'])
    row = emails(db, identity)[0]
    assert row.status == 'FAILED' and row.destination_encrypted is None
    assert not delivery.process_one(db) and not sent
    status = client.get(f'{BASE}/complaints/{identity}/status', headers=charge)
    assert status.status_code == 200 and status.json()['email_verified'] is False
    assert status.json()['deliveries'][0]['reason'] == 'EMAIL_NOT_VERIFIED'
    assert client.get(f'{BASE}/complaints/{identity}/status', headers=foreign).status_code == 404
    assert client.get(f'{BASE}/complaints/{identity}/status', headers=headers).status_code == 403


@pytest.mark.parametrize('state,reason,retryable', [
    ('BLOCKED','SMTP_NOT_CONFIGURED',True), ('BLOCKED','SMTP_CONNECTION_FAILED',True),
    ('REJECTED','SMTP_REJECTED',True), ('UNKNOWN','SEND_OUTCOME_UNKNOWN',False)])
def test_worker_failures_retry_and_append_only_attempts(workflow_context, sent, monkeypatch, state, reason, retryable):
    client, db = workflow_context
    _, owner, _ = account(client, db)
    row = complaint(db, owner)
    identity = delivery.enqueue(db, row, None, 'complaint.registered', row.id)
    assert delivery.enqueue(db, row, None, 'complaint.registered', row.id) == identity
    db.commit()
    monkeypatch.setattr(mail, 'send_case_message', lambda *args:(state,reason))
    assert delivery.process_one(db)
    assert db.get(Complaint, row.id)
    assert not delivery.process_one(db)
    if retryable:
        delivery.retry(db, identity)
        monkeypatch.setattr(mail, 'send_case_message', lambda *args:('ACCEPTED','SMTP_ACCEPTED'))
        assert delivery.process_one(db)
        assert db.get(Notification, identity).status == 'SENT'
    else:
        with pytest.raises(ValueError):
            delivery.retry(db, identity)
        assert db.get(Notification, identity).status == 'PROCESSING'
    attempts = list(db.scalars(select(NotificationAttempt).where(NotificationAttempt.notification_id == identity)
        .order_by(NotificationAttempt.attempt_number)))
    assert len(attempts) == (2 if retryable else 1)
    assert attempts[0].error_code == reason
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(NotificationAttempt.__table__.update().where(NotificationAttempt.id == attempts[0].id).values(error_code='changed'))


def test_queue_rollback_and_email_change_block_send(workflow_context, sent):
    client, db = workflow_context
    _, owner, _ = account(client, db)
    row = complaint(db, owner); db.commit()
    identity = delivery.enqueue(db,row,None,'complaint.registered',row.id)
    db.flush(); db.rollback()
    assert db.get(Notification,identity) is None
    identity = delivery.enqueue(db,row,None,'complaint.registered',row.id); db.commit()
    db.get(Complainant,owner).email = 'unverified@example.invalid'; db.commit()
    assert delivery.process_one(db) and not sent
    assert db.get(Notification,identity).status == 'FAILED'


def test_email_link_requires_login_and_owner_authorization(workflow_context, sent):
    client, db = workflow_context
    headers, owner, _ = account(client,db)
    other, _, _ = account(client,db)
    row=complaint(db,owner); db.commit()
    link=delivery.tracking_link(row.id)
    assert link == f'http://127.0.0.1:8000/portal/#complaint={row.id}'
    assert client.get('/portal/').status_code == 200  # Public shell is only sign-in UI.
    endpoint=f'/api/v1/complaints/{row.id}/tracking'
    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint,headers=other).status_code == 404
    assert client.get(endpoint,headers=headers).status_code == 200
    for path in ['/api/v1/case-email/tracking/request','/api/v1/case-email/tracking/verify',
                 '/api/v1/sms/tracking/request']:
        assert client.post(path,json={'reference_number':row.reference_number}).status_code == 404


def test_legacy_pending_email_link_is_replaced_before_delivery(workflow_context,sent):
    client,db=workflow_context
    _,owner,_=account(client,db)
    row=complaint(db,owner)
    identity=delivery.enqueue(db,row,None,'complaint.registered',row.id)
    notification=db.get(Notification,identity)
    notification.message='Complaint submitted.\n\nView updates securely: http://127.0.0.1:8000/portal/tracking.html'
    db.commit()
    assert delivery.process_one(db)
    assert '/tracking.html' not in sent[0][2]
    assert delivery.tracking_link(row.id) in sent[0][2]


@pytest.mark.parametrize('failure,expected',[(None,'ACCEPTED'),('connect','BLOCKED'),('data','UNKNOWN'),('reject','REJECTED')])
def test_real_smtp_adapter_with_mock_transport(monkeypatch,failure,expected):
    spec=importlib.util.spec_from_file_location('case_smtp_test',mail.__file__)
    adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
    for key,value in {'smtp_host':'smtp.example.invalid','smtp_use_starttls':True,
        'smtp_username':SecretStr('test'),'smtp_password':SecretStr('test'),'email_from':'sender@example.invalid'}.items():
        monkeypatch.setattr(settings,key,value)
    smtp=MagicMock();smtp.__enter__.return_value=smtp;smtp.send_message.return_value={}
    if failure=='data': smtp.send_message.side_effect=OSError('private provider detail')
    if failure=='reject': smtp.send_message.side_effect=adapter.smtplib.SMTPDataError(550,b'private provider detail')
    factory=MagicMock(return_value=smtp)
    if failure=='connect':factory.side_effect=OSError('private provider detail')
    monkeypatch.setattr(adapter.smtplib,'SMTP',factory)
    state,reason=adapter.send_case_message('recipient@example.invalid','Subject','Body',uuid.uuid4())
    assert state==expected and 'private' not in reason
    if failure!='connect':
        smtp.starttls.assert_called_once()
        assert smtp.send_message.call_args.args[0]['To']=='recipient@example.invalid'
