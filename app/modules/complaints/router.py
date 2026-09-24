import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.access.models import User
from app.modules.authentication.dependencies import require_permission
from app.modules.complaints.schemas import (ComplaintTracking, ComplaintTrackingPage,
                                             StationComplaintOut, StationComplaintPage)
from app.modules.complaints.service import ComplaintTrackingService
from app.modules.complaints.service import ComplaintRegistrationService
from app.modules.complaints.schemas import ComplaintRegistration
from app.modules.complaints.service import StationComplaintService

router = APIRouter(prefix='/complaints', tags=['Complaints'])


def get_registration_service(db: Session = Depends(get_db)) -> ComplaintRegistrationService:
    return ComplaintRegistrationService(db)


@router.post('', response_model=ComplaintTracking, status_code=201)
def register_complaint(data: ComplaintRegistration,
                       user: User = Depends(require_permission('complaint.submit')),
                       service: ComplaintRegistrationService = Depends(get_registration_service)):
    return service.register(user.id, data)


def get_tracking_service(db: Session = Depends(get_db)) -> ComplaintTrackingService:
    return ComplaintTrackingService(db)


def get_station_service(db: Session = Depends(get_db)) -> StationComplaintService:
    return StationComplaintService(db)


@router.get('/station', response_model=StationComplaintPage)
def list_station_complaints(
        status: Literal['SUBMITTED', 'UNDER_REVIEW', 'ACCEPTED', 'REFUSED', 'ESCALATED',
                        'DOCKET_CREATED'] | None = None,
        limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
        user: User = Depends(require_permission('complaint.view_station')),
        service: StationComplaintService = Depends(get_station_service)):
    return service.list(user.id, status, limit=limit, offset=offset)


@router.post('/{complaint_id}/review', response_model=StationComplaintOut)
def start_complaint_review(
        complaint_id: uuid.UUID,
        user: User = Depends(require_permission('complaint.decide')),
        service: StationComplaintService = Depends(get_station_service)):
    return service.start_review(user.id, complaint_id)


@router.get('/mine', response_model=ComplaintTrackingPage)
def list_own_complaints(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                        user: User = Depends(require_permission('case.track_own')),
                        service: ComplaintTrackingService = Depends(get_tracking_service)):
    return service.track(user.id, limit=limit, offset=offset)


@router.get('/{complaint_id}/tracking', response_model=ComplaintTracking)
def track_own_complaint(complaint_id: uuid.UUID,
                        user: User = Depends(require_permission('case.track_own')),
                        service: ComplaintTrackingService = Depends(get_tracking_service)):
    return service.track(user.id, complaint_id)
