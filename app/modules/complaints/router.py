import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.access.models import User
from app.modules.authentication.dependencies import require_permission, get_current_active_user
from app.modules.complaints.schemas import (ComplaintTracking, ComplaintTrackingPage,
                                             StationComplaintOut, StationComplaintPage)
from app.modules.complaints.service import ComplaintTrackingService
from app.modules.complaints.service import ComplaintRegistrationService
from app.modules.complaints.schemas import ComplaintRegistration, ReceivingStationOut
from app.modules.complaints.service import StationComplaintService
from app.modules.complaints.intake import ComplaintIntakeService
from app.modules.complaints.schemas import (InStationRegistration, ReferenceTrackingRequest,
    StatementRequest, StatementOut, WitnessRequest, WitnessOut, ComplaintMaterialOut)
from app.modules.evidence.schemas import EvidenceRequest, EvidenceOut

router = APIRouter(prefix='/complaints', tags=['Complaints'])


def get_registration_service(db: Session = Depends(get_db)) -> ComplaintRegistrationService:
    return ComplaintRegistrationService(db)


@router.get('/receiving-stations', response_model=list[ReceivingStationOut])
def receiving_stations(user: User = Depends(require_permission('complaint.submit')),
                      service: ComplaintRegistrationService = Depends(get_registration_service)):
    return service.receiving_stations(user.id)


@router.post('', response_model=ComplaintTracking, status_code=201)
def register_complaint(data: ComplaintRegistration,
                       user: User = Depends(require_permission('complaint.submit')),
                       service: ComplaintRegistrationService = Depends(get_registration_service)):
    return service.register(user.id, data)


def get_tracking_service(db: Session = Depends(get_db)) -> ComplaintTrackingService:
    return ComplaintTrackingService(db)


def get_station_service(db: Session = Depends(get_db)) -> StationComplaintService:
    return StationComplaintService(db)


def get_intake_service(db: Session = Depends(get_db)):
    return ComplaintIntakeService(db)


@router.get('/in-station/receiving-station', response_model=ReceivingStationOut)
def intake_station(user: User = Depends(require_permission('complaint.register')),
                   service: ComplaintIntakeService = Depends(get_intake_service)):
    return service.receiving_station(user.id)


@router.post('/in-station', response_model=StationComplaintOut, status_code=201,
             summary='Record a new walk-in complainant and complaint at the actor station')
def in_station(data: InStationRegistration, user: User = Depends(require_permission('complaint.register')),
               service: ComplaintIntakeService = Depends(get_intake_service)):
    return service.register(user.id, data)


@router.post('/track-by-reference', response_model=ComplaintTracking,
             summary='Track an exact reference belonging to the signed-in complainant')
def track_reference(data: ReferenceTrackingRequest, user: User = Depends(require_permission('case.track_own')),
                    service: ComplaintTrackingService = Depends(get_tracking_service)):
    return service.track(user.id, reference_number=data.reference_number)


@router.get('/{complaint_id}/updates')
def own_updates(complaint_id: uuid.UUID, user: User = Depends(require_permission('case.track_own')),
                db: Session = Depends(get_db)):
    from sqlalchemy import select
    from app.modules.dockets.models import Docket
    from app.modules.feedback.service import FeedbackService
    from app.modules.authentication.security import utcnow
    ComplaintTrackingService(db).track(user.id, complaint_id=complaint_id)
    docket = db.scalar(select(Docket).where(Docket.complaint_id == complaint_id))
    if docket is None:
        return {'docket_status': None, 'feedback': []}
    page = FeedbackService(db).list(user, docket.id)
    superseded = {row.supersedes_feedback_id for row in page.items if row.supersedes_feedback_id}
    return {'docket_status': docket.status, 'feedback': [
        {'subject': row.subject, 'message': row.message, 'published_at': row.published_at} for row in page.items
        if row.is_official and row.published_at <= utcnow() and row.id not in superseded]}


@router.get('/{complaint_id}/materials', response_model=ComplaintMaterialOut)
def materials(complaint_id: uuid.UUID, user: User = Depends(get_current_active_user),
              service: ComplaintIntakeService = Depends(get_intake_service)):
    return service.materials(user.id, complaint_id)


@router.post('/{complaint_id}/statements', response_model=StatementOut, status_code=201)
def statement(complaint_id: uuid.UUID, data: StatementRequest, user: User = Depends(get_current_active_user),
              service: ComplaintIntakeService = Depends(get_intake_service)):
    return service.append_statement(user.id, complaint_id, data)


@router.post('/{complaint_id}/witnesses', response_model=WitnessOut, status_code=201)
def witness(complaint_id: uuid.UUID, data: WitnessRequest, user: User = Depends(get_current_active_user),
            service: ComplaintIntakeService = Depends(get_intake_service)):
    return service.add_witness(user.id, complaint_id, data)


@router.post('/{complaint_id}/witnesses/{witness_id}/statements', response_model=StatementOut, status_code=201)
def witness_statement(complaint_id: uuid.UUID, witness_id: uuid.UUID, data: StatementRequest,
                      user: User = Depends(get_current_active_user), service: ComplaintIntakeService = Depends(get_intake_service)):
    return service.append_statement(user.id, complaint_id, data, witness_id)


@router.post('/{complaint_id}/initial-evidence', response_model=EvidenceOut, status_code=201,
             summary='Register initial evidence at the receiving station before commander approval')
def initial_evidence(complaint_id: uuid.UUID, data: EvidenceRequest,
                     user: User = Depends(require_permission('complaint.register')),
                     service: ComplaintIntakeService = Depends(get_intake_service)):
    return service.initial_evidence(user.id, complaint_id, data)


@router.get('/station', response_model=StationComplaintPage)
def list_station_complaints(
        status: Literal['SUBMITTED', 'UNDER_REVIEW', 'ACCEPTED', 'REFUSED', 'ESCALATED',
                        'DOCKET_CREATED'] | None = None,
        limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
        user: User = Depends(require_permission('complaint.view_station')),
        service: StationComplaintService = Depends(get_station_service)):
    return service.list(user.id, status, limit=limit, offset=offset)


@router.post('/{complaint_id}/review', response_model=StationComplaintOut,
             summary='Start complaint review',
             description='The active station charge officer moves SUBMITTED to UNDER_REVIEW. Records complaint.review.start atomically.',
             responses={401: {'description': 'Authentication required'}, 403: {'description': 'Active charge officer required'},
                        404: {'description': 'Complaint unavailable'}, 409: {'description': 'Already reviewed or decided'}})
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
