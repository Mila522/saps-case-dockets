from fastapi import FastAPI
from sqlalchemy import text

from app.db.session import engine


app = FastAPI(
    title="SAPS Case-Docket Management System",
    version="1.0.0",
)


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