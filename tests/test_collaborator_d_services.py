"""Isolated D contracts: scope, transactions, immutable files, and alert policy."""
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError

from app.db import models  # noqa: F401
from app.core.config import settings
from app.modules.authentication.security import utcnow
from app.modules.audit.models import AuditLog
from app.modules.communications.common import ScopedService
from app.modules.communications.documents import DocumentStorage, DocumentService, render_confirmation
from app.modules.communications.notifications import NotificationService, notify_complainant
from app.modules.communications.models import Notification, NotificationAttempt, OfficialDocument
from app.modules.communications.schemas import AlertTransition, DocumentRequest
from app.modules.communications.dashboards import DashboardService
from app.modules.alerts.service import AlertService
from app.modules.alerts.models import Alert


def user():
    return NS(id=uuid4(), is_active=True)


def complaint():
    return NS(id=uuid4(), complainant_id=uuid4(), station_id=uuid4(), reference_number='CMP-TEST-2026-000001')


def test_notification_delivery_is_atomic_and_redacted():
    db, parent, actor = Mock(), complaint(), user()
    row = notify_complainant(db, parent, 'complaint.registered', actor_user_id=actor.id)
    records = [call.args[0] for call in db.add.call_args_list]
    assert [type(record) for record in records] == [Notification, NotificationAttempt, AuditLog]
    assert row.recipient_complainant_id == parent.complainant_id
    assert row.recipient_user_id is None and row.status == 'DELIVERED'
    assert records[1].notification is row and records[1].attempt_number == 1
    assert records[2].event_metadata is None
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_notification_query_scopes_both_recipient_types():
    service, actor = NotificationService(Mock()), user()
    service.permissions = Mock(return_value=set())
    query = service.query(actor).compile(dialect=postgresql.dialect())
    sql = str(query)
    assert 'recipient_user_id' in sql and 'recipient_complainant_id IN (SELECT' in sql
    assert 'complainants.user_id =' in sql and 'notifications.channel =' in sql
    assert actor.id in query.params.values()


def test_notification_missing_is_not_disclosed_and_rolls_back():
    db, actor = Mock(), user()
    db.scalar.return_value = None
    service = NotificationService(db)
    service.permissions = Mock(return_value=set())
    with pytest.raises(HTTPException) as exc:
        service.get(actor, uuid4())
    assert exc.value.status_code == 404
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_sensitive_read_audit_failure_prevents_return():
    db, actor = Mock(), user()
    service = NotificationService(db)
    service.permissions = Mock(return_value=set())
    db.scalars.return_value = []
    db.commit.side_effect = SQLAlchemyError('private database details')
    with pytest.raises(HTTPException) as exc:
        service.list(actor, 50, 0)
    assert exc.value.status_code == 503 and 'private' not in exc.value.detail
    db.rollback.assert_called_once()


@pytest.mark.parametrize('permission,owned,assigned,same_station,allowed', [
    ('case.track_own', True, False, False, True),
    ('case.track_own', False, False, False, False),
    ('docket.view_assigned', False, True, True, True),
    ('docket.view_assigned', False, False, True, False),
    ('docket.view_assigned', False, True, False, False),
    ('complaint.view_station', False, False, True, True),
    ('complaint.view_station', False, False, False, False),
])
def test_document_access_scope(permission, owned, assigned, same_station, allowed):
    db, actor, parent = Mock(), user(), complaint()
    db.get.return_value = parent
    db.scalar.side_effect = [uuid4() if owned else None, uuid4() if assigned else None]
    service = ScopedService(db)
    service.permissions = Mock(return_value={'confirmation.download', permission})
    service.officer = Mock(return_value=NS(id=uuid4(), station_id=parent.station_id if same_station else uuid4()))
    if allowed:
        assert service.complaint_scope(actor, parent.id) is parent
    else:
        with pytest.raises(HTTPException) as exc:
            service.complaint_scope(actor, parent.id)
        assert exc.value.status_code == 404


def test_document_requires_download_permission_even_for_owner():
    service = ScopedService(Mock())
    service.permissions = Mock(return_value={'case.track_own'})
    with pytest.raises(HTTPException) as exc:
        service.complaint_scope(user(), uuid4())
    assert exc.value.status_code == 403


def test_document_render_escapes_data_and_excludes_case_narrative():
    parent = complaint()
    parent.reference_number = '<script>secret</script>'
    parent.incident_description = 'DO NOT PUBLISH'
    content = render_confirmation('DOC-1', 'REFUSAL_CONFIRMATION', parent, None, utcnow(), '<b>reason</b>')
    assert b'<script>' not in content and b'&lt;script&gt;' in content
    assert b'&lt;b&gt;reason' in content and b'DO NOT PUBLISH' not in content


