"""C workspace delivery and the contracts required by its browser forms."""
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.main import app
from app.modules.access.models import Permission, Role, RolePermission
from test_decisions_dockets import workflow_context
from test_investigation_api import investigation, assign, evidence

ROOT = Path(__file__).resolve().parents[1]


def test_investigator_pages_assets_and_api_coexist():
    with TestClient(app) as client:
        for path in ('/investigator/', '/investigator/docket', '/investigator/evidence'):
            response = client.get(path)
            assert response.status_code == 200
            assert 'Not an official SAPS product' in response.text
            assert response.headers['cache-control'] == 'no-store'
            assert "frame-ancestors 'none'" in response.headers['content-security-policy']
        assert client.get('/investigator', follow_redirects=False).status_code == 307
        for asset in (ROOT / 'frontend/investigator/assets').iterdir():
            response = client.get('/investigator/assets/' + asset.name)
            assert response.status_code == 200
            if asset.suffix == '.mjs':
                assert response.headers['content-type'].startswith('text/javascript')
        for path in ('/portal/api.mjs', '/officer/assets/styles.css', '/portal/', '/officer/', '/docs', '/redoc', '/openapi.json'):
            assert client.get(path).status_code == 200
        assert client.get('/api/v1/investigations/dockets').status_code == 401
        assert '/health/database' in client.get('/openapi.json').json()['paths']


def test_frontend_contract_enums_and_no_embedded_secrets():
    schema = app.openapi()['components']['schemas']
    source = (ROOT / 'frontend/investigator/assets/contracts.mjs').read_text()
    for name, model, field in [('NOTE_TYPES','NoteRequest','note_type'),
                               ('EVIDENCE_TYPES','EvidenceRequest','evidence_type'),
                               ('CUSTODY_TYPES','CustodyRequest','event_type')]:
        values = re.search(r'export const ' + name + r' = (\[.*?\]);', source).group(1)
        assert json.loads(values.replace("'", '"')) == schema[model]['properties'][field]['enum']
    for path in (ROOT / 'frontend/investigator').rglob('*'):
        if not path.is_file():
            continue
        content = path.read_text(encoding='utf-8')
        assert 'localStorage' not in content and 'console.log' not in content
        assert not re.search(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.', content)
        assert not re.search(r'(?:password|access_token|refresh_token)\s*[:=]\s*[\'\"][^\'\"]+', content)
        assert not re.search(r'https?://', content)


def test_investigator_detail_history_and_candidates_are_scoped(investigation):
    client, db, docket, _, headers, officer, other_headers, other, station = investigation
    assign(investigation)
    detail = client.get(f'/api/v1/investigations/dockets/{docket}', headers=headers).json()
    assert detail['station_name'] == station.name
    assert detail['incident_description']
    assert detail['allowed_next_statuses'] == ['ON_HOLD', 'CLOSED']
    assert client.get(f'/api/v1/investigations/dockets/{docket}/status-history', headers=headers).json()
    item = evidence(investigation)
    candidates = client.get(f'/api/v1/evidence/{item}/custodians', headers=headers)
    assert candidates.status_code == 200
    assert {row['id'] for row in candidates.json()} == {str(officer.id), str(other.id)}
    for url in (f'/api/v1/investigations/dockets/{docket}/status-history', f'/api/v1/evidence/{item}/custodians'):
        assert client.get(url, headers=other_headers).status_code == 404


def test_allowed_statuses_respect_closure_permission(investigation):
    client, db, docket, _, headers, *_ = investigation
    assign(investigation)
    # Fixture-only change, rolled back by workflow_context; no production grants change.
    from sqlalchemy import text
    db.execute(text('RESET ROLE'))
    role = db.scalar(select(Role.id).where(Role.code == 'INVESTIGATING_OFFICER'))
    permission = db.scalar(select(Permission.id).where(Permission.code == 'case.close'))
    db.execute(delete(RolePermission).where(RolePermission.role_id == role, RolePermission.permission_id == permission))
    db.commit()
    db.execute(text('SET LOCAL ROLE saps_api'))
    db.commit()
    detail = client.get(f'/api/v1/investigations/dockets/{docket}', headers=headers).json()
    assert detail['allowed_next_statuses'] == ['ON_HOLD']
    assert client.post(f'/api/v1/dockets/{docket}/status', headers=headers, json={
        'expected_status': 'ACTIVE', 'status': 'CLOSED', 'reason': 'Attempt'}).status_code == 403
