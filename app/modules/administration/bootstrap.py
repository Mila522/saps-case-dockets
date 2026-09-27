"""Explicit environment-based first administrator setup. Never runs at startup."""
import os
from sqlalchemy import select, func, text
from app.db import models  # noqa: F401
from app.db.session import SessionLocal
from app.modules.access.models import User, Role, UserRole
from app.modules.audit.models import AuditLog
from app.modules.authentication.security import hash_password
from .schemas import Credentials


def provision(db, data):
    db.execute(text('SELECT pg_advisory_xact_lock(71493025)'))
    role=db.scalar(select(Role).where(Role.code=='SYSTEM_ADMINISTRATOR'))
    if role is None: raise ValueError('Run migrations before administrator setup.')
    existing=db.scalar(select(User).where(func.lower(User.username)==data.username))
    if existing:
        roles=set(db.scalars(select(Role.code).join(UserRole).where(UserRole.user_id==existing.id)))
        marker=db.scalar(select(AuditLog.id).where(AuditLog.entity_id==existing.id,AuditLog.action=='admin.bootstrap'))
        if existing.email==str(data.email) and roles=={'SYSTEM_ADMINISTRATOR'} and marker:
            return False
        raise ValueError('Account already exists; no account was changed.')
    if db.scalar(select(UserRole.user_id).where(UserRole.role_id==role.id).limit(1)):
        raise ValueError('An administrator already exists; this command only provisions the first administrator.')
    if db.scalar(select(User.id).where(func.lower(User.email)==str(data.email))):
        raise ValueError('Email already belongs to an account; no account was changed.')
    user=User(username=data.username,email=str(data.email),password_hash=hash_password(data.password.get_secret_value()),
              is_active=True,is_verified=False,mfa_enabled=False)
    db.add(user);db.flush()
    db.add(UserRole(user_id=user.id,role_id=role.id))
    db.add(AuditLog(actor_type='SYSTEM',action='admin.bootstrap',entity_type='user',entity_id=user.id))
    db.flush()
    return True


def main():
    try:
        data=Credentials(username=os.environ.get('SAPS_ADMIN_USERNAME',''),email=os.environ.get('SAPS_ADMIN_EMAIL',''),
                         password=os.environ.get('SAPS_ADMIN_PASSWORD',''))
    except Exception:
        raise SystemExit('Set valid SAPS_ADMIN_USERNAME, SAPS_ADMIN_EMAIL and a strong SAPS_ADMIN_PASSWORD. No account created.') from None
    try:
        with SessionLocal() as db:
            created=provision(db,data);db.commit()
        print('Administrator created; sign in and verify your email.' if created else 'Administrator already provisioned; unchanged.')
    except ValueError as error:
        raise SystemExit(str(error)) from None
    except Exception:
        raise SystemExit('Administrator setup failed. Check database availability and migrations; no credentials printed.') from None


if __name__=='__main__': main()
