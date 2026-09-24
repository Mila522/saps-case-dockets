from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.access.models import User
from app.modules.authentication.dependencies import get_current_active_user, require_permission
from app.modules.feedback.schemas import FeedbackCreate, FeedbackPage, FeedbackResponse
from app.modules.feedback.service import FeedbackService

router = APIRouter(prefix='/dockets/{docket_id}/feedback', tags=['Feedback'])


@router.post('', response_model=FeedbackResponse, status_code=201)
def publish_feedback(docket_id: UUID, data: FeedbackCreate, response: Response,
                     user: User = Depends(require_permission('feedback.provide')), db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    return FeedbackService(db).publish(user, docket_id, data)


@router.get('', response_model=FeedbackPage)
def list_feedback(docket_id: UUID, response: Response, limit: int = Query(50, ge=1, le=100),
                  offset: int = Query(0, ge=0), user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    return FeedbackService(db).list(user, docket_id, limit, offset)


@router.get('/{feedback_id}', response_model=FeedbackResponse)
def get_feedback(docket_id: UUID, feedback_id: UUID, response: Response,
                 user: User = Depends(get_current_active_user), db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    return FeedbackService(db).get(user, docket_id, feedback_id)
