"""Real A -> B -> C API flow; outer transaction rolls back every record."""
import json
import re
import uuid

import pytest
from sqlalchemy import select, text, func
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from test_decisions_dockets import workflow_context, officer_account
from test_complaint_tracking import account
from app.core.config import settings
from app.modules.audit.models import AuditLog
from app.modules.dockets.models import Docket
from app.modules.evidence.models import EvidenceItem, EvidenceFile, EvidenceCustodyEvent
from app.modules.investigations.models import InvestigationNote, DocketStatusHistory
from app.modules.investigations.service import InvestigationService
from app.modules.refusals.models import ComplaintDecision
from app.modules.stations.models import Station


def test_end_to_end_case_workflow(workflow_context, tmp_path, monkeypatch):
    client, db = workflow_context
    monkeypatch.setattr(settings, 'evidence_storage_path', tmp_path / 'files')
    station = Station(station_code='E2E-' + uuid.uuid4().hex[:8], name='Demo station', province='Test')
    foreign = Station(station_code='OUT-' + uuid.uuid4().hex[:8], name='Other station', province='Test')
    db.add_all([station, foreign])
    db.commit()
    complainant, _, _ = account(client, db)
    other_complainant, _, _ = account(client, db)
    charge, charge_id, _ = officer_account(client, db, 'CHARGE_OFFICER', station)
    outsider, _, _ = officer_account(client, db, 'CHARGE_OFFICER', foreign)
    commander, _, _ = officer_account(client, db, 'STATION_COMMANDER', station)
    foreign_commander, _, _ = officer_account(client, db, 'STATION_COMMANDER', foreign)
    investigator, investigator_id, officer = officer_account(client, db, 'INVESTIGATING_OFFICER', station)
    unassigned, _, destination = officer_account(client, db, 'INVESTIGATING_OFFICER', station)
    # Provisioning is a fixture responsibility; all workflow routes use runtime privileges.
    db.execute(text('SET LOCAL ROLE saps_api'))
    db.commit()
    submitted = client.post('/api/v1/complaints', headers=complainant, json={
        'station_id': str(station.id), 'crime_category': 'Theft',
        'incident_description': 'PRIVATE-STATEMENT-CONTENT',
        'incident_location': 'Demo location', 'incident_province': 'Test'})
    assert submitted.status_code == 201, submitted.text
    complaint = submitted.json()['id']
    assert re.fullmatch(rf'CMP-{station.station_code}-\d{{4}}-\d{{6,}}', submitted.json()['reference_number'])
    assert client.get(f'/api/v1/complaints/{complaint}/tracking', headers=other_complainant).status_code == 404
    review = f'/api/v1/complaints/{complaint}/review'
    assert client.post(review, headers=outsider).status_code == 404
    assert client.post(review, headers=complainant).status_code == 403
    reviewed = client.post(review, headers=charge)
    assert reviewed.status_code == 200 and reviewed.json()['status'] == 'UNDER_REVIEW'
    assert reviewed.json()['review_started_at']
    assert client.post(review, headers=charge).status_code == 409
    accepted = client.post(f'/api/v1/complaints/{complaint}/decisions', headers=charge, json={'decision': 'ACCEPTED'})
    assert accepted.status_code == 201
    decision = db.scalar(select(ComplaintDecision).where(ComplaintDecision.complaint_id == uuid.UUID(complaint)))
    assert decision.decision == 'ACCEPTED'
    opened = client.post(f'/api/v1/complaints/{complaint}/dockets', headers=charge)
    assert opened.status_code == 201
    docket = opened.json()['id']
    assert re.fullmatch(rf'CAS-{station.station_code}-\d{{4}}-\d{{6,}}', opened.json()['cas_number'])
    assert client.post(f'/api/v1/complaints/{complaint}/dockets', headers=charge).status_code == 409
    assert client.get(f'/api/v1/dockets/{docket}', headers=foreign_commander).status_code == 404
    assert client.post(f'/api/v1/dockets/{docket}/approvals', headers=commander,
        json={'decision': 'APPROVED'}).status_code == 201
    assert client.post(f'/api/v1/dockets/{docket}/assignments', headers=commander, json={
        'investigating_officer_id': str(officer.id), 'reason': 'Investigate'}).status_code == 201
    assert client.get('/api/v1/investigations/dockets', headers=investigator).json()[0]['id'] == docket
    assert client.get(f'/api/v1/investigations/dockets/{docket}', headers=investigator).status_code == 200
    note = client.post(f'/api/v1/dockets/{docket}/notes', headers=investigator,
        json={'content': 'PRIVATE-NOTE-CONTENT', 'is_sensitive': True, 'note_type': 'INTERVIEW'})
    assert note.status_code == 201
    assert client.get(f'/api/v1/dockets/{docket}/notes', headers=complainant).status_code == 403
    status_data = {'status': 'ON_HOLD', 'expected_status': 'ACTIVE', 'reason': 'Await analysis'}
    assert client.post(f'/api/v1/dockets/{docket}/status', headers=unassigned, json=status_data).status_code == 404
    assert client.post(f'/api/v1/dockets/{docket}/status', headers=investigator, json=status_data).status_code == 200
    evidence_data = {'title': 'Recording', 'description': 'PRIVATE-EVIDENCE-CONTENT',
        'evidence_type': 'VIDEO', 'is_digital': True, 'storage_location': 'Locker A'}
    assert client.post(f'/api/v1/dockets/{docket}/evidence', headers=unassigned, json=evidence_data).status_code == 404
    registered = client.post(f'/api/v1/dockets/{docket}/evidence', headers=investigator, json=evidence_data)
    assert registered.status_code == 201
    item = registered.json()['id']
    assert re.fullmatch(rf'EVD-{station.station_code}-\d{{4}}-000001', registered.json()['evidence_reference'])
    initial = client.get(f'/api/v1/evidence/{item}/custody-events', headers=investigator).json()[0]
    assert initial['id'] == registered.json()['custody_version']
    assert initial['to_custodian_officer_id'] == registered.json()['current_custodian_officer_id']
    assert initial['to_location'] == registered.json()['current_storage_location']
    uploaded = client.post(f'/api/v1/evidence/{item}/files', headers=investigator,
        files={'file': ('demo.txt', b'private file bytes', 'text/plain')})
    assert uploaded.status_code == 201 and re.fullmatch('[0-9a-f]{64}', uploaded.json()['sha256_hash'])
    assert 'storage_key' not in uploaded.json()
    transfer_data = {'event_type': 'TRANSFERRED', 'expected_custody_event_id': initial['id'],
        'to_custodian_officer_id': str(destination.id), 'to_location': 'Locker B', 'notes': 'Signed handover'}
    transferred = client.post(f'/api/v1/evidence/{item}/custody-events', headers=investigator, json=transfer_data)
    assert transferred.status_code == 201
    current = client.get(f'/api/v1/evidence/{item}', headers=investigator).json()
    assert current['current_custodian_officer_id'] == str(destination.id)
    assert current['current_storage_location'] == 'Locker B'
    assert current['custody_version'] == transferred.json()['id']
    assert client.post(f'/api/v1/evidence/{item}/custody-events', headers=investigator, json=transfer_data).status_code == 409
    assert client.get(f'/api/v1/evidence/{item}/custody-events', headers=unassigned).status_code == 404
    # Verify real immutable rows, including both UPDATE and DELETE as runtime.
    for model, row_id, field, value in (
        (InvestigationNote, uuid.UUID(note.json()['id']), 'content', 'altered'),
        (EvidenceFile, uuid.UUID(uploaded.json()['id']), 'original_filename', 'altered'),
        (EvidenceCustodyEvent, uuid.UUID(transferred.json()['id']), 'event_notes', 'altered'),
    ):
        for statement in (model.__table__.update().where(model.id == row_id).values(**{field: value}),
                          model.__table__.delete().where(model.id == row_id)):
            with pytest.raises(DBAPIError):
                with db.begin_nested():
                    db.execute(statement)
    history = db.scalars(select(DocketStatusHistory).where(DocketStatusHistory.docket_id == uuid.UUID(docket))
        .order_by(DocketStatusHistory.changed_at)).all()
    assert [row.to_status for row in history] == ['PENDING_APPROVAL', 'APPROVED', 'ACTIVE', 'ON_HOLD']
    assert history[-1].changed_by_user_id == investigator_id
    for statement in (DocketStatusHistory.__table__.update().where(DocketStatusHistory.id == history[-1].id).values(change_reason='altered'),
                      DocketStatusHistory.__table__.delete().where(DocketStatusHistory.id == history[-1].id)):
        with pytest.raises(DBAPIError):
            with db.begin_nested():
                db.execute(statement)
    # An audit failure must undo the current status and its history together.
    def fail(*args, **kwargs):
        raise SQLAlchemyError('Simulated audit failure')
    with monkeypatch.context() as patch:
        patch.setattr(InvestigationService, 'audit', fail)
        result = client.post(f'/api/v1/dockets/{docket}/status', headers=investigator,
            json={'expected_status': 'ON_HOLD', 'status': 'ACTIVE', 'reason': 'Resume'})
    assert result.status_code == 503
    db.expire_all()
    assert db.get(Docket, uuid.UUID(docket)).status == 'ON_HOLD'
    assert db.scalar(select(func.count()).select_from(DocketStatusHistory).where(
        DocketStatusHistory.docket_id == uuid.UUID(docket))) == 4
    events = db.scalars(select(AuditLog).where(AuditLog.station_id == station.id)).all()
    assert {'complaint.review.start', 'complaint.decide.accepted', 'docket.create', 'docket.review.approved',
        'docket.assign', 'docket.view_assigned', 'case.add_note', 'case.update_status',
        'evidence.register', 'evidence.file.upload', 'evidence.custody.transferred',
        'evidence.custody.view', 'evidence.view'} <= {event.action for event in events}
    payloads = json.dumps([{'old': e.old_values, 'new': e.new_values, 'metadata': e.event_metadata} for e in events])
    assert all(value not in payloads for value in ['PRIVATE-', 'private file bytes', 'Bearer ',
        'password', 'refresh_token', 'secret_encrypted', 'identity_number', 'storage_key'])
