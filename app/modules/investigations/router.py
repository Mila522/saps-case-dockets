import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.access.models import User
from app.modules.authentication.dependencies import require_permission
from app.modules.dockets.schemas import DocketOut
from app.modules.investigations.schemas import AssignmentRequest, AssignmentOut, ReasonRequest, NoteRequest, NoteOut, StatusRequest
from app.modules.investigations.service import InvestigationService
from app.modules.investigations.schemas import InvestigatorDocketOut, StatusHistoryOut

from app.modules.investigations.schemas import ERROR_RESPONSES

router = APIRouter(responses=ERROR_RESPONSES, tags=['Investigations'])


def service(db: Session = Depends(get_db)):
    return InvestigationService(db)


@router.post('/dockets/{docket_id}/assignments', response_model=AssignmentOut, status_code=201,
             summary='Assign or reassign an investigator',
             description='Station commanders only. Close the previous assignment atomically; the first assignment activates an approved docket.')
def assign(docket_id: uuid.UUID, data: AssignmentRequest,
           user: User = Depends(require_permission('docket.assign')), svc=Depends(service)):
    return svc.assign(user.id, docket_id, data)


@router.post('/dockets/{docket_id}/assignments/end', response_model=AssignmentOut,
             summary='End the active assignment',
             description='Station commanders only. A reason is required; the former investigator loses access immediately.')
def unassign(docket_id: uuid.UUID, data: ReasonRequest,
             user: User = Depends(require_permission('docket.assign')), svc=Depends(service)):
    return svc.unassign(user.id, docket_id, data)


@router.get('/dockets/{docket_id}/assignments', response_model=list[AssignmentOut],
             summary='Read assignment history',
             description='Station commanders may read only their station dockets. Paginated and audited.')
def assignments(docket_id: uuid.UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                user: User = Depends(require_permission('docket.assign')), svc=Depends(service)):
    return svc.assignments(user.id, docket_id, limit, offset)


@router.get('/investigations/dockets', response_model=list[InvestigatorDocketOut],
             summary='List actively assigned dockets',
             description='Active investigating officers see only their current station assignments. No implicit national access.')
def mine(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
         user: User = Depends(require_permission('docket.view_assigned')), svc=Depends(service)):
    return svc.mine(user.id, limit, offset)


@router.get('/investigations/dockets/{docket_id}', response_model=InvestigatorDocketOut,
             summary='Read an assigned docket',
             description='Checks current assignment and station on every request. Sensitive read is audited.')
def docket(docket_id: uuid.UUID, user: User = Depends(require_permission('docket.view_assigned')), svc=Depends(service)):
    return svc.get(user.id, docket_id)


@router.get('/investigations/dockets/{docket_id}/status-history', response_model=list[StatusHistoryOut],
             summary='Read assigned docket status history',
             description='Paginated immutable history, restricted to the active assigned investigator and audited.')
def status_history(docket_id: uuid.UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                   user: User = Depends(require_permission('docket.view_assigned')), svc=Depends(service)):
    return svc.status_history(user.id, docket_id, limit, offset)


@router.post('/dockets/{docket_id}/notes', response_model=NoteOut, status_code=201,
             summary='Append an investigation note',
             description='Active assigned investigators only. Notes cannot be edited or deleted; append a CORRECTION note instead.')
def add_note(docket_id: uuid.UUID, data: NoteRequest,
             user: User = Depends(require_permission('case.add_note')), svc=Depends(service)):
    return svc.add_note(user.id, docket_id, data)


@router.get('/dockets/{docket_id}/notes', response_model=list[NoteOut],
             summary='Read investigation notes',
             description='Active assigned investigators only, including sensitive notes. Complainants cannot access this route.')
def notes(docket_id: uuid.UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
          user: User = Depends(require_permission('docket.view_assigned')), svc=Depends(service)):
    return svc.notes(user.id, docket_id, limit, offset)


@router.post('/dockets/{docket_id}/status', response_model=DocketOut,
             summary='Change investigation status',
             description='Supply expected_status from the current docket. Stale state returns 409. ACTIVE and ON_HOLD may transition to each other or CLOSED. reason becomes closure_reason on closure; case.close is also required. No D notifications are sent.')
def status(docket_id: uuid.UUID, data: StatusRequest,
           user: User = Depends(require_permission('case.update_status')), svc=Depends(service)):
    return svc.update_status(user.id, docket_id, data)
