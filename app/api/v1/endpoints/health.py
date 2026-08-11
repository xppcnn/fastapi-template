import structlog
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.core.database import DbSession
from app.core.exceptions import AppError
from app.core.response import ok

router = APIRouter()
logger = structlog.get_logger(__name__)


@router.get("/health")
def health() -> dict:
    return ok({"status": "ok"})


@router.get("/health/live")
def liveness() -> dict:
    return ok({"status": "alive"})


@router.get("/health/ready")
async def readiness(session: DbSession) -> dict:
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning(
            "readiness_check_failed",
            message="Readiness check failed",
            component="database",
            error_type=type(exc).__name__,
        )
        raise AppError("Service Unavailable", code=503) from exc
    return ok({"status": "ready", "database": "ok"})


class EchoRequest(BaseModel):
    name: str


@router.post("/echo")
def echo(body: EchoRequest) -> dict:
    return ok({"name": body.name})


@router.get("/test-error")
def test_error() -> dict:
    raise AppError("演示冲突", code=409)
