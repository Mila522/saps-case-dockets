"""Collaborator C integrates with A/B; database writes roll back and files use tmp_path."""
import hashlib
import uuid

import pytest
from sqlalchemy import select, text, func
from sqlalchemy.exc import SQLAlchemyError

from test_decisions_dockets import workflow_context, officer_account, complaint_at_station
from app.core.config import settings
from app.modules.audit.models import AuditLog
from app.modules.dockets.models import Docket
from app.modules.evidence.models import EvidenceItem, EvidenceFile, EvidenceCustodyEvent
from app.modules.evidence.storage import EvidenceStorage
from app.modules.investigations.models import CaseAssignment, InvestigationNote, DocketStatusHistory
from app.modules.investigations.service import InvestigationService
from app.modules.stations.models import Station
from app.modules.system.models import IdentifierCounter


@pytest.fixture
def investigation(workflow_context, tmp_path, monkeypatch):
    client, db = workflow_context
    monkeypatch.setattr(settings, 'evidence_storage_path', tmp_path / 'evidence')
    station = Station(station_code='C-' + uuid.uuid4().hex[:8], name='Investigation', province='Test')
    db.add(station)
    db.commit()
    charge, _, _ = officer_account(client, db, 'CHARGE_OFFICER', station)
    commander, _, _ = officer_account(client, db, 'STATION_COMMANDER', station)
    investigator, _, officer = officer_account(client, db, 'INVESTIGATING_OFFICER', station)
    second, _, other = officer_account(client, db, 'INVESTIGATING_OFFICER', station)
    complaint = complaint_at_station(client, db, station)
    assert client.post(f'/api/v1/complaints/{complaint.id}/decisions',
        json={'decision': 'ACCEPTED'}, headers=charge).status_code == 201
    response = client.post(f'/api/v1/complaints/{complaint.id}/dockets', headers=charge)
    assert response.status_code == 201
    docket_id = response.json()['id']
    assert client.post(f'/api/v1/dockets/{docket_id}/approvals',
        json={'decision': 'APPROVED'}, headers=commander).status_code == 201
    # Exercise C through the real restricted runtime role, not the fixture owner.
    db.execute(text('SET LOCAL ROLE saps_api'))
    db.commit()
    return client, db, docket_id, commander, investigator, officer, second, other, station


def assign(ctx, officer=None):
    client, _, docket, commander, _, first, *_ = ctx
    result = client.post(f'/api/v1/dockets/{docket}/assignments', headers=commander,
        json={'investigating_officer_id': str((officer or first).id), 'reason': 'Investigate case'})
    assert result.status_code == 201, result.text
    return result.json()


def evidence(ctx):
    client, _, docket, _, headers, *_ = ctx
    response = client.post(f'/api/v1/dockets/{docket}/evidence', headers=headers, json={
        'title': 'Camera recording', 'description': 'Private evidence narrative',
        'evidence_type': 'VIDEO', 'is_digital': True, 'storage_location': 'Secure locker'})
    assert response.status_code == 201, response.text
    return response.json()['id']


def test_assignment_history_and_immediate_access_revocation(investigation):
    client, db, docket, commander, first, officer, second, other, _ = investigation
    first_assignment = assign(investigation)
    assert db.get(Docket, uuid.UUID(docket)).status == 'ACTIVE'
    assert client.get('/api/v1/investigations/dockets', headers=first).json()[0]['id'] == docket
    assert client.get(f'/api/v1/investigations/dockets/{docket}', headers=second).status_code == 404
    assert client.post(f'/api/v1/dockets/{docket}/assignments', headers=commander, json={
        'investigating_officer_id': str(officer.id), 'reason': 'Duplicate'}).status_code == 409
    assign(investigation, other)
    db.expire_all()
    assert db.get(CaseAssignment, uuid.UUID(first_assignment['id'])).unassigned_at is not None
    assert client.get(f'/api/v1/dockets/{docket}/notes', headers=first).status_code == 404
    assert client.get(f'/api/v1/investigations/dockets/{docket}', headers=second).status_code == 200
    assert len(client.get(f'/api/v1/dockets/{docket}/assignments', headers=commander).json()) == 2
    assert client.post(f'/api/v1/dockets/{docket}/assignments/end', headers=commander,
        json={'reason': 'Await replacement'}).status_code == 200
    assert client.get(f'/api/v1/investigations/dockets/{docket}', headers=second).status_code == 404


