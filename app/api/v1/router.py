from fastapi import APIRouter
from app.modules.authentication.router import router as authentication_router
from app.modules.complaints.router import router as complaint_router

router = APIRouter(prefix='/v1')
router.include_router(authentication_router)
router.include_router(complaint_router)
