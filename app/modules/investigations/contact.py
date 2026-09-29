"""Assigned-investigator contact reads and templated station invitations."""
import uuid
from datetime import timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict
from typing import Literal
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.modules.authentication.dependencies import require_permission
from app.modules.authentication.security import utcnow
from app.modules.complainants.models import Complainant
from app.modules.complaints.models import Complaint
from app.modules.communications.models import CaseFeedback, Notification
from app.modules.communications.email_models import CaseEmailIntent
from app.modules.communications.case_email import enqueue, lock
from app.modules.communications.notifications import notify_complainant
from app.modules.investigations.service import InvestigationService
from app.modules.stations.models import Station

router = APIRouter(tags=['Investigator contact'])


@router.get('/investigations/dockets/{docket_id}/complainant')
def contact(docket_id: uuid.UUID, db: Session = Depends(get_db), user=Depends(require_permission('docket.view_assigned'))):
    svc = InvestigationService(db)
    officer, docket = svc.scope(user.id, docket_id)
    complaint = db.get(Complaint, docket.complaint_id)
    person = db.get(Complainant, complaint.complainant_id)
    result = {key: getattr(person, key) for key in ('first_name', 'last_name', 'phone_number', 'email',
        'address_line_1', 'address_line_2', 'city', 'province', 'postal_code')}
    svc.audit(user.id, officer.station_id, 'complainant.contact.view', 'docket', docket.id)
    db.commit()
    return result


class Invitation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: uuid.UUID
    purpose: Literal['INTERVIEW', 'MEETING']
    starts_at: AwareDatetime


@router.post('/investigations/dockets/{docket_id}/invitations', status_code=201)
def invite(docket_id: uuid.UUID, data: Invitation, db: Session = Depends(get_db),
           user=Depends(require_permission('feedback.provide'))):
    svc = InvestigationService(db)
    officer, docket = svc.scope(user.id, docket_id, writable=True)
    complaint = db.get(Complaint, docket.complaint_id)
    station = db.get(Station, complaint.station_id)
    if data.starts_at <= utcnow():
        raise HTTPException(422, 'Choose a future appointment time')
    when = data.starts_at.astimezone(timezone(timedelta(hours=2))).strftime('%d %B %Y at %H:%M SAST')
    address = ', '.join(filter(None, [station.address_line_1, station.address_line_2, station.city, station.province]))
    subject = 'Station ' + data.purpose.lower() + ' invitation'
    message = f'Please attend an {data.purpose.lower()} at {station.name} on {when}.' if data.purpose == 'INTERVIEW' else f'Please attend a meeting at {station.name} on {when}.'
    if address:
        message += ' Station address: ' + address + '.'
    message += ' If you cannot attend, contact the station to arrange another time.'
    lock(db, 'invitation:' + str(data.request_id))
    existing = db.get(CaseFeedback, data.request_id)
    if existing and (existing.docket_id != docket.id or existing.provided_by_officer_id != officer.id
                     or existing.subject != subject or existing.message != message):
        raise HTTPException(409, 'This request identifier has already been used. Refresh before creating another invitation.')
    if not existing:
        db.add(CaseFeedback(id=data.request_id, docket_id=docket.id, complainant_id=complaint.complainant_id,
            provided_by_officer_id=officer.id, feedback_type='REQUEST_FOR_INFORMATION',
            subject=subject, message=message, is_official=True, published_at=utcnow()))
        db.flush()
        svc.audit(user.id, officer.station_id, 'case.invitation.recorded', 'case_feedback', data.request_id)
        notify_complainant(db, complaint, 'meeting.requested', docket_id=docket.id, actor_user_id=user.id)
    notification_id = enqueue(db, complaint, docket.id, 'meeting.requested', data.request_id)
    db.flush()
    notification = db.get(Notification, notification_id)
    intent = db.get(CaseEmailIntent, notification_id)
    result = {'feedback_id': data.request_id, 'notification_id': notification_id,
              'delivery_status': notification.status, 'delivery_reason': intent.reason}
    db.commit()
    return result
