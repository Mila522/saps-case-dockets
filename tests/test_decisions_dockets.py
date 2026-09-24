"""Collaborator B workflow tests: decisions, escalation, docket creation and approval."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy import select

from test_authentication import authorization, enroll
from test_complaint_tracking import account, complaint
from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.modules.access.models import Role, UserRole
from app.modules.audit.models import AuditLog
from app.modules.authentication.security import decode_access_token
from app.modules.dockets.models import Docket, DocketApproval
from app.modules.investigations.models import DocketStatusHistory
from app.modules.refusals.models import ComplaintDecision, RefusalEscalation, RefusalReason
from app.modules.stations.models import Officer, Station
from app.modules.system.models import IdentifierCounter


@pytest.fixture
def workflow_context():
    """Owner connection permits isolated fixture creation; every write is rolled back."""
    owner_engine = create_engine(settings.migration_database_url, hide_parameters=True)
    connection = owner_engine.connect()
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode='create_savepoint', expire_on_commit=False)

    def override_db():
        yield db

    old_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            yield client, db
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)
        db.close()
        outer.rollback()
        connection.close()
        owner_engine.dispose()


def officer_account(client, db, role_code, station):
    _, _, tokens = enroll(client)
    headers = authorization(tokens)
    user_id = uuid.UUID(decode_access_token(tokens['access_token'])['sub'])
    role_id = db.scalar(select(Role.id).where(Role.code == role_code))
    db.execute(UserRole.__table__.delete().where(UserRole.user_id == user_id))
    db.add(UserRole(user_id=user_id, role_id=role_id))
    officer = Officer(user_id=user_id, station_id=station.id,
                      service_number='S-' + uuid.uuid4().hex, rank='Test')
    db.add(officer)
    db.commit()
    return headers, user_id, officer


def complaint_at_station(client, db, station):
    _, owner, _ = account(client, db)
    row = complaint(db, owner)
    row.station_id = station.id
    db.commit()
    return row


def test_acceptance_creates_numbered_docket_history_and_audits(workflow_context):
    client, db = workflow_context
    station = Station(station_code='DEC-' + uuid.uuid4().hex[:8], name='Decision', province='Test')
    db.add(station)
    db.commit()
    headers, user_id, officer = officer_account(client, db, 'CHARGE_OFFICER', station)
    row = complaint_at_station(client, db, station)
    reasons = client.get('/api/v1/refusal-reasons', headers=headers)
    assert reasons.status_code == 200 and len(reasons.json()) == 7
    assert reasons.headers['cache-control'] == 'no-store'

    response = client.post(f'/api/v1/complaints/{row.id}/decisions',
                           json={'decision': 'ACCEPTED'}, headers=headers)
    assert response.status_code == 201
    body = response.json()
    assert body['complaint_status'] == 'ACCEPTED'
    assert db.scalar(select(Docket.id).where(Docket.complaint_id == row.id)) is None
    opened = client.post(f'/api/v1/complaints/{row.id}/dockets', headers=headers)
    assert opened.status_code == 201
    assert opened.json()['cas_number'].endswith('-000001')
    docket = db.get(Docket, uuid.UUID(opened.json()['id']))
    assert docket.complaint_id == row.id and docket.opened_by_officer_id == officer.id
    assert docket.status == 'PENDING_APPROVAL'
    assert db.scalar(select(IdentifierCounter.last_value).where(
        IdentifierCounter.counter_type == 'CAS', IdentifierCounter.station_id == station.id)) == 1
    history = db.scalar(select(DocketStatusHistory).where(DocketStatusHistory.docket_id == docket.id))
    assert history.from_status is None and history.to_status == 'PENDING_APPROVAL'
    actions = set(db.scalars(select(AuditLog.action).where(AuditLog.actor_user_id == user_id)).all())
    assert {'complaint.decide.accepted', 'docket.create'} <= actions


def test_refusal_policy_notes_station_scope_and_escalations(workflow_context):
    client, db = workflow_context
    first = Station(station_code='REF-' + uuid.uuid4().hex[:8], name='First', province='Test')
    second = Station(station_code='OTH-' + uuid.uuid4().hex[:8], name='Other', province='Test')
    db.add_all([first, second])
    db.commit()
    headers, _, _ = officer_account(client, db, 'CHARGE_OFFICER', first)
    foreign = complaint_at_station(client, db, second)
    assert client.post(f'/api/v1/complaints/{foreign.id}/decisions',
                       json={'decision': 'ACCEPTED'}, headers=headers).status_code == 404

    row = complaint_at_station(client, db, first)
    non_compliant = db.scalar(select(RefusalReason).where(RefusalReason.code == 'SUSPECT_UNKNOWN'))
    response = client.post(f'/api/v1/complaints/{row.id}/decisions', json={
        'decision': 'REFUSED', 'refusal_reason_id': str(non_compliant.id)}, headers=headers)
    assert response.status_code == 201 and response.json()['complaint_status'] == 'ESCALATED'
    targets = set(db.scalars(select(RefusalEscalation.target).where(
        RefusalEscalation.complaint_id == row.id)).all())
    assert targets == {'STATION_COMMANDER', 'NCC'}

    notes_required = db.scalar(select(RefusalReason).where(
        RefusalReason.code == 'DUPLICATE_COMPLAINT'))
    second_row = complaint_at_station(client, db, first)
    denied = client.post(f'/api/v1/complaints/{second_row.id}/decisions', json={
        'decision': 'REFUSED', 'refusal_reason_id': str(notes_required.id)}, headers=headers)
    assert denied.status_code == 422
    assert db.scalar(select(ComplaintDecision.id).where(
        ComplaintDecision.complaint_id == second_row.id)) is None
    accepted = client.post(f'/api/v1/complaints/{second_row.id}/decisions', json={
        'decision': 'REFUSED', 'refusal_reason_id': str(notes_required.id),
        'officer_notes': 'Duplicate of an existing complaint'}, headers=headers)
    assert accepted.status_code == 201 and accepted.json()['complaint_status'] == 'REFUSED'


def test_commander_approval_is_station_scoped_and_historical(workflow_context):
    client, db = workflow_context
    station = Station(station_code='APP-' + uuid.uuid4().hex[:8], name='Approval', province='Test')
    other = Station(station_code='NO-' + uuid.uuid4().hex[:8], name='Other', province='Test')
    db.add_all([station, other])
    db.commit()
    charge_headers, _, _ = officer_account(client, db, 'CHARGE_OFFICER', station)
    row = complaint_at_station(client, db, station)
    client.post(f'/api/v1/complaints/{row.id}/decisions',
                json={'decision': 'ACCEPTED'}, headers=charge_headers)
    created = client.post(f'/api/v1/complaints/{row.id}/dockets', headers=charge_headers).json()
    docket_id = created['id']
    commander_headers, commander_id, _ = officer_account(client, db, 'STATION_COMMANDER', station)
    other_headers, _, _ = officer_account(client, db, 'STATION_COMMANDER', other)
    assert client.get(f'/api/v1/dockets/{docket_id}', headers=other_headers).status_code == 404
    returned = client.post(f'/api/v1/dockets/{docket_id}/approvals', json={
        'decision': 'RETURNED_FOR_CORRECTION', 'notes': 'Correct the cover sheet'},
        headers=commander_headers)
    assert returned.status_code == 201 and returned.json()['docket_status'] == 'PENDING_APPROVAL'
    response = client.post(f'/api/v1/dockets/{docket_id}/approvals',
                           json={'decision': 'APPROVED', 'notes': 'Reviewed'},
                           headers=commander_headers)
    assert response.status_code == 201 and response.json()['docket_status'] == 'APPROVED'
    assert response.json()['decision_sequence'] == 2
    assert db.scalar(select(DocketApproval.id).where(
        DocketApproval.docket_id == uuid.UUID(docket_id))) is not None
    history = db.scalars(select(DocketStatusHistory).where(
        DocketStatusHistory.docket_id == uuid.UUID(docket_id))).all()
    assert [(item.from_status, item.to_status) for item in history] == [
        (None, 'PENDING_APPROVAL'), ('PENDING_APPROVAL', 'APPROVED')]
    audit = db.scalar(select(AuditLog).where(
        AuditLog.actor_user_id == commander_id, AuditLog.action == 'docket.review.approved'))
    assert audit and audit.station_id == station.id
    assert client.post(f'/api/v1/dockets/{docket_id}/approvals',
                       json={'decision': 'APPROVED'}, headers=commander_headers).status_code == 409


def test_escalation_visibility_and_state_changes_follow_target_scope(workflow_context):
    client, db = workflow_context
    station = Station(station_code='ESC-' + uuid.uuid4().hex[:8], name='Escalation', province='Test')
    other = Station(station_code='ESC2-' + uuid.uuid4().hex[:8], name='Other', province='Test')
    db.add_all([station, other])
    db.commit()
    charge_headers, _, _ = officer_account(client, db, 'CHARGE_OFFICER', station)
    row = complaint_at_station(client, db, station)
    reason = db.scalar(select(RefusalReason).where(RefusalReason.code == 'SUSPECT_UNKNOWN'))
    client.post(f'/api/v1/complaints/{row.id}/decisions', json={
        'decision': 'REFUSED', 'refusal_reason_id': str(reason.id)}, headers=charge_headers)

    commander_headers, _, _ = officer_account(client, db, 'STATION_COMMANDER', station)
    other_headers, _, _ = officer_account(client, db, 'STATION_COMMANDER', other)
    visible = client.get('/api/v1/refusal-escalations', headers=commander_headers)
    assert visible.status_code == 200
    assert {item['target'] for item in visible.json()} == {'STATION_COMMANDER'}
    assert client.get('/api/v1/refusal-escalations', headers=other_headers).json() == []
    ncc_headers, _, _ = officer_account(client, db, 'NCC_OFFICER', other)
    ncc_visible = client.get('/api/v1/refusal-escalations', headers=ncc_headers)
    assert ncc_visible.status_code == 200
    assert {item['target'] for item in ncc_visible.json()} == {'NCC'}
    escalation_id = visible.json()[0]['id']
    acknowledged = client.post(
        f'/api/v1/refusal-escalations/{escalation_id}/acknowledge', headers=commander_headers)
    assert acknowledged.status_code == 200 and acknowledged.json()['status'] == 'ACKNOWLEDGED'
    resolved = client.post(f'/api/v1/refusal-escalations/{escalation_id}/resolve',
                           json={'resolution_notes': 'Commander reviewed the refusal'},
                           headers=commander_headers)
    assert resolved.status_code == 200 and resolved.json()['status'] == 'RESOLVED'
