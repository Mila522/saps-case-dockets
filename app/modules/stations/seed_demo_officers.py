"""Interactive local-only officer provisioning. Never invoked at startup."""
import argparse
from dataclasses import dataclass
import secrets
import string
import sys

from pydantic import EmailStr, SecretStr, TypeAdapter, ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.db import models  # noqa: F401
from app.db.session import SessionLocal
from app.modules.access.models import Role, User, UserRole
from app.modules.audit.models import AuditLog
from app.modules.authentication.security import hash_password
from app.modules.stations.models import Officer, Station

DEFAULT_EMAIL = 'milatwantwa522@yahoo.com'
ACTION = 'auth.demo_officer_created'
SPECS = (
    ('demo_charge_officer', 'CHARGE_OFFICER', 'Charge Officer', 'DEMO-LOCAL-CHARGE'),
    ('demo_station_commander', 'STATION_COMMANDER', 'Captain', 'DEMO-LOCAL-COMMANDER'),
    ('demo_investigator', 'INVESTIGATING_OFFICER', 'Detective', 'DEMO-LOCAL-INVESTIGATOR'),
)


def ensure_local():
    if settings.environment != 'development' or make_url(settings.database_url).host not in {'localhost', '127.0.0.1', '::1'}:
        raise ValueError('Demo provisioning requires ENVIRONMENT=development and a loopback local database host.')


def valid_email(value):
    try:
        email = str(TypeAdapter(EmailStr).validate_python(value.strip())).lower()
        if len(email) > 254: raise ValueError()
        return email
    except (ValidationError, ValueError):
        raise ValueError('Enter a valid email address of at most 254 characters.') from None


def password():
    # Every generated value meets the same strength policy as registration.
    alphabet = string.ascii_letters + string.digits + '!@#$%*-_+'
    value = [secrets.choice(group) for group in (string.ascii_lowercase, string.ascii_uppercase, string.digits, '!@#$%*-_+')]
    value += [secrets.choice(alphabet) for _ in range(24)]
    secrets.SystemRandom().shuffle(value)
    return SecretStr(''.join(value))


def select_station(db):
    stations = list(db.scalars(select(Station).where(Station.is_active.is_(True)).with_for_update(read=True)))
    if not stations: raise ValueError('No active stations exist. Run the KZN station seed first.')
    return min(stations, key=lambda row: (
        row.province.casefold() not in {'kwazulu-natal', 'kzn'},
        row.name.strip().casefold() != 'point', row.name.casefold(), str(row.id)))


def existing_user(db, spec, station):
    username, role_code, _, service_number = spec
    user = db.scalar(select(User).where(func.lower(User.username) == username))
    if user is None:
        if db.scalar(select(Officer.id).where(Officer.service_number == service_number)):
            raise ValueError(f'Service-number collision for {username}; no existing account changed.')
        return None
    officer = db.scalar(select(Officer).where(Officer.user_id == user.id))
    roles = set(db.scalars(select(Role.code).join(UserRole).where(UserRole.user_id == user.id)))
    marker = db.scalar(select(AuditLog.id).where(AuditLog.action == ACTION, AuditLog.entity_id == user.id))
    if (not marker or not user.is_active or not officer or not officer.is_active
            or officer.service_number != service_number or officer.station_id != station.id or roles != {role_code}):
        raise ValueError(f'{username} already exists with different provisioning/state. No roles, station, email or password will be changed.')
    return user


@dataclass
class AccountResult:
    username: str
    role: str
    station: str
    temporary_password: SecretStr | None = None


