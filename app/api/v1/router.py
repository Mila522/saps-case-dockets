from fastapi import APIRouter
from app.modules.authentication.router import router as authentication_router

router = APIRouter(prefix='/v1')
router.include_router(authentication_router)
