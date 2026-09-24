from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import engine
from app.db import models  # noqa: F401 -- register mappings before authentication queries
from app.api.router import router as api_router
from app.core.config import settings
from app.modules.evidence.middleware import EvidenceUploadLimit



app = FastAPI(
    title="SAPS Case-Docket Management System",
    version="1.0.0",
)
app.add_middleware(EvidenceUploadLimit)
app.include_router(api_router)
app.mount("/portal", StaticFiles(directory="app/frontend", html=True), name="portal")

officer_frontend_root = Path(__file__).resolve().parents[1] / 'frontend' / 'officer'
app.mount('/officer/assets', StaticFiles(directory=officer_frontend_root / 'assets'), name='officer-assets')


@app.get('/officer/', include_in_schema=False)
def officer_frontend():
    return FileResponse(officer_frontend_root / 'index.html', headers={
        'Cache-Control': 'no-store',
        'Content-Security-Policy': "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
                                   "style-src 'self'; script-src 'self'; object-src 'none'; "
                                   "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        'Referrer-Policy': 'no-referrer',
        'X-Content-Type-Options': 'nosniff',
    })


@app.get('/officer', include_in_schema=False)
def officer_frontend_redirect():
    return RedirectResponse('/officer/', status_code=307)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # FastAPI's default response includes the rejected input, which can be a
    # password, OTP, or token. Return only safe validation descriptions.
    return JSONResponse(status_code=422, content={'detail': [
        {'loc': list(error['loc']), 'msg': error['msg'], 'type': error['type']} for error in exc.errors()
    ]})


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc: SQLAlchemyError):
    return JSONResponse(status_code=503, content={'detail': 'Database service unavailable'})


@app.middleware('http')
async def protect_auth_responses(request: Request, call_next):
    protected = request.url.path.startswith(('/api/v1/auth', '/api/v1/complaints',
                                              '/api/v1/dockets', '/api/v1/refusal-',
                                              '/api/v1/investigations', '/api/v1/evidence',
                                              '/api/v1/notifications', '/api/v1/documents',
                                              '/api/v1/alerts', '/api/v1/dashboards',
                                              '/api/v1/stations'))
    if settings.environment == 'production' and protected and request.url.scheme != 'https':
        return JSONResponse(status_code=400, content={'detail': 'HTTPS is required'}, headers={'Cache-Control': 'no-store'})
    response = await call_next(request)
    if protected:
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Pragma'] = 'no-cache'
    return response


@app.get("/")
def root() -> dict[str, str]:
    return {
        "message": "SAPS Case-Docket Management API",
        "status": "running",
    }


@app.get("/health/database")
def database_health() -> dict[str, str]:
    with engine.connect() as connection:
        database_name = connection.execute(
            text("SELECT current_database()")
        ).scalar_one()

        current_user = connection.execute(
            text("SELECT current_user")
        ).scalar_one()

        active_schema = connection.execute(
            text("SELECT current_schema()")
        ).scalar_one()

    return {
        "status": "connected",
        "database": database_name,
        "user": current_user,
        "schema": active_schema,
    }
