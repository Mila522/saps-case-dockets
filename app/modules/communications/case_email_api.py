"""Authenticated, ownership/station-scoped case email delivery status only."""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.modules.authentication.dependencies import require_permission
from app.modules.complainants.models import Complainant
from app.modules.complaints.models import Complaint
from app.modules.complaints.service import StationComplaintService
from app.modules.communications.models import Notification
from app.modules.communications.email_models import CaseEmailIntent
from app.modules.communications.case_email import verified_email
from app.modules.audit.models import AuditLog

router = APIRouter(prefix='/case-email', tags=['Case email delivery status'])


def delivery_status(db, person, user, complaint_id=None):
    query = select(Notification, CaseEmailIntent).join(CaseEmailIntent).where(
        Notification.channel == 'EMAIL', Notification.recipient_complainant_id == person.id)
    if complaint_id:
        query = query.where(Notification.complaint_id == complaint_id)
    rows = db.execute(query.order_by(Notification.created_at.desc(), Notification.id).limit(20)).all()
    result = {'email_verified': bool(verified_email(db, person)), 'deliveries': [
        {'id': row.id, 'event_type': row.event_type, 'status': row.status, 'reason': intent.reason}
        for row, intent in rows]}
    db.add(AuditLog(actor_type='USER', actor_user_id=user.id, action='case_email.status.read',
        entity_type='complainant', entity_id=person.id))
    db.commit()
    return result


@router.get('/status')
def own_status(db: Session = Depends(get_db), user=Depends(require_permission('case.track_own'))):
    person = db.scalar(select(Complainant).where(Complainant.user_id == user.id))
    if not person:
        raise HTTPException(404, 'Complainant profile not found')
    return delivery_status(db, person, user)


@router.get('/complaints/{complaint_id}/status')
def station_status(complaint_id: uuid.UUID, db: Session = Depends(get_db), user=Depends(require_permission('complaint.view_station'))):
    officer = StationComplaintService(db)._officer(user.id, {'CHARGE_OFFICER', 'STATION_COMMANDER'})
    complaint = db.get(Complaint, complaint_id)
    if not complaint or complaint.station_id != officer.station_id:
        raise HTTPException(404, 'Complaint not found')
    return delivery_status(db, db.get(Complainant, complaint.complainant_id), user, complaint.id)
