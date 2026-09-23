"""Tracking integration tests use the authentication suite's rollback-only database."""
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from test_authentication import auth_context, authorization, enroll
from app.modules.access.models import Permission, RolePermission
from app.modules.audit.models import AuditLog
from app.modules.complainants.models import Complainant
from app.modules.complaints.models import Complaint
from app.modules.stations.models import Station


def complaint(db, owner_id):
    station = Station(station_code=uuid.uuid4().hex, name='Tracking test', province='Test')
    db.add(station)
    db.flush()
    row = Complaint(reference_number='TEST-' + uuid.uuid4().hex, complainant_id=owner_id,
                    station_id=station.id, channel='ONLINE', crime_category='Test',
                    incident_description='Private narrative', incident_location='Private address',
                    incident_province='Test')
    db.add(row)
    db.flush()
    return row


def account(client, db):
    _, _, tokens = enroll(client)
    headers = authorization(tokens)
    person = client.get('/api/v1/auth/me', headers=headers).json()['complainant_id']
    return headers, uuid.UUID(person), tokens


def test_tracking_ownership_pagination_and_audit(auth_context):
    client, db = auth_context
    headers, owner, _ = account(client, db)
    other_headers, other_owner, _ = account(client, db)
    first, second = complaint(db, owner), complaint(db, owner)
    foreign = complaint(db, other_owner)
    db.commit()
    response = client.get('/api/v1/complaints/mine?limit=1', headers=headers)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['has_more'] is True
    page_two = client.get('/api/v1/complaints/mine?limit=1&offset=1', headers=headers).json()
    assert page_two['has_more'] is False
    assert {response.json()['items'][0]['id'], page_two['items'][0]['id']} == {str(first.id), str(second.id)}
    detail = client.get(f'/api/v1/complaints/{first.id}/tracking', headers=headers)
    assert detail.status_code == 200
    assert set(detail.json()) == {'id', 'reference_number', 'status', 'station_id', 'submitted_at',
                                  'review_started_at', 'updated_at'}
    assert detail.json()['status'] == 'SUBMITTED'
    denied = client.get(f'/api/v1/complaints/{foreign.id}/tracking', headers=headers)
    missing = client.get(f'/api/v1/complaints/{uuid.uuid4()}/tracking', headers=headers)
    assert denied.status_code == missing.status_code == 404
    assert denied.json() == missing.json()
    assert client.get(f'/api/v1/complaints/{first.id}/tracking', headers=other_headers).status_code == 404
    audit = db.scalars(select(AuditLog).where(AuditLog.action == 'complaint.track_own',
                                            AuditLog.entity_id == first.id)).all()
    assert audit and all(event.station_id == first.station_id for event in audit)
    assert all(event.new_values is None and event.event_metadata is None for event in audit)


def test_tracking_authentication_permission_and_logout(auth_context):
    client, db = auth_context
    headers, owner, tokens = account(client, db)
    row = complaint(db, owner)
    db.commit()
    paths = ['/api/v1/complaints/mine', f'/api/v1/complaints/{row.id}/tracking']
    for path in paths:
        for invalid in ({}, {'Authorization': 'Bearer invalid'}):
            response = client.get(path, headers=invalid)
            assert response.status_code == 401
            assert response.headers['cache-control'] == 'no-store'
    permission = db.scalar(select(Permission.id).where(Permission.code == 'case.track_own'))
    db.execute(delete(RolePermission).where(RolePermission.permission_id == permission))
    db.commit()
    for path in paths:
        assert client.get(path, headers=headers).status_code == 403
    assert client.post('/api/v1/auth/logout', json={'refresh_token': tokens['refresh_token']}).status_code == 204
    assert client.get(paths[0], headers=headers).status_code == 401


def test_tracking_empty_missing_profile_and_validation(auth_context):
    client, db = auth_context
    headers, owner, _ = account(client, db)
    assert client.get('/api/v1/complaints/mine', headers=headers).json()['items'] == []
    for query in ('limit=0', 'limit=101', 'offset=-1'):
        assert client.get('/api/v1/complaints/mine?' + query, headers=headers).status_code == 422
    assert client.get('/api/v1/complaints/not-a-uuid/tracking', headers=headers).status_code == 422
    person = db.get(Complainant, owner)
    person.user_id = None
    db.commit()
    assert client.get('/api/v1/complaints/mine', headers=headers).status_code == 403


@pytest.mark.parametrize('listing', [False, True])
def test_tracking_audit_failure_returns_no_data(auth_context, monkeypatch, listing):
    client, db = auth_context
    headers, owner, _ = account(client, db)
    row = complaint(db, owner)
    db.commit()
    original_add = db.add
    def fail_audit(instance, **kwargs):
        if isinstance(instance, AuditLog):
            raise SQLAlchemyError('Sensitive internal failure')
        return original_add(instance, **kwargs)
    monkeypatch.setattr(db, 'add', fail_audit)
    path = '/api/v1/complaints/mine' if listing else f'/api/v1/complaints/{row.id}/tracking'
    response = client.get(path, headers=headers)
    assert response.status_code == 503
    assert response.json() == {'detail': 'Complaint tracking unavailable'}
    assert db.scalar(select(AuditLog.id).where(AuditLog.action == 'complaint.track_own',
                                             AuditLog.entity_id == row.id)) is None
