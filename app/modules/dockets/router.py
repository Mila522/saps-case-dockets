import uuid

from fastapi import APIRouter, Depends, Query
from typing import Literal
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.access.models import User
from app.modules.authentication.dependencies import require_permission
from app.modules.dockets.schemas import DocketApprovalOut, DocketApprovalRequest, DocketOut
from app.modules.dockets.service import DocketService

router = APIRouter(tags=['Dockets'])


def get_docket_service(db: Session = Depends(get_db)) -> DocketService:
    return DocketService(db)


@router.get('/dockets', response_model=list[DocketOut])
def list_station_dockets(
        status: Literal['PENDING_APPROVAL', 'APPROVED', 'ACTIVE', 'ON_HOLD', 'CLOSED', 'ARCHIVED'] | None = None,
        limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
        user: User = Depends(require_permission('docket.approve')),
        service: DocketService = Depends(get_docket_service)):
    return service.list_for_commander(user.id, status, limit=limit, offset=offset)


@router.post('/complaints/{complaint_id}/dockets', response_model=DocketOut, deprecated=True,
             summary='Ensure a docket exists for a previously accepted complaint',
             description='Compatibility endpoint: returns the existing docket with 200; allocates only for legacy accepted complaints without a docket. New acceptance creates a docket atomically.')
def open_docket(
        complaint_id: uuid.UUID,
        user: User = Depends(require_permission('complaint.decide')),
        service: DocketService = Depends(get_docket_service)):
    return service.open(user.id, complaint_id)


@router.get('/dockets/{docket_id}', response_model=DocketOut)
def get_docket(
        docket_id: uuid.UUID,
        user: User = Depends(require_permission('docket.approve')),
        service: DocketService = Depends(get_docket_service)):
    return service.get_for_commander(user.id, docket_id)


@router.post('/dockets/{docket_id}/approvals', response_model=DocketApprovalOut, status_code=201)
def approve_docket(
        docket_id: uuid.UUID, data: DocketApprovalRequest,
        user: User = Depends(require_permission('docket.approve')),
        service: DocketService = Depends(get_docket_service)):
    return service.approve(user.id, docket_id, data)
