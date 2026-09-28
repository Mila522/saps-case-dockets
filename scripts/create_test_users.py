"""Explicit local-development fixtures for every role; never runs at startup.

Run with --credentials-file .cache/test-users.md. These synthetic accounts are
preverified for local testing; their reserved addresses cannot receive email.
Existing accounts are never reset or converted.
"""
import argparse
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.db import models  # noqa: F401
from app.db.session import SessionLocal
from app.modules.access.models import Role, User, UserRole
from app.modules.audit.models import AuditLog
from app.modules.authentication.models import UserEmailAuth
from app.modules.authentication.security import hash_password, utcnow
from app.modules.complainants.models import Complainant
from app.modules.stations.models import Officer
from app.modules.stations.seed_demo_officers import ensure_local, select_station, password

ACTION = 'auth.local_role_fixture_created'
SPECS = (
    ('test_complainant', 'COMPLAINANT'),
    ('test_charge_officer', 'CHARGE_OFFICER'),
    ('test_commander', 'STATION_COMMANDER'),
    ('test_investigator_1', 'INVESTIGATING_OFFICER'),
    ('test_investigator_2', 'INVESTIGATING_OFFICER'),
    ('test_ncc_officer', 'NCC_OFFICER'),
    ('test_management', 'SAPS_MANAGEMENT'),
    ('test_administrator', 'SYSTEM_ADMINISTRATOR'),
)


def provision(db):
    ensure_local()
    db.execute(text('SELECT pg_advisory_xact_lock(78432020)'))
    station = select_station(db)
    roles = {r.code: r for r in db.scalars(select(Role))}
    if set(roles) != {role for _, role in SPECS}:
        raise ValueError('Role catalog changed; update the fixture specs before provisioning.')
    results = []
    for username, code in SPECS:
        existing = db.scalar(select(User).where(User.username == username))
        if existing:
            marker = db.scalar(select(AuditLog.id).where(AuditLog.action == ACTION, AuditLog.entity_id == existing.id))
            assigned = set(db.scalars(select(Role.code).join(UserRole).where(UserRole.user_id == existing.id)))
            if not marker or assigned != {code}:
                raise ValueError(f'{username} already exists outside this fixture; no changes made.')
            results.append((username, code, None, station.name))
            continue
        temporary = password().get_secret_value()
        user = User(username=username, email=username + '@example.invalid', password_hash=hash_password(temporary),
                    is_active=True, is_verified=True, mfa_enabled=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=roles[code].id))
        db.add(UserEmailAuth(user_id=user.id, verified_email=user.email, verified_at=utcnow()))
        if code == 'COMPLAINANT':
            db.add(Complainant(user_id=user.id, first_name='Test', last_name='Complainant', email=user.email,
                phone_number='0000000000', preferred_contact_method='EMAIL', address_line_1='Test address',
                city=station.city or 'Durban', province=station.province))
        elif code in {'CHARGE_OFFICER', 'STATION_COMMANDER', 'INVESTIGATING_OFFICER', 'NCC_OFFICER'}:
            db.add(Officer(user_id=user.id, station_id=station.id, service_number='LOCAL-' + username.upper(),
                rank={'STATION_COMMANDER': 'Captain', 'INVESTIGATING_OFFICER': 'Detective'}.get(code, 'Officer'), is_active=True))
        db.add(AuditLog(actor_type='SYSTEM', action=ACTION, entity_type='user', entity_id=user.id, station_id=station.id))
        results.append((username, code, temporary, station.name))
    db.flush()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials-file', type=Path, required=True)
    args = parser.parse_args()
    ensure_local()
    target = args.credentials_file.resolve()
    root = (Path(__file__).resolve().parents[1] / '.cache').resolve()
    if not target.is_relative_to(root):
        raise SystemExit('Keep credentials under the git-ignored project .cache directory.')
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects an existing credential handoff from overwrites.
    with target.open('x', encoding='utf-8') as output:
        try:
            with SessionLocal() as db:
                rows = provision(db)
                output.write('# Local test accounts\n\nSynthetic, preverified development accounts. Do not use in production.\n'
                    'No email is sent during setup; example.invalid addresses cannot receive verification or recovery mail.\n\n'
                    '| Role | Username | Password | Station |\n| --- | --- | --- | --- |\n')
                for username, role, secret, station in rows:
                    output.write(f'| {role} | {username} | `{secret or "Existing password unchanged"}` | {station} |\n')
                output.write('\nComplainant: http://127.0.0.1:8000/portal/\n\nStaff: http://127.0.0.1:8000/officer/\n')
                output.flush()
                db.commit()
        except (ValueError, SQLAlchemyError):
            # Credentials may have been written before an ambiguous DB commit;
            # retain the handoff but make the outcome explicit for the operator.
            output.write('\nSETUP FAILED: verify database state before using these credentials.\n')
            raise SystemExit('Local fixture setup failed; existing accounts were not changed.') from None
    print(f'Prepared {len(rows)} test accounts. Credentials: {target}')
    for username, role, secret, station in rows:
        print(f'{username} | {role} | {"created" if secret else "already exists"} | {station}')


if __name__ == '__main__':
    main()
