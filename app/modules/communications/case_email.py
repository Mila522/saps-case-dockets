"""Transactional case-email outbox. Run the worker separately after commits."""
import argparse
import hashlib
import hmac
import uuid
import time
from urllib.parse import urlsplit
from sqlalchemy import func, select, text
from app.core.config import settings
from app.db import models  # noqa: F401
from app.db.session import SessionLocal
from app.modules.access.models import User
from app.modules.authentication import mail
from app.modules.authentication.models import UserEmailAuth
from app.modules.authentication.security import utcnow, encrypt_totp_secret, decrypt_totp_secret
from app.modules.audit.models import AuditLog
from app.modules.complainants.models import Complainant
from app.modules.communications.models import Notification, NotificationAttempt, CaseFeedback
from app.modules.communications.email_models import CaseEmailIntent
from app.modules.dockets.models import Docket
from app.modules.stations.models import Station


def digest(value):
    return hmac.new(settings.jwt_secret_key.get_secret_value().encode(),
        ('case-email-v1:' + value).encode(), hashlib.sha256).hexdigest()


def lock(db, key):
    number = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big', signed=True)
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': number})


def verified_email(db, person):
    if person is None or person.user_id is None or not person.email:
        return None
    user = db.get(User, person.user_id)
    proof = db.get(UserEmailAuth, person.user_id)
    if not user or not user.is_active or not proof or not user.email:
        return None
    address = user.email.strip().casefold()
    return address if address == proof.verified_email.strip().casefold() == person.email.strip().casefold() else None


def tracking_link(complaint_id=None):
    try:
        value = settings.case_portal_url
        url = urlsplit(value)
        local = settings.environment != 'production' and url.hostname in {'localhost', '127.0.0.1', '::1'}
        if (url.scheme != 'https' and not (local and url.scheme == 'http')) or not url.hostname:
            return None
        if url.username or url.password or url.query or url.fragment or url.path != '/portal/':
            return None
        return value + (f'#complaint={uuid.UUID(str(complaint_id))}' if complaint_id else '')
    except ValueError:
        return None


def enqueue(db, complaint, docket_id, event, source_id):
    """Caller owns the transaction. No SMTP, no recipient input, no internal text."""
    key = f'{event}:{source_id}'
    lock(db, 'case-email:' + key)
    existing = db.scalar(select(CaseEmailIntent.notification_id).where(CaseEmailIntent.event_key == key))
    if existing:
        return existing
    docket = db.get(Docket, docket_id) if docket_id else None
    reference = complaint.reference_number
    if event == 'complaint.registered':
        station = db.get(Station, complaint.station_id)
        body = f'Complaint {reference} was submitted to {station.name}. Status: SUBMITTED.'
    elif event == 'docket.created':
        body = f'A docket was created for complaint {reference}. Station Commander approval is pending.'
    elif event == 'docket.approved':
        body = f'Complaint {reference} was approved by the Station Commander. CAS number: {docket.cas_number}.'
    elif event == 'docket.assigned':
        # No approved public investigator display-name field exists.
        body = f'An investigator has been assigned to complaint {reference}. CAS number: {docket.cas_number}.'
    elif event == 'feedback.published':
        body = f'An official update is available for complaint {reference}.'
    elif event == 'meeting.requested':
        invitation = db.get(CaseFeedback, source_id)
        body = f'Complaint {reference}: {invitation.message}'
    elif event in {'case.active', 'case.on_hold', 'case.closed'}:
        body = f'The case status for complaint {reference} changed to {event.split(".")[1].upper()}.'
    elif event == 'complaint.refused':
        # Controlled reasons include non-compliant refusals. Do not endorse them
        # or copy officer notes into mail; report the public outcome accurately.
        outcome = 'A refusal was recorded and escalated for review' if complaint.status == 'ESCALATED' else 'A refusal was recorded'
        body = f'{outcome} for complaint {reference}.'
    else:
        raise ValueError('Unsupported case email event')
    if docket:
        if docket.cas_number not in body:
            body += f' CAS number: {docket.cas_number}.'
        body += f' Case status: {docket.status}.'
    link = tracking_link(complaint.id)
    body += '\n\nSign in to view your case securely: ' + (link or '[tracking link not configured]')
    destination = verified_email(db, db.get(Complainant, complaint.complainant_id))
    reason = 'EMAIL_NOT_VERIFIED' if not destination else 'TRACKING_URL_MISSING' if not link else 'QUEUED'
    row = Notification(id=uuid.uuid4(), recipient_complainant_id=complaint.complainant_id,
        complaint_id=complaint.id, docket_id=docket_id, channel='EMAIL', event_type=event,
        subject='SAPS complaint update', message=body,
        status='PENDING' if reason == 'QUEUED' else 'FAILED',
        failed_at=None if reason == 'QUEUED' else utcnow(),
        destination_encrypted=encrypt_totp_secret(destination) if destination else None)
    db.add(row)
    db.flush()
    db.add(CaseEmailIntent(notification_id=row.id, event_key=key,
        email_digest=digest('email:' + destination) if destination else None, reason=reason))
    db.add(AuditLog(actor_type='SYSTEM', action='case_email.queued', entity_type='notification',
        entity_id=row.id, station_id=complaint.station_id, event_metadata={'reason': reason}))
    return row.id


