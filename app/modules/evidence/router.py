import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response, UploadFile, File
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.access.models import User
from app.modules.authentication.dependencies import require_permission
from app.modules.evidence.schemas import EvidenceRequest, EvidenceOut, CustodyRequest, CustodyOut, FileOut
from app.modules.evidence.service import EvidenceService

from app.modules.investigations.schemas import ERROR_RESPONSES

router = APIRouter(responses=ERROR_RESPONSES, tags=['Evidence'])


def service(db: Session = Depends(get_db)):
    return EvidenceService(db)


@router.post('/dockets/{docket_id}/evidence', response_model=EvidenceOut, status_code=201,
             summary='Register evidence and initial custody',
             description='Active assigned investigators only. Allocate a prototype EVD reference, record initial custodian/location and audit in one transaction.')
def register(docket_id: uuid.UUID, data: EvidenceRequest,
             user: User = Depends(require_permission('evidence.manage')), svc=Depends(service)):
    return svc.register(user.id, docket_id, data)


@router.get('/dockets/{docket_id}/evidence', response_model=list[EvidenceOut],
             summary='List docket evidence',
             description='Paginated evidence metadata for the active assigned investigator. No binary content or storage keys are exposed.')
def list_items(docket_id: uuid.UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
               user: User = Depends(require_permission('evidence.manage')), svc=Depends(service)):
    return svc.list_items(user.id, docket_id, limit, offset)


@router.get('/evidence/{evidence_id}', response_model=EvidenceOut,
             summary='Read evidence and its custody version',
             description='Return custody_version for the next custody change. Access requires the current docket assignment and station.')
def get_item(evidence_id: uuid.UUID,
             user: User = Depends(require_permission('evidence.manage')), svc=Depends(service)):
    return svc.get_item(user.id, evidence_id)


@router.post('/evidence/{evidence_id}/custody-events', response_model=CustodyOut, status_code=201,
             summary='Append a custody change',
             description='Supply expected_custody_event_id from the current custody_version. Stale versions return 409. Source comes from locked database state. Transfers require an active same-station investigator and destination location.')
def custody(evidence_id: uuid.UUID, data: CustodyRequest,
            user: User = Depends(require_permission('evidence.manage')), svc=Depends(service)):
    return svc.custody(user.id, evidence_id, data)


@router.get('/evidence/{evidence_id}/custody-events', response_model=list[CustodyOut],
             summary='Read custody history',
             description='Append-only events visible to the active assigned investigator with evidence.view_custody. Sensitive read is audited.')
def history(evidence_id: uuid.UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
            user: User = Depends(require_permission('evidence.view_custody')), svc=Depends(service)):
    return svc.custody_history(user.id, evidence_id, limit, offset)


@router.post('/evidence/{evidence_id}/files', response_model=FileOut, status_code=201,
             summary='Upload a protected evidence file',
             description='Multipart file field; size, SHA-256 and version are calculated by the server. Metadata only in PostgreSQL; bytes in existing private local storage. No client storage key accepted.', responses={413: {'description': 'File or multipart request exceeds configured size limit'}})
def upload(evidence_id: uuid.UUID, file: UploadFile = File(...),
           user: User = Depends(require_permission('evidence.manage')), svc=Depends(service)):
    try:
        return svc.upload(user.id, evidence_id, file)
    finally:
        file.file.close()


@router.get('/evidence/{evidence_id}/files', response_model=list[FileOut],
             summary='List immutable file metadata',
             description='Paginated versions, size and SHA-256; private storage keys and credentials are excluded.')
def files(evidence_id: uuid.UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
          user: User = Depends(require_permission('evidence.manage')), svc=Depends(service)):
    return svc.files(user.id, evidence_id, limit, offset)


@router.get('/evidence/{evidence_id}/files/{file_id}/download',
             summary='Download verified evidence bytes',
             description='Verify stored size/hash before returning an attachment. Requires active assignment; creates audit and ACCESSED custody records.', response_class=Response, responses={200: {'description': 'Verified binary attachment', 'content': {'application/octet-stream': {'schema': {'type': 'string', 'format': 'binary'}}}}})
def download(evidence_id: uuid.UUID, file_id: uuid.UUID,
             user: User = Depends(require_permission('evidence.manage')), svc=Depends(service)):
    data, filename = svc.download(user.id, evidence_id, file_id)
    return Response(data, media_type='application/octet-stream', headers={
        'Content-Disposition': "attachment; filename*=UTF-8''" + quote(filename, safe=''),
        'X-Content-Type-Options': 'nosniff',
    })
