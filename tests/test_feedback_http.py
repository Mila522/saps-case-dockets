"""HTTP wiring checks in an isolated process; no real database or credentials."""
import subprocess
import sys


def test_feedback_http_contract():
    program = r'''
import os, secrets
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
from cryptography.fernet import Fernet
os.environ.update(DATABASE_URL='postgresql+psycopg://unused:unused@127.0.0.1:1/unused',
    MIGRATION_DATABASE_URL='postgresql+psycopg://unused:unused@127.0.0.1:1/unused',
    JWT_SECRET_KEY=secrets.token_urlsafe(48), MFA_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    ENVIRONMENT='test')
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from app.main import app
from app.db.session import get_db
from app.modules.authentication.dependencies import get_current_active_user, get_user_permissions
import app.modules.feedback.service as module
from app.modules.feedback.service import FeedbackService

db, repo = Mock(), Mock()
user = SimpleNamespace(id=uuid4(), is_active=True)
docket = SimpleNamespace(id=uuid4())
complaint = SimpleNamespace(id=uuid4(), complainant_id=uuid4(), station_id=uuid4())
officer = SimpleNamespace(id=uuid4(), station_id=complaint.station_id)
repo.permissions.return_value = {'feedback.provide', 'docket.view_assigned'}
repo.context.return_value = (docket, complaint)
repo.officer.return_value = officer
repo.assigned.return_value = True
repo.owner.return_value = False
repo.superseded.return_value = False
module.FeedbackRepository = lambda session: repo
app.dependency_overrides[get_db] = lambda: db
base = '/api/v1/dockets/' + str(docket.id) + '/feedback'
payload = {'feedback_type': 'GENERAL', 'subject': 'Update', 'message': 'Private narrative'}
with TestClient(app) as client:
    assert client.get(base).status_code == 401
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_user_permissions] = lambda: [SimpleNamespace(code='feedback.provide')]
    response = client.post(base, json=payload)
    assert response.status_code == 201, response.text
    assert response.headers['cache-control'] == 'no-store'
    row = response.json()
    assert row['provided_by_officer_id'] == str(officer.id)
    assert row['complainant_id'] == str(complaint.complainant_id)
    assert row['message'] == payload['message']
    repo.list.return_value = [SimpleNamespace(**{**row,
        'id': row['id'], 'published_at': row['published_at'], 'created_at': row['created_at']})]
    response = client.get(base + '?limit=1&offset=0')
    assert response.status_code == 200 and len(response.json()['items']) == 1
    repo.get.return_value = repo.list.return_value[0]
    assert client.get(base + '/' + row['id']).status_code == 200
    for suffix in ('?limit=0', '?limit=101', '?offset=-1'):
        assert client.get(base + suffix).status_code == 422
    response = client.post(base, json={**payload, 'provided_by_officer_id': str(uuid4())})
    assert response.status_code == 422 and 'Private narrative' not in response.text
    assert client.get('/api/v1/dockets/not-a-uuid/feedback').status_code == 422
    repo.assigned.return_value = False
    assert client.post(base, json=payload).status_code == 404
    repo.assigned.return_value = True
    repo.permissions.return_value = set()
    assert client.get(base).status_code == 403
    repo.permissions.return_value = {'feedback.provide', 'docket.view_assigned'}
    original = FeedbackService.audit
    def fail_audit(*args): raise SQLAlchemyError('sensitive internal failure')
    FeedbackService.audit = fail_audit
    db.reset_mock()
    response = client.post(base, json=payload)
    assert response.status_code == 503 and 'sensitive internal failure' not in response.text
    db.commit.assert_not_called()
    db.rollback.assert_called_once()
    FeedbackService.audit = original
    assert client.patch(base + '/' + row['id'], json=payload).status_code == 405
    assert client.delete(base + '/' + row['id']).status_code == 405
print('HTTP contract checks passed; database dependency mocked.')
'''
    result = subprocess.run([sys.executable, '-B', '-c', program], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
