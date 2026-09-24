from fastapi import APIRouter
from app.modules.authentication.router import router as authentication_router
from app.modules.complaints.router import router as complaint_router
from app.modules.dockets.router import router as docket_router
from app.modules.refusals.router import router as refusals_router
from app.modules.investigations.router import router as investigations_router
from app.modules.evidence.router import router as evidence_router
from app.modules.stations.router import router as stations_router

router = APIRouter(prefix='/v1')
router.include_router(authentication_router)
router.include_router(complaint_router)
router.include_router(refusals_router)
router.include_router(docket_router)
router.include_router(investigations_router)
router.include_router(evidence_router)
router.include_router(stations_router)