def test_notes_status_history_and_closed_case_guards(investigation):
    client, db, docket, commander, headers, *_ = investigation
    assign(investigation)
    note = client.post(f'/api/v1/dockets/{docket}/notes', headers=headers,
        json={'content': 'Private interview', 'note_type': 'INTERVIEW', 'is_sensitive': True})
    assert note.status_code == 201 and note.headers['cache-control'] == 'no-store'
    assert client.post(f'/api/v1/dockets/{docket}/notes', headers=headers,
        json={'content': '  '}).status_code == 422
    assert client.get(f'/api/v1/dockets/{docket}/notes?limit=1', headers=headers).json()[0]['id'] == note.json()['id']
    assert client.get(f'/api/v1/dockets/{docket}/notes?limit=101', headers=headers).status_code == 422
    for expected, status in (('ACTIVE', 'ON_HOLD'), ('ON_HOLD', 'ACTIVE'), ('ACTIVE', 'CLOSED')):
        response = client.post(f'/api/v1/dockets/{docket}/status', headers=headers,
            json={'status': status, 'expected_status': expected, 'reason': 'Documented transition'})
        assert response.status_code == 200, response.text
    assert response.json()['closed_at'] and response.json()['closure_reason']
    assert client.post(f'/api/v1/dockets/{docket}/notes', headers=headers,
        json={'content': 'Late note'}).status_code == 409
    assert client.post(f'/api/v1/dockets/{docket}/status', headers=headers,
        json={'status': 'ACTIVE', 'expected_status': 'ACTIVE', 'reason': 'Reopen'}).status_code == 409
    history = db.scalars(select(DocketStatusHistory).where(DocketStatusHistory.docket_id == uuid.UUID(docket))
        .order_by(DocketStatusHistory.changed_at)).all()
    assert [row.to_status for row in history] == ['PENDING_APPROVAL', 'APPROVED', 'ACTIVE', 'ON_HOLD', 'ACTIVE', 'CLOSED']
    assert db.scalar(select(func.count()).select_from(InvestigationNote).where(
        InvestigationNote.docket_id == uuid.UUID(docket))) == 1
    audits = db.scalars(select(AuditLog).where(AuditLog.entity_type == 'investigation_note')).all()
    assert audits and all(row.new_values is None and row.old_values is None for row in audits)


def test_evidence_upload_download_versions_integrity_and_reassignment(investigation):
    client, db, docket, commander, headers, _, second, other, _ = investigation
    assign(investigation)
    item = evidence(investigation)
    content = b'private evidence bytes'
    uploads = [client.post(f'/api/v1/evidence/{item}/files', headers=headers,
        files={'file': ('../../recording.html', content, 'text/html')}) for _ in range(2)]
    assert [r.status_code for r in uploads] == [201, 201]
    assert [r.json()['file_version'] for r in uploads] == [1, 2]
    first = uploads[0].json()
    assert first['original_filename'] == 'recording.html'
    assert first['sha256_hash'] == hashlib.sha256(content).hexdigest()
    assert first['file_size_bytes'] == len(content) and 'storage_key' not in first
    download_url = f"/api/v1/evidence/{item}/files/{first['id']}/download"
    assert client.get(download_url, headers=second).status_code == 404
    downloaded = client.get(download_url, headers=headers)
    assert downloaded.content == content
    assert downloaded.headers['content-type'] == 'application/octet-stream'
    assert downloaded.headers['content-disposition'].startswith('attachment;')
    assert downloaded.headers['x-content-type-options'] == 'nosniff'
    assert downloaded.headers['cache-control'] == 'no-store'
    assert len(client.get(f'/api/v1/evidence/{item}/files', headers=headers).json()) == 2
    events = client.get(f'/api/v1/evidence/{item}/custody-events', headers=headers).json()
    assert [event['event_type'] for event in events] == ['REGISTERED', 'ACCESSED']
    row = db.get(EvidenceFile, uuid.UUID(first['id']))
    (settings.evidence_storage_path / row.storage_key).write_bytes(b'tampered')
    assert client.get(download_url, headers=headers).status_code == 409
    assign(investigation, other)
    assert client.get(download_url, headers=headers).status_code == 404
    assert client.get(f'/api/v1/evidence/{item}', headers=second).status_code == 200


def test_custody_transitions_preserve_current_state_and_history(investigation):
    client, db, _, _, headers, first, _, other, _ = investigation
    assign(investigation)
    item = evidence(investigation)
    url = f'/api/v1/evidence/{item}/custody-events'
    transfer = client.post(url, headers=headers, json={'event_type': 'TRANSFERRED',
        'expected_custody_event_id': client.get(f'/api/v1/evidence/{item}', headers=headers).json()['custody_version'],
        'to_custodian_officer_id': str(other.id), 'to_location': 'Laboratory', 'notes': 'Signed handover'})
    assert transfer.status_code == 201
    assert transfer.json()['from_custodian_officer_id'] == str(first.id)
    for kind in ('ANALYSIS_STARTED', 'ANALYSIS_COMPLETED', 'RELEASED'):
        assert client.post(url, headers=headers, json={'event_type': kind,
            'expected_custody_event_id': client.get(f'/api/v1/evidence/{item}', headers=headers).json()['custody_version'],
            'to_location': 'Laboratory', 'notes': 'Recorded action'}).status_code == 201
    db.expire_all()
    stored = db.get(EvidenceItem, uuid.UUID(item))
    assert stored.status == 'RELEASED' and stored.current_custodian_officer_id is None
    assert client.post(url, headers=headers, json={'event_type': 'DISPOSED',
        'expected_custody_event_id': client.get(f'/api/v1/evidence/{item}', headers=headers).json()['custody_version'],
        'to_location': 'Store', 'notes': 'Invalid followup'}).status_code == 409
    assert client.post(f'/api/v1/evidence/{item}/files', headers=headers,
        files={'file': ('new.txt', b'late')}).status_code == 409
    assert len(client.get(url, headers=headers).json()) == 5


