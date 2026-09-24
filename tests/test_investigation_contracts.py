"""Authorization, validation, stale-source and Swagger contract regressions."""
import uuid

import pytest
from sqlalchemy import select, text, LargeBinary
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from test_investigation_api import investigation, assign, evidence
from test_decisions_dockets import workflow_context, officer_account
from app.main import app
from app.modules.access.models import User, Role, UserRole
from app.modules.authentication.security import utcnow
from app.modules.evidence.models import EvidenceItem, EvidenceFile, EvidenceCustodyEvent
from app.modules.investigations.models import CaseAssignment
from app.modules.investigations.service import InvestigationService
from app.modules.stations.models import Station
from app.modules.system.service import allocate_evidence_reference


def test_validation_stale_source_and_custody_failure_atomicity(investigation, monkeypatch):
    client, db, docket, _, headers, officer, _, other, _ = investigation
    assign(investigation)
    for payload in ({'content': ' '}, {'content': 'Text', 'note_type': 'INVALID'}):
        assert client.post(f'/api/v1/dockets/{docket}/notes', headers=headers, json=payload).status_code == 422
    for payload in ({'status': 'CLOSED', 'expected_status': 'ACTIVE'},
                    {'status': 'CLOSED', 'expected_status': 'ACTIVE', 'reason': ' '},
                    {'status': 'ARCHIVED', 'expected_status': 'ACTIVE', 'reason': 'Archive'}):
        assert client.post(f'/api/v1/dockets/{docket}/status', headers=headers, json=payload).status_code == 422
    assert client.post(f'/api/v1/dockets/{docket}/status', headers=headers, json={
        'status': 'ACTIVE', 'expected_status': 'ACTIVE', 'reason': 'No change'}).status_code == 409
    item = evidence(investigation)
    current = client.get(f'/api/v1/evidence/{item}', headers=headers).json()
    payload = {'event_type': 'TRANSFERRED', 'expected_custody_event_id': current['custody_version'],
        'to_custodian_officer_id': str(other.id), 'to_location': 'Lab', 'notes': 'Transfer'}
    url = f'/api/v1/evidence/{item}/custody-events'
    assert client.post(url, headers=headers, json=payload | {'expected_custody_event_id': str(uuid.uuid4())}).status_code == 409
    assert client.post(url, headers=headers, json=payload | {'from_custodian_officer_id': str(other.id)}).status_code == 422
    assert client.post(url, headers=headers, json=payload | {'to_custodian_officer_id': None}).status_code == 422
    assert client.post(url, headers=headers, json=payload | {'event_type': 'INVALID'}).status_code == 422
    def fail(*args, **kwargs):
        raise SQLAlchemyError('Audit failure')
    with monkeypatch.context() as patch:
        patch.setattr(InvestigationService, 'audit', fail)
        assert client.post(url, headers=headers, json=payload).status_code == 503
    db.expire_all()
    row = db.get(EvidenceItem, uuid.UUID(item))
    assert row.current_custodian_officer_id == officer.id and row.current_storage_location == 'Secure locker'
    assert client.get(f'/api/v1/evidence/{item}', headers=headers).json()['custody_version'] == current['custody_version']
    assert len(client.get(url, headers=headers).json()) == 1


def test_expired_assignments_inactive_officers_and_no_implicit_national_access(investigation):
    client, db, docket, _, headers, officer, *_ = investigation
    row = assign(investigation)
    # Role grants alone never bypass the assignment check, including management.
    db.execute(text('RESET ROLE'))
    management = db.scalar(select(Role.id).where(Role.code == 'SAPS_MANAGEMENT'))
    db.add(UserRole(user_id=officer.user_id, role_id=management))
    db.commit()
    db.execute(text('SET LOCAL ROLE saps_api'))
    db.commit()
    assert client.get(f'/api/v1/investigations/dockets/{docket}', headers=headers).status_code == 200
    officer.is_active = False
    db.commit()
    assert client.get('/api/v1/investigations/dockets', headers=headers).status_code == 403
    officer.is_active = True
    assignment = db.get(CaseAssignment, uuid.UUID(row['id']))
    assignment.unassigned_at = utcnow()
    db.commit()
    assert client.get('/api/v1/investigations/dockets', headers=headers).json() == []
    assert client.get(f'/api/v1/investigations/dockets/{docket}', headers=headers).status_code == 404
    assert client.post(f'/api/v1/dockets/{docket}/notes', headers=headers,
        json={'content': 'Expired'}).status_code == 404


def test_one_assignment_and_file_database_constraints(investigation):
    client, db, docket, commander, headers, officer, _, other, _ = investigation
    assign(investigation)
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(CaseAssignment.__table__.insert().values(docket_id=uuid.UUID(docket),
                investigating_officer_id=other.id, assigned_by_officer_id=officer.id))
    item = evidence(investigation)
    uploaded = client.post(f'/api/v1/evidence/{item}/files', headers=headers,
        files={'file': ('one.txt', b'content')}).json()
    values = dict(evidence_item_id=uuid.UUID(item), original_filename='two.txt', storage_key=uuid.uuid4().hex,
        media_type='text/plain', file_size_bytes=1, sha256_hash='a' * 64,
        uploaded_by_user_id=officer.user_id, file_version=2)
    for change in ({'sha256_hash': 'a' * 63}, {'sha256_hash': 'g' * 64},
                   {'file_size_bytes': 0}, {'file_size_bytes': -1}, {'file_version': 1}):
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.execute(EvidenceFile.__table__.insert().values(**(values | change)))
    assert not any(isinstance(column.type, LargeBinary) for model in (EvidenceItem, EvidenceFile, EvidenceCustodyEvent)
        for column in model.__table__.columns)
    for method in (client.put, client.patch, client.delete):
        assert method(f'/api/v1/evidence/{item}/files/{uploaded["id"]}', headers=headers).status_code in (404, 405)
        assert method(f'/api/v1/dockets/{docket}/notes', headers=headers).status_code in (404, 405)


def test_evidence_counters_are_station_scoped(workflow_context):
    _, db = workflow_context
    stations = [Station(station_code='COUNT-' + uuid.uuid4().hex[:8], name='Counter', province='Test') for _ in range(2)]
    db.add_all(stations)
    db.flush()
    now = utcnow()
    first = [allocate_evidence_reference(db, stations[0], now) for _ in range(2)]
    second = allocate_evidence_reference(db, stations[1], now)
    assert first[0].endswith('-000001') and first[1].endswith('-000002') and second.endswith('-000001')
    assert stations[0].station_code in first[0] and stations[1].station_code in second


def test_c_openapi_authentication_errors_and_documentation(workflow_context):
    client, _ = workflow_context
    assert client.get('/docs').status_code == 200
    schema = client.get('/openapi.json').json()
    routes = [(path, method, operation) for path, methods in schema['paths'].items()
        for method, operation in methods.items() if set(operation.get('tags', [])) & {'Investigations', 'Evidence'}]
    assert len(routes) == 16
    for path, method, operation in routes:
        assert operation['summary'] and operation['description'] and operation['security']
        assert {'401', '403', '404', '409', '422'} <= set(operation['responses'])
    binary = schema['paths']['/api/v1/evidence/{evidence_id}/files/{file_id}/download']['get']
    assert 'application/octet-stream' in binary['responses']['200']['content']
    assert not any(path.startswith(('/api/v1/feedback', '/api/v1/notifications', '/api/v1/documents',
        '/api/v1/alerts', '/api/v1/dashboards')) for path, _, _ in routes)
