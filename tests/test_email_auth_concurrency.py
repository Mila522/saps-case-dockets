from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import time
import uuid

from fastapi import HTTPException
from sqlalchemy import select, text, func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.access.models import User
from app.modules.authentication import security
from app.modules.authentication.models import AuthSession, EmailChallenge, UserEmailAuth
from app.modules.authentication.service import AuthService
from auth_mailbox import code_for
from test_investigation_concurrency import race_database


def setup(engine):
    with Session(engine) as db:
        user = User(username=uuid.uuid4().hex, email=uuid.uuid4().hex+'@example.invalid',password_hash='!')
        db.add(user);db.flush()
        challenge = AuthService(db).challenge(user)
        db.commit()
        return user.id, challenge.challenge_token, code_for(user.email)


def race(engine, user_id, operation):
    def worker():
        with Session(engine) as db:
            db.execute(text('SET LOCAL ROLE saps_api'))
            db.execute(text("SET LOCAL lock_timeout='10s'"))
            try:
                operation(AuthService(db))
                return 200
            except HTTPException as error:
                return error.status_code
    with engine.connect() as blocker:
        tx = blocker.begin()
        blocker.execute(select(User.id).where(User.id == user_id).with_for_update())
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(worker) for _ in range(2)]
            try:
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    with engine.connect() as observer:
                        waiting = observer.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname=:name AND wait_event_type='Lock'"),{'name':engine.url.database}).scalar_one()
                    if waiting >= 2:break
                    time.sleep(.02)
                assert waiting >= 2
            finally:tx.rollback()
            return sorted(job.result(timeout=20) for job in jobs)


def test_concurrent_verify_single_token_session(race_database):
    user_id, token, code = setup(race_database)
    assert race(race_database, user_id, lambda auth:auth.verify_email(token,code)) == [200,401]
    with Session(race_database) as db:
        assert db.get(UserEmailAuth, user_id)
        assert db.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.user_id==user_id,AuthSession.revoked_at.is_(None))) == 1


def test_concurrent_resend_one_new_code(race_database, monkeypatch):
    user_id, token, _ = setup(race_database)
    monkeypatch.setattr(settings, 'email_resend_seconds', 60)
    with Session(race_database) as db:
        row = db.scalar(select(EmailChallenge).where(EmailChallenge.user_id==user_id))
        row.attempted_at -= timedelta(seconds=61);db.commit()
    assert race(race_database, user_id, lambda auth:auth.resend_email(token)) == [200,401]
    with Session(race_database) as db:
        assert db.scalar(select(func.count()).select_from(EmailChallenge).where(EmailChallenge.user_id==user_id)) == 2
        assert db.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.user_id==user_id,AuthSession.revoked_at.is_(None))) == 1
