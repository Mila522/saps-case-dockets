"""Separate PostgreSQL connections exercise email deduplication and worker claims."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from test_investigation_concurrency import race_database, race_case
from app.modules.access.models import User
from app.modules.authentication.models import UserEmailAuth
from app.modules.authentication.security import utcnow
from app.modules.authentication import mail
from app.modules.complainants.models import Complainant
from app.modules.complaints.models import Complaint
from app.modules.dockets.models import Docket
from app.modules.communications import case_email
from app.modules.communications.email_models import CaseEmailIntent
from app.modules.communications.models import Notification, NotificationAttempt


@pytest.fixture
def email_case(race_database, race_case):
    docket_id, user_id, _ = race_case
    with Session(race_database) as db:
        docket = db.get(Docket, docket_id)
        row = db.get(Complaint, docket.complaint_id)
        person = db.get(Complainant, row.complainant_id)
        user = db.get(User, user_id)
        person.user_id, person.email = user_id, user.email
        db.add(UserEmailAuth(user_id=user_id, verified_email=user.email, verified_at=utcnow()))
        db.commit()
        return row.id


def test_duplicate_event_and_two_workers_send_once(race_database, email_case, monkeypatch):
    barrier = Barrier(2)
    def enqueue():
        with Session(race_database) as db:
            row = db.get(Complaint, email_case)
            barrier.wait(timeout=10)
            identity = case_email.enqueue(db,row,None,'complaint.registered',row.id)
            db.commit()
            return identity
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(enqueue) for _ in range(2)]
        identities = [future.result(timeout=15) for future in futures]
    assert identities[0] == identities[1]
    entered, release = Event(), Event()
    calls = []
    def send(*args):
        calls.append(args)
        entered.set()
        assert release.wait(10)
        return 'ACCEPTED','SMTP_ACCEPTED'
    monkeypatch.setattr(mail,'send_case_message',send)
    def process():
        with Session(race_database) as db:
            return case_email.process_one(db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(process)
        try:
            assert entered.wait(10)
            assert pool.submit(process).result(timeout=10) is False
        finally:
            release.set()
        assert first.result(timeout=10)
    assert len(calls)==1
    with Session(race_database) as db:
        assert db.get(Notification,identities[0]).status=='SENT'
        assert db.scalar(select(func.count()).select_from(CaseEmailIntent))==1
        assert db.scalar(select(func.count()).select_from(NotificationAttempt).where(
            NotificationAttempt.notification_id==identities[0]))==1