def test_document_storage_integrity_and_path_traversal(tmp_path, monkeypatch):
    import io
    monkeypatch.setattr(settings, 'document_storage_path', tmp_path)
    storage = DocumentStorage()
    key, size, digest = storage.save(io.BytesIO(b'private confirmation'))
    row = NS(storage_key=key, file_size_bytes=size, sha256_hash=digest)
    assert storage.read_verified(row) == b'private confirmation'
    storage.path(key).write_bytes(b'tampered')
    with pytest.raises(HTTPException) as exc:
        storage.read_verified(row)
    assert exc.value.status_code == 409
    for invalid in ('../outside', '/absolute', 'https://example.com', 'C:\\secret'):
        with pytest.raises(HTTPException):
            storage.path(invalid)


def document_context(monkeypatch):
    import app.modules.communications.documents as module
    db, actor, parent = Mock(), user(), complaint()
    service, storage = DocumentService(db), Mock()
    service.complaint_scope = Mock(return_value=parent)
    db.scalar.side_effect = [parent, None, None]
    monkeypatch.setattr(module, 'allocate_document_number', Mock(return_value='DOC-X-2026-000001'))
    monkeypatch.setattr(module, 'DocumentStorage', lambda: storage)
    storage.save.return_value = ('a' * 32, 20, 'b' * 64)
    return service, db, actor, parent, storage


def test_document_generate_shared_number_and_audit(monkeypatch):
    service, db, actor, parent, storage = document_context(monkeypatch)
    row = service.generate(actor, parent.id, DocumentRequest(document_type='COMPLAINT_REGISTRATION_CONFIRMATION'))
    assert row.document_number == 'DOC-X-2026-000001'
    assert 'storage_key' not in row.model_dump()
    assert [type(c.args[0]) for c in db.add.call_args_list] == [OfficialDocument, AuditLog]
    db.commit.assert_called_once()


def test_document_flush_failure_cleans_file_and_rolls_back(monkeypatch):
    service, db, actor, parent, storage = document_context(monkeypatch)
    db.flush.side_effect = SQLAlchemyError('failure')
    with pytest.raises(HTTPException):
        service.generate(actor, parent.id, DocumentRequest(document_type='COMPLAINT_REGISTRATION_CONFIRMATION'))
    storage.discard.assert_called_once_with('a' * 32)
    db.rollback.assert_called_once()


def test_document_ambiguous_commit_keeps_file(monkeypatch):
    service, db, actor, parent, storage = document_context(monkeypatch)
    db.commit.side_effect = SQLAlchemyError('lost connection')
    with pytest.raises(HTTPException):
        service.generate(actor, parent.id, DocumentRequest(document_type='COMPLAINT_REGISTRATION_CONFIRMATION'))
    storage.discard.assert_not_called()


@pytest.mark.parametrize('kind', ['DOCKET_REGISTRATION_CONFIRMATION', 'CASE_CLOSURE_CONFIRMATION', 'REFUSAL_CONFIRMATION'])
def test_document_rejects_inapplicable_confirmation(monkeypatch, kind):
    service, db, actor, parent, storage = document_context(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        service.generate(actor, parent.id, DocumentRequest(document_type=kind))
    assert exc.value.status_code == 409
    storage.save.assert_not_called()


@pytest.mark.parametrize('current,target,allowed', [('OPEN', 'ACKNOWLEDGED', True),
    ('ACKNOWLEDGED', 'RESOLVED', True), ('OPEN', 'DISMISSED', True), ('RESOLVED', 'ACKNOWLEDGED', False)])
def test_alert_transition_attribution_and_conflicts(current, target, allowed):
    db, actor = Mock(), user()
    station = uuid4()
    row = Alert(id=uuid4(), station_id=station, alert_type='CASE_INACTIVITY', status=current,
        severity='MEDIUM', title='Inactive', description='No activity', detected_at=utcnow())
    db.scalar.return_value = row
    service = AlertService(db)
    service.require = Mock()
    service.management_scope = Mock(return_value=station)
    data = AlertTransition(expected_status=current if current != 'RESOLVED' else 'OPEN', status=target, notes='Reviewed')
    if allowed:
        result = service.transition(actor, row.id, data)
        assert result.status == target and result.assigned_to_user_id == actor.id
        assert (result.acknowledged_by_user_id if target == 'ACKNOWLEDGED' else result.resolved_by_user_id) == actor.id
        db.commit.assert_called_once()
    else:
        with pytest.raises(HTTPException) as exc:
            service.transition(actor, row.id, data)
        assert exc.value.status_code == 409
        db.rollback.assert_called_once()


def test_alert_assigned_to_other_user_denied():
    db, actor = Mock(), user()
    db.scalar.return_value = NS(assigned_to_user_id=uuid4())
    service = AlertService(db)
    service.require = Mock()
    service.management_scope = Mock(return_value=uuid4())
    with pytest.raises(HTTPException) as exc:
        service.transition(actor, uuid4(), AlertTransition(expected_status='OPEN', status='ACKNOWLEDGED', notes='Review'))
    assert exc.value.status_code == 403


def test_alert_evaluation_uses_deadlines_and_deduplicates():
    db, actor, parent = Mock(), user(), complaint()
    service = AlertService(db)
    service.require = Mock()
    service.management_scope = Mock(return_value=parent.station_id)
    old = utcnow() - timedelta(days=10)
    docket = NS(id=uuid4(), status='PENDING_APPROVAL', opened_at=old)
    db.execute.side_effect = [[], [], [(parent, docket)], [], [], [(parent, docket)]]
    db.scalar.side_effect = [parent.station_id, None, parent.station_id, uuid4()]
    assert service.evaluate(actor)['created'] == 1
    assert service.evaluate(actor)['created'] == 0
    rows = [c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], Alert)]
    assert len(rows) == 1 and rows[0].alert_type == 'OVERDUE_DOCKET'
    assert rows[0].due_at == old + timedelta(hours=settings.alert_docket_approval_hours)


