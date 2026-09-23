import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.access.models import User
from app.modules.authentication.dependencies import require_permission
from app.modules.dockets.schemas import DocketOut
from app.modules.investigations.schemas import AssignmentRequest, AssignmentOut, ReasonRequest, NoteRequest, NoteOut, StatusRequest
from app.modules.investigations.service import InvestigationService

router = APIRouter(tags=['Investigations'])


def service(db: Session = Depends(get_db)):
    return InvestigationService(db)


@router.post('/dockets/{docket_id}/assignments', response_model=AssignmentOut, status_code=201)
def assign(docket_id: uuid.UUID, data: AssignmentRequest,
           user: User = Depends(require_permission('docket.assign')), svc=Depends(service)):
    return svc.assign(user.id, docket_id, data)


@router.post('/dockets/{docket_id}/assignments/end', response_model=AssignmentOut)
def unassign(docket_id: uuid.UUID, data: ReasonRequest,
             user: User = Depends(require_permission('docket.assign')), svc=Depends(service)):
    return svc.unassign(user.id, docket_id, data)


@router.get('/dockets/{docket_id}/assignments', response_model=list[AssignmentOut])
def assignments(docket_id: uuid.UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                user: User = Depends(require_permission('docket.assign')), svc=Depends(service)):
    return svc.assignments(user.id, docket_id, limit, offset)


@router.get('/investigations/dockets', response_model=list[DocketOut])
def mine(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
         user: User = Depends(require_permission('docket.view_assigned')), svc=Depends(service)):
    return svc.mine(user.id, limit, offset)


@router.get('/investigations/dockets/{docket_id}', response_model=DocketOut)
def docket(docket_id: uuid.UUID, user: User = Depends(require_permission('docket.view_assigned')), svc=Depends(service)):
    return svc.get(user.id, docket_id)


@router.post('/dockets/{docket_id}/notes', response_model=NoteOut, status_code=201)
def add_note(docket_id: uuid.UUID, data: NoteRequest,
             user: User = Depends(require_permission('case.add_note')), svc=Depends(service)):
    return svc.add_note(user.id, docket_id, data)


@router.get('/dockets/{docket_id}/notes', response_model=list[NoteOut])
def notes(docket_id: uuid.UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
          user: User = Depends(require_permission('docket.view_assigned')), svc=Depends(service)):
    return svc.notes(user.id, docket_id, limit, offset)


@router.post('/dockets/{docket_id}/status', response_model=DocketOut)
def status(docket_id: uuid.UUID, data: StatusRequest,
           user: User = Depends(require_permission('case.update_status')), svc=Depends(service)):
    return svc.update_status(user.id, docket_id, data)
