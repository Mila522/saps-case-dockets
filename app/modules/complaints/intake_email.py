"""Complainant consent to link new walk-in intake to a verified portal account."""
import hmac
import secrets
import uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.modules.complaints.intake_email_models import IntakeConsent
from app.db.session import get_db
from app.modules.access.models import User
from app.modules.authentication import mail
from app.modules.authentication.dependencies import require_permission
from app.modules.authentication.security import utcnow
from app.modules.audit.models import AuditLog
from app.modules.complainants.models import Complainant
from app.modules.complaints.service import StationComplaintService
from app.modules.communications.case_email import digest, lock, verified_email




router = APIRouter(tags=['In-station account consent'])


class EmailRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    email: EmailStr


@router.post('/complaints/in-station/email-consent', status_code=202)
def request_consent(data: EmailRequest, db: Session = Depends(get_db),
                    user=Depends(require_permission('complaint.register'))):
    officer = StationComplaintService(db)._officer(user.id, {'CHARGE_OFFICER'})
    address = str(data.email).casefold()
    email_hash = digest('intake-email:' + address)
    lock(db, 'intake-email:' + str(user.id))
    lock(db, 'intake-recipient:' + email_hash)
    now = utcnow()
    recent = IntakeConsent.created_at > now - timedelta(hours=1)
    rows = list(db.scalars(select(IntakeConsent).where(recent,
        (IntakeConsent.officer_user_id == user.id) | (IntakeConsent.email_digest == email_hash))))
    email_rows = [row for row in rows if row.email_digest == email_hash]
    if (sum(row.officer_user_id == user.id for row in rows) >= 20 or len(email_rows) >= 5 or
            any(row.created_at > now-timedelta(seconds=60) for row in email_rows)):
        raise HTTPException(429, 'Please wait before requesting another consent code.', headers={'Retry-After':'60'})
    person = db.scalar(select(Complainant).join(User, User.id == Complainant.user_id)
        .where(func.lower(User.email) == address, func.lower(Complainant.email) == address))
    destination = verified_email(db, person)
    identity, code = uuid.uuid4(), f'{secrets.randbelow(1_000_000):06d}'
    for previous in email_rows:
        if previous.used_at is None:
            previous.used_at = now
    row = IntakeConsent(id=identity, officer_user_id=user.id, station_id=officer.station_id,
        complainant_id=person.id if destination else None, email_digest=email_hash,
        code_digest=digest(f'intake-code:{identity}:{code}'), created_at=now,
        expires_at=now+timedelta(minutes=5), delivery_state='UNAVAILABLE')
    db.add(row)
    db.flush()
    if destination:
        state, _ = mail.send_case_message(destination, 'Confirm your in-station complaint account link',
            f'Your consent code is {code}. It expires in five minutes. Give it only to the charge officer '
            'recording your complaint if you agree to link that complaint to your portal account and save the contact details you supplied. '
            'This is not a sign-in code. Ignore it if you did not request an in-station complaint.', identity)
        row.delivery_state = state
    db.add(AuditLog(actor_type='USER', actor_user_id=user.id, action='complaint.intake_consent.requested',
        entity_type='intake_email_consent', entity_id=identity, station_id=officer.station_id))
    db.commit()
    return {'challenge_id': identity, 'message':'If this email has a verified complainant account, a consent code was submitted. Otherwise ask the complainant to register and verify at /portal/ first.', 'retry_after':60}


def consume(db, officer, data):
    row = db.scalar(select(IntakeConsent).where(IntakeConsent.id == data.email_consent_id).with_for_update())
    person = db.get(Complainant, row.complainant_id) if row and row.complainant_id else None
    email = verified_email(db, person)
    if row and row.officer_user_id == officer.user_id and row.station_id == officer.station_id:
        row.attempts += 1
    valid = (row and email and row.officer_user_id == officer.user_id and row.station_id == officer.station_id
        and row.delivery_state == 'ACCEPTED' and row.used_at is None and row.expires_at > utcnow()
        and row.attempts <= 5 and data.complainant.email and email == str(data.complainant.email).casefold()
        and hmac.compare_digest(row.email_digest, digest('intake-email:' + email))
        and hmac.compare_digest(row.code_digest, digest(f'intake-code:{row.id}:{data.email_consent_code}')))
    if not valid:
        db.commit()  # Preserve the guess limit; no intake records have been written yet.
        raise HTTPException(400, 'Consent code is invalid, expired or unavailable. Request a new code.')
    row.used_at = utcnow()
    for key in ('phone_number', 'address_line_1', 'address_line_2', 'city', 'province', 'postal_code'):
        value = getattr(data.complainant, key)
        if value is not None:
            setattr(person, key, value)
    db.add(AuditLog(actor_type='USER', actor_user_id=officer.user_id, action='complaint.intake_consent.used',
        entity_type='intake_email_consent', entity_id=row.id, station_id=officer.station_id))
    return person
