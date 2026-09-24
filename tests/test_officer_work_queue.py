"""Collaborator B UI support: station queues, review start and investigator discovery."""
import uuid

from sqlalchemy import select

from test_decisions_dockets import (complaint_at_station, officer_account,
                                    workflow_context)
from app.modules.audit.models import AuditLog
from app.modules.complaints.models import Complaint
from app.modules.stations.models import Station


def station(db, prefix):
    row = Station(station_code=f'{prefix}-{uuid.uuid4().hex[:8]}',
                  name=f'{prefix} Station', province='Test')
    db.add(row)
    db.commit()
    return row


def test_officer_frontend_assets_are_served(workflow_context):
    client, _ = workflow_context
    page = client.get('/officer/')
    assert page.status_code == 200
    assert 'SAPS Case Desk' in page.text
    assert client.get('/officer/assets/styles.css').status_code == 200
    assert client.get('/officer/assets/app.js').status_code == 200


def test_station_complaint_queue_and_review_are_scoped(workflow_context):
    client, db = workflow_context
    own_station, other_station = station(db, 'QUEUE'), station(db, 'OTHER')
    headers, user_id, _ = officer_account(client, db, 'CHARGE_OFFICER', own_station)
    own = complaint_at_station(client, db, own_station)
    foreign = complaint_at_station(client, db, other_station)

    queue = client.get('/api/v1/complaints/station', headers=headers)
    assert queue.status_code == 200
    assert {item['id'] for item in queue.json()['items']} == {str(own.id)}
    assert client.post(f'/api/v1/complaints/{foreign.id}/review', headers=headers).status_code == 404
    reviewed = client.post(f'/api/v1/complaints/{own.id}/review', headers=headers)
    assert reviewed.status_code == 200
    assert reviewed.json()['status'] == 'UNDER_REVIEW'
    assert db.get(Complaint, own.id).review_started_at is not None
    actions = set(db.scalars(select(AuditLog.action).where(
        AuditLog.actor_user_id == user_id)).all())
    assert {'complaint.list_station', 'complaint.view_station', 'complaint.review.start'} <= actions


def test_commander_docket_queue_and_investigators_are_station_scoped(workflow_context):
    client, db = workflow_context
    own_station, other_station = station(db, 'DOCKETQ'), station(db, 'FOREIGNQ')
    charge, _, _ = officer_account(client, db, 'CHARGE_OFFICER', own_station)
    complaint = complaint_at_station(client, db, own_station)
    client.post(f'/api/v1/complaints/{complaint.id}/decisions',
                json={'decision': 'ACCEPTED'}, headers=charge)
    docket = client.post(f'/api/v1/complaints/{complaint.id}/dockets', headers=charge).json()

    commander, _, _ = officer_account(client, db, 'STATION_COMMANDER', own_station)
    foreign_commander, _, _ = officer_account(client, db, 'STATION_COMMANDER', other_station)
    _, _, own_investigator = officer_account(client, db, 'INVESTIGATING_OFFICER', own_station)
    _, _, foreign_investigator = officer_account(client, db, 'INVESTIGATING_OFFICER', other_station)

    own_queue = client.get('/api/v1/dockets?status=PENDING_APPROVAL', headers=commander)
    assert own_queue.status_code == 200
    assert [item['id'] for item in own_queue.json()] == [docket['id']]
    assert client.get('/api/v1/dockets', headers=foreign_commander).json() == []

    investigators = client.get('/api/v1/stations/investigators', headers=commander)
    assert investigators.status_code == 200
    ids = {item['id'] for item in investigators.json()}
    assert str(own_investigator.id) in ids
    assert str(foreign_investigator.id) not in ids
