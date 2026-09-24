from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.access.models import User
from app.modules.authentication.dependencies import require_permission
from app.modules.stations.schemas import InvestigatorOut
from app.modules.stations.service import StationOfficerService

router = APIRouter(prefix='/stations', tags=['Stations'])


def service(db: Session = Depends(get_db)) -> StationOfficerService:
    return StationOfficerService(db)


@router.get('/investigators', response_model=list[InvestigatorOut])
def list_station_investigators(
        user: User = Depends(require_permission('docket.assign')),
        svc: StationOfficerService = Depends(service)):
    return svc.investigators(user.id)
