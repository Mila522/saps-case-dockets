"""Credential-free service tests; PostgreSQL integration remains a separate gate."""
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import configure_mappers

from app.db import models  # noqa: F401
from app.modules.audit.models import AuditLog
from app.modules.communications.models import CaseFeedback
from app.modules.feedback.schemas import FeedbackCreate
from app.modules.feedback.service import FeedbackService


@pytest.fixture
def context():
    configure_mappers()
    db = Mock()
    service = FeedbackService(db)
    service.repo = Mock()
    user = SimpleNamespace(id=uuid4(), is_active=True)
    complaint = SimpleNamespace(id=uuid4(), complainant_id=uuid4(), station_id=uuid4())
    docket = SimpleNamespace(id=uuid4())
    officer = SimpleNamespace(id=uuid4(), station_id=complaint.station_id)
    service.repo.permissions.return_value = {'feedback.provide', 'docket.view_assigned'}
    service.repo.context.return_value = (docket, complaint)
    service.repo.officer.return_value = officer
    service.repo.assigned.return_value = True
    service.repo.owner.return_value = False
    service.repo.superseded.return_value = False
    data = FeedbackCreate(feedback_type='PROGRESS_UPDATE', subject='Progress', message='An update')
    return service, db, user, docket, complaint, officer, data


def test_publish_derives_identity_and_audits_without_content(context):
    service, db, user, docket, complaint, officer, data = context
    result = service.publish(user, docket.id, data)
    assert result.complainant_id == complaint.complainant_id
    assert result.provided_by_officer_id == officer.id
    assert result.is_official
    rows = [call.args[0] for call in db.add.call_args_list]
    assert isinstance(rows[0], CaseFeedback)
    assert isinstance(rows[1], AuditLog)
    assert rows[1].actor_user_id == user.id and rows[1].action == 'feedback.published'
    assert rows[1].new_values is None and rows[1].event_metadata is None
    service.repo.assigned.assert_called_once_with(docket.id, officer.id, lock=True)
    db.commit.assert_called_once()


@pytest.mark.parametrize('denial', ['permission', 'assignment', 'station', 'officer', 'inactive', 'missing'])
def test_publish_denials_do_not_write(context, denial):
    service, db, user, docket, complaint, officer, data = context
    if denial == 'permission': service.repo.permissions.return_value = set()
    if denial == 'assignment': service.repo.assigned.return_value = False
    if denial == 'station': officer.station_id = uuid4()
    if denial == 'officer': service.repo.officer.return_value = None
    if denial == 'inactive': user.is_active = False
    if denial == 'missing': service.repo.context.return_value = None
    with pytest.raises(HTTPException) as exc:
        service.publish(user, docket.id, data)
    assert exc.value.status_code in {403, 404}
    db.add.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_called_once()


def test_owner_can_read_and_sensitive_read_is_audited(context):
    service, db, user, docket, complaint, officer, data = context
    service.repo.permissions.return_value = {'case.track_own'}
    service.repo.owner.return_value = True
    service.repo.list.return_value = []
    result = service.list(user, docket.id, 10, 20)
    assert result.items == []
    service.repo.list.assert_called_once_with(docket.id, complaint.complainant_id, 10, 20)
    assert db.add.call_args.args[0].action == 'feedback.listed'
    db.commit.assert_called_once()


def test_other_complainant_cannot_read(context):
    service, db, user, docket, *_ = context
    service.repo.permissions.return_value = {'case.track_own'}
    with pytest.raises(HTTPException) as exc:
        service.list(user, docket.id)
    assert exc.value.status_code == 404
    service.repo.list.assert_not_called()


def test_correction_appends_and_never_mutates_old_row(context):
    service, db, user, docket, complaint, officer, data = context
    old = SimpleNamespace(id=uuid4(), message='Original')
    service.repo.get.return_value = old
    data.supersedes_feedback_id = old.id
    result = service.publish(user, docket.id, data)
    assert result.supersedes_feedback_id == old.id and result.id != old.id
    assert old.message == 'Original'
    service.repo.get.assert_called_once_with(docket.id, old.id, complaint.complainant_id)


@pytest.mark.parametrize('missing', [True, False])
def test_invalid_correction_rejected(context, missing):
    service, db, user, docket, complaint, officer, data = context
    data.supersedes_feedback_id = uuid4()
    service.repo.get.return_value = None if missing else SimpleNamespace(id=data.supersedes_feedback_id)
    service.repo.superseded.return_value = not missing
    with pytest.raises(HTTPException) as exc:
        service.publish(user, docket.id, data)
    assert exc.value.status_code == (404 if missing else 409)
    db.add.assert_not_called()


@pytest.mark.parametrize('operation', ['publish', 'list', 'get'])
def test_audit_failure_rolls_back_and_returns_no_result(context, operation):
    service, db, user, docket, complaint, officer, data = context
    row = service.publish(user, docket.id, data)
    db.reset_mock()
    service.repo.list.return_value = [row]
    service.repo.get.return_value = row
    service.audit = Mock(side_effect=SQLAlchemyError('private failure detail'))
    args = (data,) if operation == 'publish' else ((row.id,) if operation == 'get' else ())
    with pytest.raises(HTTPException) as exc:
        getattr(service, operation)(user, docket.id, *args)
    assert exc.value.status_code == 503
    assert 'private' not in exc.value.detail
    db.commit.assert_not_called()
    db.rollback.assert_called_once()


@pytest.mark.parametrize('limit,offset', [(0, 0), (101, 0), (10, -1)])
def test_invalid_pagination(context, limit, offset):
    service, db, user, docket, *_ = context
    with pytest.raises(HTTPException) as exc:
        service.list(user, docket.id, limit, offset)
    assert exc.value.status_code == 422
    service.repo.context.assert_not_called()


@pytest.mark.parametrize('change', [
    {'subject': '   '}, {'message': ''}, {'message': 'x' * 10001},
    {'subject': 'x' * 256}, {'feedback_type': 'INVALID'},
    {'provided_by_officer_id': str(uuid4())}, {'complainant_id': str(uuid4())},
    {'acknowledged_at': '2026-01-01'}, {'is_official': False},
])
def test_payload_rejects_blank_oversized_and_server_owned_fields(change):
    with pytest.raises(ValidationError):
        FeedbackCreate(**({'feedback_type': 'GENERAL', 'subject': 'Subject', 'message': 'Message'} | change))
