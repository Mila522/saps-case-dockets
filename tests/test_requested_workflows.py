"""Registration-only verification, recovery, private intake files and full dockets."""
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.core.config import settings
from app.modules.authentication import mail, security
from app.modules.authentication.models import PasswordReset
from app.modules.complainants.models import Complainant
from app.modules.complaints.models import Complaint, ComplaintUpload, Witness
from test_authentication import auth_context, enroll, authorization, registration
from test_complaint_registration import payload
from test_decisions_dockets import workflow_context, officer_account
from test_investigation_api import investigation, assign, evidence


def test_verified_login_does_not_send_another_code_and_address_saved(auth_context, monkeypatch):
    client, db = auth_context
    data, _, tokens = enroll(client, registration(address_line_1='12 Test Road', city='Durban', province='KwaZulu-Natal'))
    def unexpected(*args):
        raise AssertionError('Verified sign-in must not send a code')
    monkeypatch.setattr(mail, 'send_code', unexpected)
    response = client.post('/api/v1/auth/login', json={'username': data['email'], 'password': data['password']})
    assert response.status_code == 200 and 'access_token' in response.json()
    person = db.scalar(select(Complainant).where(Complainant.email == data['email']))
    assert (person.address_line_1, person.city, person.province) == ('12 Test Road', 'Durban', 'KwaZulu-Natal')


def test_recovery_generic_single_use_expiry_and_session_revocation(auth_context, monkeypatch):
    client, db = auth_context
    data, _, tokens = enroll(client)
    delivered = []
    def send(recipient, subject, body, identity):
        delivered.append(body.split('\n\n')[1])
        return 'ACCEPTED', 'SMTP_ACCEPTED'
    monkeypatch.setattr(mail, 'send_case_message', send)
    url = '/api/v1/auth/forgot-password'
    response = client.post(url, json={'email': data['email']})
    assert response.json() == client.post(url, json={'email': 'missing@example.com'}).json()
    assert client.post(url, json={'email': data['email']}).status_code == 200 and len(delivered) == 1
    reset = {'token': delivered[0], 'password': 'Replacement-Password12!'}
    assert client.post('/api/v1/auth/reset-password', json=reset | {'password': 'weak'}).status_code == 422
    assert client.post('/api/v1/auth/reset-password', json=reset).status_code == 200
    assert client.post('/api/v1/auth/reset-password', json=reset).status_code == 400
    assert client.get('/api/v1/auth/me', headers=authorization(tokens)).status_code == 401
    assert client.post('/api/v1/auth/login', json={'username': data['email'], 'password': data['password']}).status_code == 401
    assert client.post('/api/v1/auth/login', json={'username': data['email'], 'password': reset['password']}).status_code == 200
    row = db.scalar(select(PasswordReset).where(PasswordReset.token_hash == security.hash_refresh_token(delivered[0])))
    row.used_at = None
    row.expires_at = security.utcnow() - timedelta(seconds=1)
    db.commit()
    assert client.post('/api/v1/auth/reset-password', json=reset).status_code == 400


def test_vehicle_files_witnesses_ownership_and_download(auth_context, monkeypatch, tmp_path):
    client, db = auth_context
    monkeypatch.setattr(settings, 'evidence_storage_path', tmp_path)
    _, _, tokens = enroll(client)
    headers = authorization(tokens)
    data = payload(db, crime_category='Vehicle theft')
    assert client.post('/api/v1/complaints', headers=headers, json=data).status_code == 422
    data['vehicle_number_plate'] = 'ND 123 456'
    assert client.post('/api/v1/complaints', headers=headers, json=data).status_code == 422
    uploaded = client.post('/api/v1/complaints/uploads', headers=headers,
        data={'purpose': 'VEHICLE_REGISTRATION'}, files={'file': ('registration.pdf', b'%PDF-test', 'application/pdf')})
    assert uploaded.status_code == 201, uploaded.text
    upload_id = uploaded.json()['id']
    _, _, other = enroll(client)
    assert client.post('/api/v1/complaints', headers=authorization(other), json=data | {'upload_ids': [upload_id]}).status_code == 422
    data.update(upload_ids=[upload_id], witnesses=[{'first_name': 'Test', 'last_name': 'Witness', 'statement_text': 'I saw the incident.'}])
    result = client.post('/api/v1/complaints', headers=headers, json=data)
    assert result.status_code == 201, result.text
    complaint_id = result.json()['id']
    row = db.get(Complaint, uuid.UUID(complaint_id))
    assert row.vehicle_number_plate == 'ND 123 456'
    assert db.scalar(select(Witness.id).where(Witness.complaint_id == row.id))
    url = f'/api/v1/complaints/{complaint_id}/uploads/{upload_id}/download'
    downloaded = client.get(url, headers=headers)
    assert downloaded.status_code == 200 and downloaded.content == b'%PDF-test'
    assert downloaded.headers['content-disposition'].startswith('attachment;')
    assert client.get(url, headers=authorization(other)).status_code in (403, 404)
    assert client.post('/api/v1/complaints', headers=headers, json=data).status_code == 422
    audio = client.post('/api/v1/complaints/uploads', headers=headers, data={'complaint_id': complaint_id},
        files={'file': ('voice.webm', b'voice recording', 'audio/webm')})
    assert audio.status_code == 201
    monkeypatch.setattr(settings, 'evidence_max_file_bytes', 4)
    assert client.post('/api/v1/complaints/uploads', headers=headers,
        files={'file': ('large.mp4', b'12345', 'video/mp4')}).status_code == 413


def test_full_docket_includes_history_and_scopes_commander(investigation):
    client, db, docket, commander, investigator, officer, *_ = investigation
    assign(investigation)
    item = evidence(investigation)
    assert client.post(f'/api/v1/dockets/{docket}/notes', headers=investigator,
        json={'note_type': 'PROGRESS', 'content': 'Full docket note', 'is_sensitive': True}).status_code == 201
    assert client.post(f'/api/v1/dockets/{docket}/feedback', headers=investigator,
        json={'feedback_type': 'PROGRESS_UPDATE', 'subject': 'Progress', 'message': 'Official update'}).status_code == 201
    uploaded = client.post(f"/api/v1/evidence/{item}/files", headers=investigator,
        files={'file': ('evidence.txt', b'private evidence', 'text/plain')})
    assert uploaded.status_code == 201, uploaded.text
    url = f'/api/v1/dockets/{docket}/full'
    result = client.get(url, headers=commander)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body['assignments'] and body['approvals'] and body['decisions']
    assert body['investigation_notes'][0]['content'] == 'Full docket note'
    assert body['official_feedback'][0]['message'] == 'Official update'
    assert 'storage_key' not in result.text and 'identity_number_encrypted' not in result.text
    assert client.get(url, headers=investigator).status_code == 200
    downloaded = client.get(f"/api/v1/dockets/{docket}/files/{uploaded.json()['id']}/download", headers=commander)
    assert downloaded.status_code == 200 and downloaded.content == b'private evidence'
    assert client.get(f'/api/v1/dockets/{uuid.uuid4()}/full', headers=commander).status_code == 404
