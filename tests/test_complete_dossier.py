import uuid
from sqlalchemy import select

from app.core.config import settings
from app.modules.audit.models import AuditLog
from app.modules.stations.models import Station
from test_authentication import enroll, registration, authorization
from test_complaint_registration import payload
from test_decisions_dockets import workflow_context, officer_account


def test_complete_record_follows_handoffs_with_scoped_activity(workflow_context, monkeypatch, tmp_path):
    client, db = workflow_context
    monkeypatch.setattr(settings, 'evidence_storage_path', tmp_path)
    person, _, tokens = enroll(client, registration(address_line_1='12 Original Street', city='Durban', province='KwaZulu-Natal'))
    owner = authorization(tokens)
    data = payload(db, crime_category='Vehicle theft', vehicle_number_plate='ND 123 456',
        incident_description='Original_statement with underscores', incident_city='Durban')
    station = db.get(Station, uuid.UUID(data['station_id']))
    charge, charge_id, _ = officer_account(client, db, 'CHARGE_OFFICER', station)
    commander, commander_id, _ = officer_account(client, db, 'STATION_COMMANDER', station)
    investigator, investigator_id, officer = officer_account(client, db, 'INVESTIGATING_OFFICER', station)
    other, _, _ = officer_account(client, db, 'INVESTIGATING_OFFICER', station)
    file = client.post('/api/v1/complaints/uploads', headers=owner, data={'purpose': 'VEHICLE_REGISTRATION'},
        files={'file': ('car.pdf', b'%PDF-original', 'application/pdf')}).json()
    data.update(upload_ids=[file['id']], witnesses=[{'first_name': 'Actual', 'last_name': 'Witness', 'statement_text': 'Original witness account'}])
    created = client.post('/api/v1/complaints', headers=owner, json=data)
    assert created.status_code == 201, created.text
    complaint_id = created.json()['id']
    before = client.get(f'/api/v1/complaints/{complaint_id}/dossier', headers=charge)
    assert before.status_code == 200, before.text
    assert before.json()['docket'] is None
    assert before.json()['complainant']['address_line_1'] == '12 Original Street'
    assert before.json()['witnesses'][0]['statements'][0]['statement_text'] == 'Original witness account'
    assert before.json()['complainant_uploads'][0]['original_filename'] == 'car.pdf'
    accepted = client.post(f'/api/v1/complaints/{complaint_id}/decisions', headers=charge, json={'decision': 'ACCEPTED'}).json()
    docket_id = accepted['docket_id']
    path = f'/api/v1/dockets/{docket_id}/full'
    # Unrelated or auth data must not enter the trail, even with a coincident entity ID.
    db.add_all([
        AuditLog(actor_type='SYSTEM', action='unrelated.case', entity_type='complaint', entity_id=uuid.uuid4()),
        AuditLog(actor_type='SYSTEM', action='auth.private', entity_type='auth_session', entity_id=uuid.UUID(complaint_id)),
    ])
    db.commit()
    review = client.get(path, headers=commander)
    assert review.status_code == 200, review.text
    body = review.json()
    assert body['docket']['status'] == 'PENDING_APPROVAL'
    assert body['complaint']['incident_description'] == data['incident_description']
    assert body['complaint']['vehicle_number_plate'] == data['vehicle_number_plate']
    assert body['complainant']['phone_number'] == person['phone_number']
    assert body['statements'][0]['statement_text'] == data['incident_description']
    actions = [r['action'] for r in body['activity_trail']]
    assert {'complaint.submit', 'complaint.upload', 'complaint.dossier.view', 'complaint.decide.accepted', 'docket.create', 'docket.full.view'} <= set(actions)
    assert 'unrelated.case' not in actions and 'auth.private' not in actions
    assert all(set(r) == {'id', 'occurred_at', 'actor', 'action', 'record_type'} for r in body['activity_trail'])
    assert 'storage_key' not in review.text and 'destination_encrypted' not in review.text
    assert client.get(path, headers=owner).status_code == 403
    assert client.get(path, headers=investigator).status_code == 404
    assert client.post(f'/api/v1/dockets/{docket_id}/approvals', headers=commander, json={'decision': 'APPROVED'}).status_code == 201
    assert client.post(f'/api/v1/dockets/{docket_id}/assignments', headers=commander,
        json={'investigating_officer_id': str(officer.id), 'reason': 'Investigate complete record'}).status_code == 201
    assigned = client.get(path, headers=investigator)
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()['complainant_uploads'] == body['complainant_uploads']
    assert client.get(path, headers=other).status_code == 404
    assert client.get(f'/api/v1/complaints/{complaint_id}/uploads/{file["id"]}/download', headers=investigator).content == b'%PDF-original'
    assert client.post(f'/api/v1/dockets/{docket_id}/notes', headers=investigator,
        json={'note_type': 'PROGRESS', 'content': 'Investigated original report', 'is_sensitive': False}).status_code == 201
    after = client.get(path, headers=charge).json()
    assert after['investigation_notes'][0]['content'] == 'Investigated original report'
    assert 'case.add_note' in {r['action'] for r in after['activity_trail']}
    assert 'complaint.upload.download' in {r['action'] for r in after['activity_trail']}
    other_station = Station(station_code='OTHER-' + uuid.uuid4().hex, name='Other', province='Test')
    db.add(other_station); db.commit()
    foreign, _, _ = officer_account(client, db, 'STATION_COMMANDER', other_station)
    assert client.get(path, headers=foreign).status_code == 404
