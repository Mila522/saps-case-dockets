import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.access.models import User
from app.modules.authentication.dependencies import require_permission
from app.modules.dockets.schemas import DocketApprovalOut, DocketApprovalRequest, DocketOut
from app.modules.dockets.service import DocketService

router = APIRouter(tags=['Dockets'])


def get_docket_service(db: Session = Depends(get_db)) -> DocketService:
    return DocketService(db)


@router.post('/complaints/{complaint_id}/dockets', response_model=DocketOut, status_code=201)
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