def process_one(db, *, notification_ids=None):
    query = select(Notification).join(CaseEmailIntent).where(Notification.channel == 'EMAIL',
        Notification.status == 'PENDING')
    if notification_ids is not None:
        query = query.where(Notification.id.in_(notification_ids))
    row = db.scalar(query.order_by(Notification.created_at, Notification.id)
        .with_for_update(skip_locked=True, of=Notification).limit(1))
    if row is None:
        db.rollback()
        return False
    intent = db.get(CaseEmailIntent, row.id)
    destination = verified_email(db, db.get(Complainant, row.recipient_complainant_id))
    reason = None
    if not destination:
        reason = 'EMAIL_NOT_VERIFIED'
    elif digest('email:' + destination) != intent.email_digest:
        reason = 'EMAIL_CHANGED'
    elif not tracking_link() or '[tracking link not configured]' in row.message:
        reason = 'TRACKING_URL_MISSING'
    if reason:
        row.status, row.failed_at, intent.reason = 'FAILED', utcnow(), reason
        db.add(AuditLog(actor_type='SYSTEM', action='case_email.blocked', entity_type='notification',
            entity_id=row.id, event_metadata={'reason': reason}))
        db.commit()
        return True
    destination = decrypt_totp_secret(row.destination_encrypted)
    # Upgrade only unsent legacy footers; old public tracking routes are removed.
    body = row.message.split('\n\nView updates securely:')[0].split('\n\nSign in to view your case securely:')[0]
    body += '\n\nSign in to view your case securely: ' + tracking_link(row.complaint_id)
    row.message = body
    identity, subject = row.id, row.subject
    number = (db.scalar(select(func.max(NotificationAttempt.attempt_number)).where(
        NotificationAttempt.notification_id == identity)) or 0) + 1
    row.status, intent.reason = 'PROCESSING', 'SEND_OUTCOME_UNKNOWN'
    db.add(AuditLog(actor_type='SYSTEM', action='case_email.send_started', entity_type='notification', entity_id=identity))
    # Claim before network I/O. Crash/unknown outcomes stay PROCESSING and cannot
    # be resent. Attempts are append-only: insert only the final known result.
    db.commit()
    state, reason = mail.send_case_message(destination, subject, body, identity)
    row = db.scalar(select(Notification).where(Notification.id == identity).with_for_update())
    intent = db.get(CaseEmailIntent, identity)
    intent.reason = reason
    if state == 'ACCEPTED':
        row.status, row.sent_at = 'SENT', utcnow()
    elif state in {'REJECTED', 'BLOCKED'}:
        row.status, row.failed_at = 'FAILED', utcnow()
    db.add(NotificationAttempt(notification_id=identity, attempt_number=number, provider='SMTP',
        provider_message_id=f'<case-{identity}@case-docket.invalid>',
        outcome='SENT' if state == 'ACCEPTED' else 'TIMEOUT' if state == 'UNKNOWN' else 'REJECTED',
        error_code=None if state == 'ACCEPTED' else reason))
    db.add(AuditLog(actor_type='SYSTEM', action='case_email.send_result', entity_type='notification',
        entity_id=identity, event_metadata={'state': state, 'reason': reason}))
    db.commit()
    return True


def retry(db, identity):
    row = db.scalar(select(Notification).where(Notification.id == identity).with_for_update())
    intent = db.get(CaseEmailIntent, identity)
    if not row or row.channel != 'EMAIL' or row.status != 'FAILED' or not intent or intent.reason not in {
            'SMTP_NOT_CONFIGURED', 'SMTP_CONNECTION_FAILED', 'SMTP_REJECTED',
            'SMTP_AUTHENTICATION_FAILED', 'SMTP_NETWORK_BLOCKED'}:
        raise ValueError('Only definitely unsent SMTP failures can be retried; uncertain, sent and address-change cases cannot.')
    row.status, row.failed_at, intent.reason = 'PENDING', None, 'QUEUED'
    db.add(AuditLog(actor_type='SYSTEM', action='case_email.retry', entity_type='notification', entity_id=identity))
    db.commit()


def main():
    parser = argparse.ArgumentParser(description='Send committed case emails. No live mail is sent by case API actions.')
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--retry', type=uuid.UUID)
    parser.add_argument('--watch', action='store_true', help='Keep processing committed emails every five seconds.')
    args = parser.parse_args()
    with SessionLocal() as db:
        if args.retry:
            retry(db, args.retry)
        count = 0
        while True:
            for _ in range(min(max(args.limit, 1), 1000)):
                if not process_one(db):
                    break
                count += 1
            if not args.watch:
                break
            time.sleep(5)
        print(f'Processed {count} email intents. SMTP acceptance does not confirm inbox delivery.')


if __name__ == '__main__':
    main()
