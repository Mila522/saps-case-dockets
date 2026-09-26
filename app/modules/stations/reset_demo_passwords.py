"""Explicit local demo password recovery; passwords only appear in the user's terminal."""
import sys

from sqlalchemy import select, text, update
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import SessionLocal
from app.modules.access.models import User
from app.modules.audit.models import AuditLog
from app.modules.authentication.models import AuthSession
from app.modules.authentication.security import hash_password, utcnow
from app.modules.stations.models import Officer, Station
from app.modules.stations import seed_demo_officers as demo

ACTION = 'auth.demo_officer_password_reset'


def reset_passwords(db):
    """Caller commits all three resets together. Never log or persist plaintext."""
    demo.ensure_local()
    db.execute(text('SELECT pg_advisory_xact_lock(78432019)'))
    accounts = []
    for spec in demo.SPECS:
        user = db.scalar(select(User).where(User.username == spec[0]).with_for_update())
        officer = db.scalar(select(Officer).where(Officer.user_id == user.id)) if user else None
        station = db.get(Station, officer.station_id) if officer else None
        if station is None or not station.is_active:
            raise ValueError(f'{spec[0]} is missing an active station assignment; no passwords reset.')
        demo.existing_user(db, spec, station)  # Exact role/service/provisioning marker.
        accounts.append((spec, user, station))
    results = []
    now = utcnow()
    for spec, user, station in accounts:
        temporary = demo.password()
        user.password_hash = hash_password(temporary.get_secret_value())
        user.failed_login_attempts = 0
        user.locked_until = None
        db.execute(update(AuthSession).where(AuthSession.user_id == user.id,
            AuthSession.revoked_at.is_(None)).values(revoked_at=now))
        db.add(AuditLog(actor_type='SYSTEM', action=ACTION, entity_type='user',
            entity_id=user.id, station_id=station.id))
        results.append(demo.AccountResult(user.username, spec[1], station.name, temporary))
    db.flush()
    return results


def main():
    try:
        demo.ensure_local()
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise ValueError('Run directly in a local interactive terminal without redirection or transcripts.')
        with SessionLocal() as db:
            with db.begin():
                results = reset_passwords(db)
        print('Passwords reset. Save these now; they are not stored in plaintext. Email codes remain required.')
        for result in results:
            print(f'\n{result.username} | {result.role} | {result.station}')
            print('New password: ' + result.temporary_password.get_secret_value())
        print('\nSign in at http://127.0.0.1:8000/officer/ . Existing sessions have been revoked.')
    except ValueError as error:
        raise SystemExit(str(error)) from None
    except SQLAlchemyError:
        raise SystemExit('Reset failed and rolled back. Check local database access; no credentials logged.') from None


if __name__ == '__main__':
    main()
