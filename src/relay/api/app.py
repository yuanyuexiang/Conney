from fastapi import FastAPI, Response
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from relay.database.session import get_engine

app = FastAPI(title="Relay", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/api/v1/health/live")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/health/ready")
def readiness(response: Response) -> dict[str, str]:
    try:
        with get_engine().connect() as connection:
            initialized = connection.scalar(text("SELECT 1 FROM alembic_version LIMIT 1"))
    except (SQLAlchemyError, ValidationError):
        initialized = None

    if initialized != 1:
        response.status_code = 503
        return {"status": "unavailable"}
    return {"status": "ok"}
