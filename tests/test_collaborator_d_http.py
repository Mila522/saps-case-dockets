"""HTTP contracts without connecting to PostgreSQL."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import settings
from app.db.session import get_db
from app.modules.authentication.dependencies import get_current_active_user, get_user_permissions
from app.modules.communications.notifications import NotificationService
from app.modules.communications.documents import DocumentService
from app.modules.communications.dashboards import DashboardService
from app.modules.alerts.service import AlertService


@pytest.fixture
def http_client():
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = lambda: Mock()
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def signed_in(permissions=()):
    app.dependency_overrides[get_current_active_user] = lambda: NS(id=uuid4(), is_active=True)
    app.dependency_overrides[get_user_permissions] = lambda: [NS(code=p) for p in permissions]


@pytest.mark.parametrize('path', ['/notifications', '/alerts', '/dashboards/summary',
    f'/documents/{uuid4()}/download', f'/complaints/{uuid4()}/documents'])
def test_new_routes_require_auth_and_disable_caching(http_client, path):
    result = http_client.get('/api/v1' + path)
    assert result.status_code == 401
    assert result.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('path', ['/alerts', f'/documents/{uuid4()}/download', f'/complaints/{uuid4()}/documents'])
def test_new_permission_routes_deny_missing_grant(http_client, path):
    signed_in()
    assert http_client.get('/api/v1' + path).status_code == 403


def test_notification_pagination_and_routing(http_client, monkeypatch):
    signed_in()
    listing = Mock(return_value=[])
    monkeypatch.setattr(NotificationService, 'list', listing)
    response = http_client.get('/api/v1/notifications?limit=7&offset=14')
    assert response.status_code == 200 and response.json() == []
    assert listing.call_args.args[1:] == (7, 14)
    for suffix in ('?limit=0', '?limit=101', '?offset=-1'):
        assert http_client.get('/api/v1/notifications' + suffix).status_code == 422


def test_document_download_is_attachment_and_sandboxed(http_client, monkeypatch):
    signed_in(['confirmation.download'])
    identifier = uuid4()
    monkeypatch.setattr(DocumentService, 'download', lambda *args: (b'<!doctype html><h1>Confirmation</h1>', identifier))
    response = http_client.get(f'/api/v1/documents/{identifier}/download')
    assert response.status_code == 200
    assert response.headers['content-disposition'] == f'attachment; filename="confirmation-{identifier}.html"'
    assert response.headers['content-security-policy'].startswith('sandbox;')
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['cache-control'] == 'no-store'


def test_document_payload_cannot_supply_author_or_number(http_client):
    signed_in(['confirmation.download'])
    response = http_client.post(f'/api/v1/complaints/{uuid4()}/documents', json={
        'document_type': 'COMPLAINT_REGISTRATION_CONFIRMATION', 'document_number': 'FORGED'})
    assert response.status_code == 422


def test_alert_evaluation_and_dashboard_routes(http_client, monkeypatch):
    signed_in(['alert.view'])
    monkeypatch.setattr(AlertService, 'evaluate', lambda *args: {'created': 0, 'alert_ids': []})
    monkeypatch.setattr(DashboardService, 'summary', lambda *args: {'scope': 'station'})
    assert http_client.post('/api/v1/alerts/evaluate').json()['created'] == 0
    assert http_client.get('/api/v1/dashboards/summary').json()['scope'] == 'station'
    assert http_client.get('/api/v1/alerts?status=INVALID').status_code == 422


@pytest.mark.parametrize('path', ['/notifications', '/alerts', '/dashboards/summary', f'/documents/{uuid4()}/download'])
def test_new_routes_enforce_production_https(http_client, monkeypatch, path):
    monkeypatch.setattr(settings, 'environment', 'production')
    response = http_client.get('/api/v1' + path)
    assert response.status_code == 400 and response.json()['detail'] == 'HTTPS is required'
