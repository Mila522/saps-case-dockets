import uuid

from fastapi import APIRouter, Depends, Query
from typing import Literal
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.access.models import User
from app.modules.authentication.dependencies import require_permission, get_current_active_user
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


@router.get('/dockets/{docket_id}/full')
def full_docket(docket_id: uuid.UUID, user: User = Depends(get_current_active_user),
                db: Session = Depends(get_db)):
    from app.modules.dockets.full import FullDocketService
    return FullDocketService(db).get(user.id, docket_id)


@router.get('/dockets/{docket_id}/files/{file_id}/download')
def commander_file(docket_id: uuid.UUID, file_id: uuid.UUID,
                   user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    from sqlalchemy import select
    from fastapi import HTTPException, Response
    from urllib.parse import quote
    from app.modules.dockets.full import FullDocketService
    from app.modules.evidence.models import EvidenceFile, EvidenceItem
    from app.modules.evidence.storage import EvidenceStorage
    service = FullDocketService(db)
    officer, docket = service.read_scope(user.id, docket_id)
    row = db.scalar(select(EvidenceFile).join(EvidenceItem).where(EvidenceFile.id == file_id, EvidenceItem.docket_id == docket.id))
    if row is None:
        raise HTTPException(404, 'File not found')
    content = EvidenceStorage().read_verified(row)
    filename = quote(row.original_filename, safe='')
    service.audit(user.id, officer.station_id, 'docket.evidence.download', 'evidence_file', row.id)
    db.commit()
    return Response(content, media_type='application/octet-stream', headers={'Cache-Control': 'no-store',
        'Content-Disposition': "attachment; filename*=UTF-8''" + filename, 'X-Content-Type-Options': 'nosniff'})


@router.get('/complaints/{complaint_id}/dossier')
def complaint_dossier(complaint_id: uuid.UUID, user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    from app.modules.dockets.full import FullDocketService
    return FullDocketService(db).complaint(user.id, complaint_id)
