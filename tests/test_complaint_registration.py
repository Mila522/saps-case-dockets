"""Registration, scope and atomicity checks against migrated PostgreSQL."""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from test_authentication import auth_context
from test_complaint_tracking import account
from app.modules.access.models import Permission, RolePermission
from app.modules.audit.models import AuditLog
from app.modules.complainants.models import Complainant
from app.modules.complaints.models import Complaint
from app.modules.stations.models import Station
from app.modules.system.models import IdentifierCounter
from app.modules.system.service import allocate_complaint_reference


def payload(db, **changes):
    station = Station(station_code='T-' + uuid.uuid4().hex, name='Test', province='Test')
    db.add(station)
    db.commit()
    return dict(station_id=str(station.id), crime_category='Theft',
                incident_description='Private description', incident_location='Private address',
                incident_province='Test') | changes


def test_registration_owned_trackable_and_numbered(auth_context):
    client, db = auth_context
    headers, owner, _ = account(client, db)
    data = payload(db)
    responses = [client.post('/api/v1/complaints', json=data, headers=headers) for _ in range(2)]
    assert all(response.status_code == 201 for response in responses)
    first, second = [response.json() for response in responses]
    assert first['reference_number'].endswith('-000001')
    assert second['reference_number'].endswith('-000002')
    assert first['status'] == 'SUBMITTED'
    assert responses[0].headers['cache-control'] == 'no-store'
    row = db.get(Complaint, uuid.UUID(first['id']))
    assert row.complainant_id == owner and row.channel == 'ONLINE'
    assert row.registered_by_officer_id is None
    assert row.incident_description == data['incident_description']
    audit = db.scalar(select(AuditLog).where(AuditLog.entity_id == row.id,
                                           AuditLog.action == 'complaint.submit'))
    assert audit and audit.station_id == row.station_id
    assert audit.new_values is None and audit.event_metadata is None
    tracked = client.get(f"/api/v1/complaints/{row.id}/tracking", headers=headers)
    assert tracked.status_code == 200 and tracked.json()['reference_number'] == first['reference_number']


@pytest.mark.parametrize('extra', [
    {'complainant_id': str(uuid.uuid4())}, {'channel': 'IN_STATION'}, {'status': 'ACCEPTED'},
    {'reference_number': 'FORGED'}, {'registered_by_officer_id': str(uuid.uuid4())},
    {'crime_category': '  '}, {'incident_description': ''}, {'incident_location': 'x' * 256},
    {'incident_occurred_at': '2026-01-01T12:00:00'},
    {'incident_occurred_at': (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()},
])
def test_registration_rejects_invalid_and_server_owned_fields(auth_context, extra):
    client, db = auth_context
    headers, _, _ = account(client, db)
    data = payload(db, **extra)
    assert client.post('/api/v1/complaints', json=data, headers=headers).status_code == 422
    assert db.scalar(select(Complaint.id).where(Complaint.station_id == uuid.UUID(data['station_id']))) is None


def test_registration_requires_auth_permission_profile_and_active_station(auth_context):
    client, db = auth_context
    headers, owner, _ = account(client, db)
    data = payload(db)
    assert client.post('/api/v1/complaints', json=data).status_code == 401
    station = db.get(Station, uuid.UUID(data['station_id']))
    station.is_active = False
    db.commit()
    assert client.post('/api/v1/complaints', json=data, headers=headers).status_code == 404
    assert client.post('/api/v1/complaints', json=data | {'station_id': str(uuid.uuid4())}, headers=headers).status_code == 404
    station.is_active = True
    db.get(Complainant, owner).user_id = None
    db.commit()
    assert client.post('/api/v1/complaints', json=data, headers=headers).status_code == 403
    permission = db.scalar(select(Permission.id).where(Permission.code == 'complaint.submit'))
    db.execute(delete(RolePermission).where(RolePermission.permission_id == permission))
    db.commit()
    assert client.post('/api/v1/complaints', json=data, headers=headers).status_code == 403


def test_audit_failure_rolls_back_complaint_and_counter(auth_context, monkeypatch):
    client, db = auth_context
    headers, _, _ = account(client, db)
    data = payload(db)
    station_id = uuid.UUID(data['station_id'])
    original_add = db.add
    def fail_audit(instance, **kwargs):
        if isinstance(instance, AuditLog):
            raise SQLAlchemyError('Do not expose')
        return original_add(instance, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(db, 'add', fail_audit)
        response = client.post('/api/v1/complaints', json=data, headers=headers)
    assert response.status_code == 503
    assert response.json() == {'detail': 'Complaint registration unavailable'}
    assert db.scalar(select(Complaint.id).where(Complaint.station_id == station_id)) is None
    assert db.scalar(select(IdentifierCounter.last_value).where(IdentifierCounter.station_id == station_id)) is None
    retry = client.post('/api/v1/complaints', json=data, headers=headers)
    assert retry.status_code == 201 and retry.json()['reference_number'].endswith('-000001')


def test_reference_uses_johannesburg_year_and_expands_width():
    db = Mock()
    db.execute.return_value.scalar_one.return_value = 1000000
    station = Station(id=uuid.uuid4(), station_code='ABC')
    reference = allocate_complaint_reference(db, station, datetime(2026, 12, 31, 23, tzinfo=timezone.utc))
    assert reference == 'CMP-ABC-2027-1000000'
    db.commit.assert_not_called()


def test_invalid_station_code_allocates_nothing(auth_context):
    client, db = auth_context
    headers, _, _ = account(client, db)
    data = payload(db)
    station = db.get(Station, uuid.UUID(data['station_id']))
    station.station_code = 'invalid/code'
    db.commit()
    assert client.post('/api/v1/complaints', json=data, headers=headers).status_code == 409
    assert db.scalar(select(IdentifierCounter.last_value).where(IdentifierCounter.station_id == station.id)) is None
