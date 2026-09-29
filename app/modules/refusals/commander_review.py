"""Commander oversight of all refused complaints, including legacy refusals."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.authentication.dependencies import require_permission
from app.modules.authentication.security import utcnow
from app.modules.audit.models import AuditLog
from app.modules.complaints.models import Complaint
from app.modules.dockets.service import DocketService
from app.modules.refusals.models import ComplaintDecision, RefusalEscalation, RefusalReason
from app.modules.refusals.schemas import RefusalEscalationOut

router = APIRouter(tags=['Refusals'])


class ReviewAction(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    decision_id: uuid.UUID
    action: Literal['ACKNOWLEDGE', 'ESCALATE']
    reason: str | None = Field(default=None, max_length=20000)

    @model_validator(mode='after')
    def require_escalation_reason(self):
        if self.action == 'ESCALATE' and not self.reason:
            raise ValueError('A reason is required to escalate to NCC')
        return self


def scope(db, user_id, complaint_id, *, write=False):
    officer = DocketService(db)._active_officer_with_role(user_id, 'STATION_COMMANDER')
    query = select(Complaint).where(Complaint.id == complaint_id, Complaint.station_id == officer.station_id)
    if write:
        query = query.with_for_update().execution_options(populate_existing=True)
    complaint = db.scalar(query)
    if complaint is None:
        raise HTTPException(404, 'Complaint not found')
    decision = db.scalar(select(ComplaintDecision).where(ComplaintDecision.complaint_id == complaint.id)
                         .order_by(ComplaintDecision.decision_sequence.desc()).limit(1))
    if not decision or decision.decision != 'REFUSED' or complaint.status not in {'REFUSED', 'ESCALATED'}:
        raise HTTPException(409, 'This complaint has no current refusal to review')
    return complaint, decision


def snapshot(db, complaint, decision):
    reason = db.get(RefusalReason, decision.refusal_reason_id)
    records = {row.target: RefusalEscalationOut.model_validate(row) for row in db.scalars(
        select(RefusalEscalation).where(RefusalEscalation.complaint_decision_id == decision.id))}
    return {'decision_id': decision.id, 'refusal_reason': reason.name, 'officer_notes': decision.officer_notes,
            'complaint_status': complaint.status, 'commander_review': records.get('STATION_COMMANDER'),
            'ncc_escalation': records.get('NCC')}


@router.get('/complaints/{complaint_id}/refusal-review')
def read_review(complaint_id: uuid.UUID, db: Session = Depends(get_db),
                user=Depends(require_permission('refusal.escalation.view'))):
    complaint, decision = scope(db, user.id, complaint_id)
    result = snapshot(db, complaint, decision)
    db.add(AuditLog(actor_type='USER', actor_user_id=user.id, station_id=complaint.station_id,
                    action='refusal.review.view', entity_type='complaint_decision', entity_id=decision.id))
    db.commit()
    return result


@router.post('/complaints/{complaint_id}/refusal-review')
def act_on_review(complaint_id: uuid.UUID, data: ReviewAction, db: Session = Depends(get_db),
                  user=Depends(require_permission('refusal.escalation.view'))):
    try:
        complaint, decision = scope(db, user.id, complaint_id, write=True)
        if decision.id != data.decision_id:
            raise HTTPException(409, 'The refusal decision changed. Refresh before reviewing it.')
        target = 'NCC' if data.action == 'ESCALATE' else 'STATION_COMMANDER'
        row = db.scalar(select(RefusalEscalation).where(
            RefusalEscalation.complaint_decision_id == decision.id, RefusalEscalation.target == target)
            .with_for_update().execution_options(populate_existing=True))
        changed = False
        if row is None:
            row = RefusalEscalation(complaint_id=complaint.id, complaint_decision_id=decision.id,
                                    target=target, status='OPEN')
            db.add(row)
            db.flush()
            changed = True
        if data.action == 'ACKNOWLEDGE':
            if row.status == 'RESOLVED':
                raise HTTPException(409, 'This refusal review is already resolved')
            if row.status == 'OPEN':
                row.status = 'ACKNOWLEDGED'
                row.acknowledged_by_user_id = user.id
                row.acknowledged_at = utcnow()
                changed = True
        elif changed:
            complaint.status = 'ESCALATED'
        if changed:
            db.add(AuditLog(actor_type='USER', actor_user_id=user.id, station_id=complaint.station_id,
                action='refusal.escalation.acknowledge' if data.action == 'ACKNOWLEDGE' else 'refusal.escalation.forward',
                entity_type='refusal_escalation', entity_id=row.id,
                event_metadata={'decision_id': str(decision.id), 'reason': data.reason} if data.action == 'ESCALATE' else None))
        db.flush()
        result = snapshot(db, complaint, decision)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
