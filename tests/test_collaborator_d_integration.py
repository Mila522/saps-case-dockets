"""Real A/B/C/D integration; reuse rollback-only fixture and temporary private storage."""
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, func, text
from sqlalchemy.exc import SQLAlchemyError

from test_decisions_dockets import workflow_context, officer_account
from test_complaint_tracking import account
from app.core.config import settings
from app.modules.authentication.security import utcnow
from app.modules.audit.models import AuditLog
from app.modules.communications.models import Notification, NotificationAttempt, OfficialDocument, CaseFeedback
from app.modules.complaints.models import Complaint
from app.modules.alerts.models import Alert
from app.modules.dockets.models import Docket
from app.modules.stations.models import Station
from app.modules.system.models import IdentifierCounter


def test_full_d_workflow_and_scope(workflow_context, tmp_path, monkeypatch):
    client, db = workflow_context
    monkeypatch.setattr(settings, 'document_storage_path', tmp_path / 'documents')
    station = Station(station_code='D-' + uuid4().hex[:8], name='D test', province='Test')
    foreign = Station(station_code='F-' + uuid4().hex[:8], name='Foreign', province='Test')
    db.add_all([station, foreign])
    db.commit()
    owner, _, _ = account(client, db)
    outsider, _, _ = account(client, db)
    charge, _, _ = officer_account(client, db, 'CHARGE_OFFICER', station)
    commander, commander_id, _ = officer_account(client, db, 'STATION_COMMANDER', station)
    foreign_commander, _, _ = officer_account(client, db, 'STATION_COMMANDER', foreign)
    investigator, _, officer = officer_account(client, db, 'INVESTIGATING_OFFICER', station)
    unassigned, _, other = officer_account(client, db, 'INVESTIGATING_OFFICER', station)
    db.execute(text('SET LOCAL ROLE saps_api'))
    db.commit()
    result = client.post('/api/v1/complaints', headers=owner, json={
        'station_id': str(station.id), 'crime_category': 'Theft', 'incident_description': 'PRIVATE NARRATIVE',
        'incident_location': 'PRIVATE ADDRESS', 'incident_province': 'Test'})
    assert result.status_code == 201, result.text
    complaint_id = result.json()['id']
    notification = client.get('/api/v1/notifications', headers=owner).json()[0]
    assert notification['event_type'] == 'complaint.registered'
    assert 'PRIVATE' not in notification['message']
    assert client.get(f'/api/v1/notifications/{notification["id"]}', headers=outsider).status_code == 404
    assert client.get('/api/v1/notifications', headers=outsider).json() == []
    url = f'/api/v1/complaints/{complaint_id}/documents'
    payload = {'document_type': 'COMPLAINT_REGISTRATION_CONFIRMATION'}
    assert client.post(url, headers=outsider, json=payload).status_code == 404
    created = client.post(url, headers=owner, json=payload)
    assert created.status_code == 201, created.text
    document = created.json()
    assert document['document_number'].startswith('DOC-' + station.station_code)
    assert client.post(url, headers=owner, json=payload).json()['id'] == document['id']
    assert db.scalar(select(IdentifierCounter.last_value).where(IdentifierCounter.station_id == station.id,
        IdentifierCounter.counter_type == 'DOCUMENT')) == 1
    download = f'/api/v1/documents/{document["id"]}/download'
    assert client.get(download, headers=owner).status_code == 200
    assert client.get(download, headers=outsider).status_code == 404
    assert client.get(download, headers=foreign_commander).status_code == 404
    assert client.get(download, headers=unassigned).status_code == 404
    assert client.post(f'/api/v1/complaints/{complaint_id}/decisions', headers=charge,
        json={'decision': 'ACCEPTED'}).status_code == 201
    opened = client.post(f'/api/v1/complaints/{complaint_id}/dockets', headers=charge)
    docket_id = opened.json()['id']
    # Existing mutable docket timestamp permits a deterministic overdue fixture.
    docket = db.get(Docket, UUID(docket_id))
    docket.opened_at = utcnow() - timedelta(days=5)
    db.commit()
    first = client.post('/api/v1/alerts/evaluate', headers=commander)
    assert first.status_code == 200, first.text
    assert first.json()['created'] == 1
    assert client.post('/api/v1/alerts/evaluate', headers=commander).json()['created'] == 0
    alert_id = first.json()['alert_ids'][0]
    assert client.get('/api/v1/alerts', headers=foreign_commander).json() == []
    change = {'expected_status': 'OPEN', 'status': 'ACKNOWLEDGED', 'notes': 'Review started'}
    assert client.patch(f'/api/v1/alerts/{alert_id}', headers=foreign_commander, json=change).status_code == 404
    assert client.patch(f'/api/v1/alerts/{alert_id}', headers=commander, json=change).status_code == 200
    assert client.patch(f'/api/v1/alerts/{alert_id}', headers=commander, json=change).status_code == 409
    assert db.get(Alert, UUID(alert_id)).acknowledged_by_user_id == commander_id
    assert client.get('/api/v1/dashboards/summary', headers=owner).status_code == 403
    summary = client.get('/api/v1/dashboards/summary', headers=commander).json()
    assert summary['station_id'] == str(station.id) and summary['dockets_by_status']['PENDING_APPROVAL'] == 1
    assert client.get('/api/v1/dashboards/summary', headers=foreign_commander).json()['dockets_by_status'] == {}
    assert client.post(f'/api/v1/dockets/{docket_id}/approvals', headers=commander,
        json={'decision': 'APPROVED'}).status_code == 201
    assert client.post(f'/api/v1/dockets/{docket_id}/assignments', headers=commander,
        json={'investigating_officer_id': str(officer.id), 'reason': 'Investigate'}).status_code == 201
    feedback_url = f'/api/v1/dockets/{docket_id}/feedback'
    feedback = {'feedback_type': 'PROGRESS_UPDATE', 'subject': 'Update', 'message': 'PRIVATE FEEDBACK'}
    assert client.post(feedback_url, headers=unassigned, json=feedback).status_code == 404
    published = client.post(feedback_url, headers=investigator, json=feedback)
    assert published.status_code == 201, published.text
    correction = client.post(feedback_url, headers=investigator,
        json=feedback | {'supersedes_feedback_id': published.json()['id'], 'message': 'Corrected'})
    assert correction.status_code == 201
    assert client.post(feedback_url, headers=investigator,
        json=feedback | {'supersedes_feedback_id': published.json()['id']}).status_code == 409
    assert len(client.get(feedback_url, headers=owner).json()['items']) == 2
    assert client.get(feedback_url, headers=outsider).status_code == 404
    assert client.get(download, headers=investigator).status_code == 200
    assert client.post(f'/api/v1/dockets/{docket_id}/assignments', headers=commander,
        json={'investigating_officer_id': str(other.id), 'reason': 'Reassign'}).status_code == 201
    assert client.get(feedback_url, headers=investigator).status_code == 404
    assert client.get(download, headers=investigator).status_code == 404
    assert client.post(f'/api/v1/dockets/{docket_id}/status', headers=unassigned,
        json={'expected_status': 'ACTIVE', 'status': 'CLOSED', 'reason': 'Completed'}).status_code == 200
    assert client.post(url, headers=owner, json={'document_type': 'CASE_CLOSURE_CONFIRMATION'}).status_code == 201
    events = client.get('/api/v1/notifications', headers=owner).json()
    assert {'complaint.registered', 'complaint.accepted', 'docket.created', 'docket.assigned',
        'feedback.published', 'case.closed'} <= {event['event_type'] for event in events}
    assert db.scalar(select(func.count()).select_from(NotificationAttempt).join(Notification)
        .where(Notification.complaint_id == UUID(complaint_id))) == len(events)
    audits = db.scalars(select(AuditLog).where(AuditLog.station_id == station.id)).all()
    assert {'feedback.published', 'document.generate', 'document.download', 'alert.detected',
        'dashboard.view', 'notification.delivered'} <= {audit.action for audit in audits}
    assert all('PRIVATE' not in str(audit.event_metadata) for audit in audits)


def test_feedback_notification_audit_failure_rolls_back(investigation, monkeypatch):
    from test_investigation_api import assign
    client, db, docket, _, investigator, *_ = investigation
    assign(investigation)
    original = db.add

    def fail_notification_audit(row):
        if isinstance(row, AuditLog) and row.action == 'notification.delivered':
            raise SQLAlchemyError('injected failure')
        original(row)

    monkeypatch.setattr(db, 'add', fail_notification_audit)
    result = client.post(f'/api/v1/dockets/{docket}/feedback', headers=investigator,
        json={'feedback_type': 'GENERAL', 'subject': 'Update', 'message': 'Failed write'})
    assert result.status_code == 503
    monkeypatch.setattr(db, 'add', original)
    assert db.scalar(select(func.count()).select_from(CaseFeedback).where(CaseFeedback.docket_id == UUID(docket))) == 0
    assert db.scalar(select(func.count()).select_from(Notification).where(Notification.docket_id == UUID(docket),
        Notification.event_type == 'feedback.published')) == 0


from test_investigation_api import investigation  # noqa: E402,F401 -- shared rollback fixture