def test_file_size_empty_and_authorization_rejections_leave_no_files(investigation, monkeypatch):
    client, _, _, commander, headers, _, second, *_ = investigation
    assign(investigation)
    item = evidence(investigation)
    monkeypatch.setattr(settings, 'evidence_max_file_bytes', 4)
    url = f'/api/v1/evidence/{item}/files'
    for payload, status in [(b'', 422), (b'12345', 413)]:
        assert client.post(url, headers=headers, files={'file': ('file', payload)}).status_code == status
    assert client.post(url, headers=second, files={'file': ('file', b'123')}).status_code == 404
    assert client.post(url, headers=commander, files={'file': ('file', b'123')}).status_code == 403
    assert client.post(url, files={'file': ('file', b'123')}).status_code == 401
    assert list(settings.evidence_storage_path.iterdir()) == []


def test_station_scope_inactive_targets_and_unapproved_dockets(workflow_context):
    client, db = workflow_context
    first = Station(station_code='C1-' + uuid.uuid4().hex[:8], name='First', province='Test')
    second = Station(station_code='C2-' + uuid.uuid4().hex[:8], name='Second', province='Test')
    db.add_all([first, second])
    db.commit()
    charge, _, _ = officer_account(client, db, 'CHARGE_OFFICER', first)
    commander, _, _ = officer_account(client, db, 'STATION_COMMANDER', first)
    foreign_commander, _, _ = officer_account(client, db, 'STATION_COMMANDER', second)
    investigator, _, officer = officer_account(client, db, 'INVESTIGATING_OFFICER', first)
    foreign_headers, _, foreign = officer_account(client, db, 'INVESTIGATING_OFFICER', second)
    row = complaint_at_station(client, db, first)
    assert client.post(f'/api/v1/complaints/{row.id}/decisions', headers=charge,
        json={'decision': 'ACCEPTED'}).status_code == 201
    docket = client.post(f'/api/v1/complaints/{row.id}/dockets', headers=charge).json()['id']
    url = f'/api/v1/dockets/{docket}/assignments'
    data = {'investigating_officer_id': str(officer.id), 'reason': 'Assign'}
    assert client.post(url, headers=commander, json=data).status_code == 409
    assert client.post(f'/api/v1/dockets/{docket}/approvals', headers=commander,
        json={'decision': 'APPROVED'}).status_code == 201
    db.execute(text('SET LOCAL ROLE saps_api'))
    db.commit()
    assert client.post(url, headers=foreign_commander, json=data).status_code == 404
    assert client.post(url, headers=investigator, json=data).status_code == 403
    assert client.post(url, headers=commander, json=data | {
        'investigating_officer_id': str(foreign.id)}).status_code == 422
    officer.is_active = False
    db.commit()
    assert client.post(url, headers=commander, json=data).status_code == 403
    officer.is_active = True
    db.commit()
    assert client.post(url, headers=commander, json=data).status_code == 201
    assert client.get(f'/api/v1/dockets/{docket}/evidence', headers=foreign_headers).status_code == 404
    assert client.post(f'/api/v1/dockets/{docket}/notes', headers=foreign_headers,
        json={'content': 'Unauthorized'}).status_code == 404


def test_private_storage_rejects_untrusted_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'evidence_storage_path', tmp_path / 'objects')
    storage = EvidenceStorage()
    from fastapi import HTTPException
    for key in ('../secret', '/secret', 'C:\\secret', 'https://example.com/file', ''):
        with pytest.raises(HTTPException) as error:
            storage.path(key)
        assert error.value.status_code == 503


