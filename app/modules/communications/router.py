from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.access.models import User
from app.modules.authentication.dependencies import get_current_active_user, require_permission
from app.modules.alerts.service import AlertService
from app.modules.communications.dashboards import DashboardService
from app.modules.communications.documents import DocumentService
from app.modules.communications.notifications import NotificationService
from app.modules.communications.schemas import AlertOut, AlertTransition, DocumentOut, DocumentRequest, NotificationOut


def private_response(response: Response):
    response.headers['Cache-Control'] = 'no-store'


router = APIRouter(tags=['Communications and oversight'], dependencies=[Depends(private_response)])


@router.get('/notifications', response_model=list[NotificationOut])
def notifications(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
        user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    return NotificationService(db).list(user, limit, offset)


@router.get('/notifications/{notification_id}', response_model=NotificationOut)
def notification(notification_id: UUID, user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    return NotificationService(db).get(user, notification_id)


@router.get('/complaints/{complaint_id}/documents', response_model=list[DocumentOut])
def documents(complaint_id: UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
        user: User = Depends(require_permission('confirmation.download')), db: Session = Depends(get_db)):
    return DocumentService(db).list(user, complaint_id, limit, offset)


@router.post('/complaints/{complaint_id}/documents', response_model=DocumentOut, status_code=201)
def generate_document(complaint_id: UUID, data: DocumentRequest,
        user: User = Depends(require_permission('confirmation.download')), db: Session = Depends(get_db)):
    return DocumentService(db).generate(user, complaint_id, data)


@router.get('/documents/{document_id}/download')
def download_document(document_id: UUID,
        user: User = Depends(require_permission('confirmation.download')), db: Session = Depends(get_db)):
    content, identifier = DocumentService(db).download(user, document_id)
    return Response(content, media_type='text/html', headers={'Cache-Control': 'no-store',
        'Content-Disposition': f'attachment; filename="confirmation-{identifier}.html"',
        'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "sandbox; default-src 'none'; style-src 'unsafe-inline'"})


@router.get('/alerts', response_model=list[AlertOut])
def alerts(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
        status: Literal['OPEN', 'ACKNOWLEDGED', 'RESOLVED', 'DISMISSED'] | None = None,
        user: User = Depends(require_permission('alert.view')), db: Session = Depends(get_db)):
    return AlertService(db).list(user, limit, offset, status)


@router.post('/alerts/evaluate')
def evaluate_alerts(user: User = Depends(require_permission('alert.view')), db: Session = Depends(get_db)):
    return AlertService(db).evaluate(user)


@router.patch('/alerts/{alert_id}', response_model=AlertOut)
def transition_alert(alert_id: UUID, data: AlertTransition,
        user: User = Depends(require_permission('alert.view')), db: Session = Depends(get_db)):
    return AlertService(db).transition(user, alert_id, data)


@router.get('/dashboards/summary')
def dashboard(user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    return DashboardService(db).summary(user)
