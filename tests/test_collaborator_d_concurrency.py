"""D race checks use C's disposable Alembic-migrated database fixture."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from test_investigation_concurrency import race_database, race_case, race
from app.core.config import settings
from app.modules.access.models import User
from app.modules.communications.documents import DocumentService
from app.modules.communications.models import CaseFeedback, OfficialDocument
from app.modules.communications.schemas import DocumentRequest
from app.modules.dockets.models import Docket
from app.modules.feedback.schemas import FeedbackCreate
from app.modules.feedback.service import FeedbackService
from app.modules.system.models import IdentifierCounter


def test_concurrent_feedback_corrections_have_one_winner(race_database, race_case):
    docket_id, user_id, _ = race_case
    with Session(race_database) as db:
        original = FeedbackService(db).publish(db.get(User, user_id), docket_id,
            FeedbackCreate(feedback_type='GENERAL', subject='Update', message='Original'))
    correction = FeedbackCreate(feedback_type='GENERAL', subject='Correction', message='Corrected',
        supersedes_feedback_id=original.id)
    operations = [lambda db: FeedbackService(db).publish(db.get(User, user_id), docket_id, correction)] * 2
    assert sorted(race(race_database, docket_id, operations)) == [200, 409]
    with Session(race_database) as db:
        assert db.scalar(select(func.count()).select_from(CaseFeedback).where(
            CaseFeedback.supersedes_feedback_id == original.id)) == 1
        assert db.get(CaseFeedback, original.id).message == 'Original'


def test_concurrent_confirmation_generation_uses_one_document_number(race_database, race_case, tmp_path, monkeypatch):
    docket_id, user_id, _ = race_case
    monkeypatch.setattr(settings, 'document_storage_path', tmp_path / 'documents')
    with Session(race_database) as db:
        complaint_id = db.get(Docket, docket_id).complaint_id
    barrier = Barrier(2)

    def generate():
        with Session(race_database) as db:
            db.execute(text('SET LOCAL ROLE saps_api'))
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            actor = db.get(User, user_id)
            barrier.wait(timeout=10)
            return DocumentService(db).generate(actor, complaint_id,
                DocumentRequest(document_type='DOCKET_REGISTRATION_CONFIRMATION')).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(generate) for _ in range(2)]
        identifiers = [future.result(timeout=20) for future in futures]
    assert identifiers[0] == identifiers[1]
    with Session(race_database) as db:
        assert db.scalar(select(func.count()).select_from(OfficialDocument).where(
            OfficialDocument.complaint_id == complaint_id)) == 1
    assert len(list((tmp_path / 'documents').iterdir())) == 1
