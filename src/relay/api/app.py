from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from relay.api.auth import router as auth_router
from relay.api.dashboard import router as dashboard_router
from relay.api.delivery import router as delivery_router
from relay.api.intake import router as intake_router
from relay.api.mailbox import router as mailbox_router
from relay.api.operations import router as operations_router
from relay.database.session import get_engine

app = FastAPI(title="Relay", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)


app.include_router(auth_router)
app.include_router(mailbox_router)
app.include_router(intake_router)
app.include_router(delivery_router)
app.include_router(operations_router)
app.include_router(dashboard_router)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    if request.url.path.startswith("/api/v1/mailbox"):
        return JSONResponse({"detail": "mailbox_invalid_fields"}, status_code=422)
    if request.url.path.startswith(("/api/v1/auth", "/api/v1/users")):
        return JSONResponse({"detail": "invalid_user_fields"}, status_code=422)
    if request.url.path.startswith(("/api/v1/material-mappings", "/api/v1/delivery")):
        return JSONResponse({"detail": "invalid_delivery_fields"}, status_code=422)
    return await request_validation_exception_handler(request, error)


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
