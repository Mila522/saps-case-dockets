import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response, UploadFile, File
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.access.models import User
from app.modules.authentication.dependencies import require_permission
from app.modules.evidence.schemas import EvidenceRequest, EvidenceOut, CustodyRequest, CustodyOut, FileOut
from app.modules.evidence.service import EvidenceService

router = APIRouter(tags=['Evidence'])


def service(db: Session = Depends(get_db)):
    return EvidenceService(db)


@router.post('/dockets/{docket_id}/evidence', response_model=EvidenceOut, status_code=201)
def register(docket_id: uuid.UUID, data: EvidenceRequest,
             user: User = Depends(require_permission('evidence.manage')), svc=Depends(service)):
    return svc.register(user.id, docket_id, data)


@router.get('/dockets/{docket_id}/evidence', response_model=list[EvidenceOut])
def list_items(docket_id: uuid.UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
               user: User = Depends(require_permission('evidence.manage')), svc=Depends(service)):
    return svc.list_items(user.id, docket_id, limit, offset)


@router.get('/evidence/{evidence_id}', response_model=EvidenceOut)
def get_item(evidence_id: uuid.UUID,
             user: User = Depends(require_permission('evidence.manage')), svc=Depends(service)):
    return svc.get_item(user.id, evidence_id)


@router.post('/evidence/{evidence_id}/custody-events', response_model=CustodyOut, status_code=201)
def custody(evidence_id: uuid.UUID, data: CustodyRequest,
            user: User = Depends(require_permission('evidence.manage')), svc=Depends(service)):
    return svc.custody(user.id, evidence_id, data)


@router.get('/evidence/{evidence_id}/custody-events', response_model=list[CustodyOut])
def history(evidence_id: uuid.UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
            user: User = Depends(require_permission('evidence.view_custody')), svc=Depends(service)):
    return svc.custody_history(user.id, evidence_id, limit, offset)


@router.post('/evidence/{evidence_id}/files', response_model=FileOut, status_code=201)
def upload(evidence_id: uuid.UUID, file: UploadFile = File(...),
           user: User = Depends(require_permission('evidence.manage')), svc=Depends(service)):
    try:
        return svc.upload(user.id, evidence_id, file)
    finally:
        file.file.close()


@router.get('/evidence/{evidence_id}/files', response_model=list[FileOut])
def files(evidence_id: uuid.UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
          user: User = Depends(require_permission('evidence.manage')), svc=Depends(service)):
    return svc.files(user.id, evidence_id, limit, offset)


@router.get('/evidence/{evidence_id}/files/{file_id}/download')
def download(evidence_id: uuid.UUID, file_id: uuid.UUID,
             user: User = Depends(require_permission('evidence.manage')), svc=Depends(service)):
    data, filename = svc.download(user.id, evidence_id, file_id)
    return Response(data, media_type='application/octet-stream', headers={
        'Content-Disposition': "attachment; filename*=UTF-8''" + quote(filename, safe=''),
        'X-Content-Type-Options': 'nosniff',
    })