def test_dashboard_scopes_every_aggregate_and_audits():
    db, actor, station = Mock(), user(), uuid4()
    service = DashboardService(db)
    service.management_scope = Mock(return_value=station)
    db.execute.return_value.all.return_value = [('OPEN', 3)]
    db.scalar.return_value = 2
    result = service.summary(actor)
    assert result['scope'] == 'station' and result['unassigned_open_dockets'] == 2
    for call in db.execute.call_args_list + db.scalar.call_args_list:
        compiled = call.args[0].compile(dialect=postgresql.dialect())
        assert station in compiled.params.values()
    db.commit.assert_called_once()
    assert db.add.call_args.args[0].action == 'dashboard.view'


@pytest.mark.parametrize('payload', [{'status': 'RESOLVED', 'expected_status': 'OPEN', 'notes': ' '},
    {'status': 'OPEN', 'expected_status': 'OPEN', 'notes': 'Reopen'},
    {'status': 'RESOLVED', 'expected_status': 'OPEN', 'notes': 'Done', 'station_id': str(uuid4())}])
def test_alert_input_cannot_inject_scope_or_blank_reason(payload):
    with pytest.raises(ValidationError):
        AlertTransition(**payload)


@pytest.mark.parametrize('role,permissions,officer,write,allowed,global_scope', [
    ('SAPS_MANAGEMENT', {'dashboard.view_all'}, False, False, True, True),
    ('SYSTEM_ADMINISTRATOR', {'dashboard.view_all'}, False, False, True, True),
    ('NCC_OFFICER', {'dashboard.view_all'}, False, False, True, True),
    ('COMPLAINANT', {'dashboard.view_all'}, False, False, False, False),
    ('STATION_COMMANDER', {'dashboard.view_station'}, True, False, True, False),
    ('STATION_COMMANDER', {'dashboard.view_station'}, False, False, False, False),
    ('STATION_COMMANDER', {'alert.view'}, True, True, False, False),
    ('STATION_COMMANDER', {'docket.assign'}, True, True, True, False),
    ('SAPS_MANAGEMENT', {'dashboard.view_all', 'alert.view'}, False, True, False, False),
])
def test_management_scope_requires_role_and_profile(monkeypatch, role, permissions, officer, write, allowed, global_scope):
    import app.modules.communications.common as module
    actor, station = user(), uuid4()
    repository = Mock()
    repository.roles.return_value = [NS(code=role)]
    monkeypatch.setattr(module, 'AuthRepository', lambda db: repository)
    service = ScopedService(Mock())
    service.permissions = Mock(return_value=permissions)
    service.officer = Mock(return_value=NS(station_id=station) if officer else None)
    if allowed:
        assert service.management_scope(actor, write=write) == (None if global_scope else station)
    else:
        with pytest.raises(HTTPException) as exc:
            service.management_scope(actor, write=write)
        assert exc.value.status_code == 403


@pytest.mark.parametrize('kind', ['NON_COMPLIANT_REFUSAL', 'UNACKNOWLEDGED_ESCALATION', 'CASE_INACTIVITY'])
def test_alert_policy_detects_each_supported_condition(kind):
    db, actor, parent = Mock(), user(), complaint()
    service = AlertService(db)
    service.require = Mock()
    service.management_scope = Mock(return_value=parent.station_id)
    old = utcnow() - timedelta(days=400)
    source = NS(id=uuid4(), escalated_at=old)
    docket = NS(id=uuid4(), status='ACTIVE', opened_at=old, updated_at=old)
    rows = [[(parent, source)] if kind == 'NON_COMPLIANT_REFUSAL' else [],
            [(parent, source)] if kind == 'UNACKNOWLEDGED_ESCALATION' else [],
            [(parent, docket)] if kind == 'CASE_INACTIVITY' else []]
    db.execute.side_effect = rows
    db.scalar.side_effect = [parent.station_id] + ([None] * 5 if kind == 'CASE_INACTIVITY' else []) + [None]
    assert service.evaluate(actor)['created'] == 1
    alert = next(call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], Alert))
    assert alert.alert_type == kind and alert.station_id == parent.station_id
    assert alert.complaint_id == parent.id
