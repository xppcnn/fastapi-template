from collections.abc import AsyncGenerator
from typing import Any

from fastapi.testclient import TestClient
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from app.core.database import get_db
from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "data": {"status": "ok"},
        "code": 200,
        "message": "ok",
    }


def test_liveness_does_not_require_database() -> None:
    async def database_must_not_be_used() -> AsyncGenerator[Any, None]:
        raise AssertionError("liveness must not access the database")
        yield

    app.dependency_overrides[get_db] = database_must_not_be_used
    try:
        response = client.get("/api/v1/health/live")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json() == {
        "data": {"status": "alive"},
        "code": 200,
        "message": "ok",
    }


def test_readiness_checks_database() -> None:
    class AvailableSession:
        def __init__(self) -> None:
            self.statement: str | None = None

        async def execute(self, statement) -> None:
            self.statement = str(statement)

    session = AvailableSession()

    async def override_db() -> AsyncGenerator[Any, None]:
        yield session

    app.dependency_overrides[get_db] = override_db
    try:
        response = client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert session.statement == "SELECT 1"
    assert response.status_code == 200
    assert response.json() == {
        "data": {"status": "ready", "database": "ok"},
        "code": 200,
        "message": "ok",
    }


def test_readiness_returns_503_when_database_is_unavailable() -> None:
    class UnavailableSession:
        async def execute(self, statement) -> None:
            raise RuntimeError("database credentials must not be exposed")

    async def override_db() -> AsyncGenerator[Any, None]:
        yield UnavailableSession()

    app.dependency_overrides[get_db] = override_db
    try:
        with capture_logs(processors=[merge_contextvars]) as logs:
            response = client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 503
    assert response.json() == {
        "data": None,
        "code": 503,
        "message": "Service Unavailable",
        "request_id": response.headers["x-request-id"],
    }
    assert "credentials" not in response.text
    failure_logs = [
        record for record in logs if record.get("event") == "readiness_check_failed"
    ]
    assert len(failure_logs) == 1
    assert failure_logs[0]["component"] == "database"
    assert failure_logs[0]["error_type"] == "RuntimeError"
    assert "credentials" not in str(failure_logs[0])
