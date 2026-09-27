from fastapi import APIRouter
from app.modules.authentication.router import router as authentication_router
from app.modules.complaints.router import router as complaint_router
from app.modules.dockets.router import router as docket_router
from app.modules.refusals.router import router as refusals_router
from app.modules.investigations.router import router as investigations_router
from app.modules.evidence.router import router as evidence_router
from app.modules.feedback.router import router as feedback_router
from app.modules.communications.router import router as communications_router
from app.modules.stations.router import router as stations_router

from app.modules.communications.case_email_api import router as case_email_router
from app.modules.investigations.contact import router as contact_router
from app.modules.complaints.intake_email import router as intake_email_router
from app.modules.administration.router import router as administration_router

router = APIRouter(prefix='/v1')
router.include_router(administration_router)
router.include_router(case_email_router)
router.include_router(contact_router)
router.include_router(intake_email_router)
router.include_router(authentication_router)
router.include_router(complaint_router)
router.include_router(refusals_router)
router.include_router(docket_router)
router.include_router(investigations_router)
router.include_router(evidence_router)
router.include_router(feedback_router)
router.include_router(communications_router)
router.include_router(stations_router)