def provision(db, emails):
    """Caller owns commit. No SMTP, tokens, method enrollment or existing-user updates."""
    ensure_local()
    db.execute(text('SELECT pg_advisory_xact_lock(78432019)'))
    station = select_station(db)
    if set(emails) - {spec[0] for spec in SPECS}: raise ValueError('Unknown demo account requested.')
    results = []
    for spec in SPECS:
        username, role_code, rank, service_number = spec
        existing = existing_user(db, spec, station)
        if existing:
            if username in emails and valid_email(emails[username]) != existing.email:
                raise ValueError(f'{username} already has a different email; it will not be changed.')
            results.append(AccountResult(username, role_code, station.name))
            continue
        if username not in emails: raise ValueError(f'An unused email address is required for {username}.')
        email = valid_email(emails[username])
        if db.scalar(select(User.id).where(func.lower(User.email) == email)):
            raise ValueError(f'The email supplied for {username} is already registered. Use a different inbox; existing users will not be modified.')
        role = db.scalar(select(Role).where(Role.code == role_code))
        if role is None: raise ValueError(f'Required existing role {role_code} is missing; run the existing migrations.')
        temporary = password()
        user = User(username=username, email=email, password_hash=hash_password(temporary.get_secret_value()),
            is_active=True, is_verified=False, mfa_enabled=False)
        db.add(user); db.flush()
        db.add(Officer(user_id=user.id, station_id=station.id, service_number=service_number, rank=rank, is_active=True))
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.add(AuditLog(actor_type='SYSTEM', action=ACTION, entity_type='user', entity_id=user.id, station_id=station.id))
        db.flush()
        results.append(AccountResult(username, role_code, station.name, temporary))
    return results


def main():
    parser = argparse.ArgumentParser(description='Create three local demo officers; passwords are displayed once in an interactive terminal.')
    parser.add_argument('--check', action='store_true', help='Read-only account/station/email availability check; no passwords or accounts created.')
    args = parser.parse_args()
    try:
        ensure_local()
        if not args.check and not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise ValueError('Run this command directly in your local interactive terminal, without redirection or logging. Use --check for a read-only check.')
        emails = {}
        with SessionLocal() as db:
            station = select_station(db)
            pending = []
            for spec in SPECS:
                existing = existing_user(db, spec, station)
                print(f'{spec[0]} | {spec[1]} | {station.name} | {"exists; unchanged" if existing else "not created"}')
                if not existing: pending.append(spec)
            taken = bool(db.scalar(select(User.id).where(func.lower(User.email) == DEFAULT_EMAIL)))
            if pending and taken:
                print('The requested default email is already registered. It cannot be reused or converted; an unused replacement is required.')
            if args.check: return
            db.rollback()  # Do not hold station locks while waiting for terminal input.
            print('No email will be sent during setup. Sign-in will require the emailed code. Disable terminal transcripts before continuing.')
            for spec in pending:
                if spec == SPECS[0] and not taken:
                    emails[spec[0]] = DEFAULT_EMAIL
                    print(f'{spec[0]} will use the requested default email.')
                    continue
                while True:
                    try:
                        email = valid_email(input(f'Unused email for {spec[0]}: '))
                        if email in emails.values(): raise ValueError('Each account requires a different email address.')
                        occupied = db.scalar(select(User.id).where(func.lower(User.email) == email))
                        db.rollback()
                        if occupied: raise ValueError('This email is already registered. Enter another inbox.')
                        emails[spec[0]] = email
                        break
                    except ValueError as error: print(str(error))
            with db.begin(): results = provision(db, emails)
        # Commit succeeded before any password is shown. Never print from services,
        # exception handling or --check, and never persist a plaintext credential.
        for result in results:
            print(f'{result.username} | {result.role} | {result.station}')
            if result.temporary_password:
                print('Temporary password (shown once): ' + result.temporary_password.get_secret_value())
            else: print('Existing account unchanged; use its original password.')
    except (ValueError, EOFError, KeyboardInterrupt) as error:
        raise SystemExit(str(error) or 'Setup cancelled; no accounts created.') from None
    except SQLAlchemyError:
        raise SystemExit('Demo account creation failed and was rolled back. Check the local database and existing account conflicts.') from None


if __name__ == '__main__': main()
