import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.access.models import User
from app.modules.authentication.dependencies import require_permission
from app.modules.refusals.schemas import (ComplaintDecisionOut, ComplaintDecisionRequest,
                                          EscalationResolutionRequest, RefusalEscalationOut,
                                          RefusalReasonOut)
from app.modules.refusals.service import ComplaintDecisionService, RefusalService

router = APIRouter(tags=['Refusals'])


def get_decision_service(db: Session = Depends(get_db)) -> ComplaintDecisionService:
    return ComplaintDecisionService(db)


@router.post('/complaints/{complaint_id}/decisions', response_model=ComplaintDecisionOut, status_code=201)
def decide_complaint(complaint_id: uuid.UUID, data: ComplaintDecisionRequest,
                     user: User = Depends(require_permission('complaint.decide')),
                     service: ComplaintDecisionService = Depends(get_decision_service)):
    return service.decide(user.id, complaint_id, data)


def get_refusal_service(db: Session = Depends(get_db)) -> RefusalService:
    return RefusalService(db)


@router.get('/refusal-reasons', response_model=list[RefusalReasonOut])
def list_refusal_reasons(
        user: User = Depends(require_permission('complaint.decide')),
        service: RefusalService = Depends(get_refusal_service)):
    return service.list_reasons()


@router.get('/refusal-escalations', response_model=list[RefusalEscalationOut])
def list_refusal_escalations(
        user: User = Depends(require_permission('refusal.escalation.view')),
        service: RefusalService = Depends(get_refusal_service)):
    return service.list_escalations(user.id)


@router.post('/refusal-escalations/{escalation_id}/acknowledge',
             response_model=RefusalEscalationOut)
def acknowledge_refusal_escalation(
        escalation_id: uuid.UUID,
        user: User = Depends(require_permission('refusal.escalation.view')),
        service: RefusalService = Depends(get_refusal_service)):
    return service.acknowledge(user.id, escalation_id)


@router.post('/refusal-escalations/{escalation_id}/resolve',
             response_model=RefusalEscalationOut)
def resolve_refusal_escalation(
        escalation_id: uuid.UUID, data: EscalationResolutionRequest,
        user: User = Depends(require_permission('refusal.escalation.view')),
        service: RefusalService = Depends(get_refusal_service)):
    return service.resolve(user.id, escalation_id, data)
