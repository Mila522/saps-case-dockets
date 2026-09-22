from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import engine
from app.db import models  # noqa: F401 -- register mappings before authentication queries
from app.api.router import router as api_router
from app.core.config import settings


app = FastAPI(
    title="SAPS Case-Docket Management System",
    version="1.0.0",
)
app.include_router(api_router)


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
    if settings.environment == 'production' and request.url.path.startswith('/api/v1/auth') and request.url.scheme != 'https':
        return JSONResponse(status_code=400, content={'detail': 'HTTPS is required'}, headers={'Cache-Control': 'no-store'})
    response = await call_next(request)
    if request.url.path.startswith('/api/v1/auth'):
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