def test_mismatched_file_missing_object_and_permission_revocation(investigation):
    client, db, _, _, headers, officer, *_ = investigation
    assign(investigation)
    item, other = evidence(investigation), evidence(investigation)
    result = client.post(f'/api/v1/evidence/{item}/files', headers=headers,
        files={'file': ('file.txt', b'content')})
    assert result.status_code == 201
    file_id = result.json()['id']
    assert client.get(f'/api/v1/evidence/{other}/files/{file_id}/download', headers=headers).status_code == 404
    row = db.get(EvidenceFile, uuid.UUID(file_id))
    (settings.evidence_storage_path / row.storage_key).unlink()
    assert client.get(f'/api/v1/evidence/{item}/files/{file_id}/download', headers=headers).status_code == 503
    from app.modules.access.models import Permission, RolePermission, Role
    db.execute(text('RESET ROLE'))
    permission = db.scalar(select(Permission.id).where(Permission.code == 'case.close'))
    role = db.scalar(select(Role.id).where(Role.code == 'INVESTIGATING_OFFICER'))
    db.execute(RolePermission.__table__.delete().where(
        RolePermission.role_id == role, RolePermission.permission_id == permission))
    db.commit()
    db.execute(text('SET LOCAL ROLE saps_api'))
    db.commit()
    assert client.post(f'/api/v1/dockets/{investigation[2]}/status', headers=headers,
        json={'status': 'CLOSED', 'expected_status': 'ACTIVE', 'reason': 'Not permitted'}).status_code == 403


def test_request_body_limits_cover_declared_and_chunked_uploads(workflow_context, monkeypatch):
    client, _ = workflow_context
    monkeypatch.setattr(settings, 'evidence_max_file_bytes', 4)
    url = f'/api/v1/evidence/{uuid.uuid4()}/files'
    # Reject before authentication or file creation, regardless of framing.
    assert client.post(url, content=b'x', headers={'Content-Length': str(2 * 1024 * 1024)}).status_code == 413
    content = b'--test\r\nContent-Disposition: form-data; name="file"; filename="test"\r\n\r\n'
    content += b'x' * (1024 * 1024 + 20) + b'\r\n--test--\r\n'
    response = client.post(url, content=iter([content]),
        headers={'Content-Type': 'multipart/form-data; boundary=test'})
    assert response.status_code == 413, response.text
    assert response.headers['cache-control'] == 'no-store'


def test_uncertain_commit_preserves_private_object(investigation, monkeypatch):
    client, db, _, _, headers, *_ = investigation
    assign(investigation)
    item = evidence(investigation)
    def failed_commit():
        raise SQLAlchemyError('Connection lost during commit')
    with monkeypatch.context() as patch:
        patch.setattr(db, 'commit', failed_commit)
        result = client.post(f'/api/v1/evidence/{item}/files', headers=headers,
            files={'file': ('uncertain.txt', b'retain until reconciled')})
    assert result.status_code == 503
    # This simulated failure rolled back, but a real network failure could have
    # committed. Never delete the object automatically after attempting COMMIT.
    assert len(list(settings.evidence_storage_path.iterdir())) == 1
    assert db.scalar(select(EvidenceFile.id).where(EvidenceFile.evidence_item_id == uuid.UUID(item))) is None


def test_production_https_covers_investigation_and_evidence(workflow_context, monkeypatch):
    client, _ = workflow_context
    monkeypatch.setattr(settings, 'environment', 'production')
    for url in ('/api/v1/investigations/dockets', f'/api/v1/evidence/{uuid.uuid4()}'):
        response = client.get(url)
        assert response.status_code == 400 and response.headers['cache-control'] == 'no-store'


def test_audit_failure_rolls_back_note_evidence_counter_and_file(investigation, monkeypatch):
    client, db, docket, _, headers, *rest = investigation
    assign(investigation)
    item = evidence(investigation)
    station = rest[-1]
    def fail(*args, **kwargs):
        raise SQLAlchemyError('Injected audit failure')
    monkeypatch.setattr(InvestigationService, 'audit', fail)
    assert client.post(f'/api/v1/dockets/{docket}/notes', headers=headers,
        json={'content': 'Must roll back'}).status_code == 503
    assert db.scalar(select(InvestigationNote.id).where(InvestigationNote.docket_id == uuid.UUID(docket))) is None
    response = client.post(f'/api/v1/dockets/{docket}/evidence', headers=headers, json={
        'title': 'Rollback', 'description': 'Rollback', 'evidence_type': 'OTHER',
        'is_digital': False, 'storage_location': 'Locker'})
    assert response.status_code == 503
    assert db.scalar(select(IdentifierCounter.last_value).where(
        IdentifierCounter.station_id == station.id, IdentifierCounter.counter_type == 'EVIDENCE')) == 1
    assert client.post(f'/api/v1/evidence/{item}/files', headers=headers,
        files={'file': ('file.txt', b'rollback')}).status_code == 503
    assert db.scalar(select(EvidenceFile.id).where(EvidenceFile.evidence_item_id == uuid.UUID(item))) is None
    assert list(settings.evidence_storage_path.iterdir()) == []
