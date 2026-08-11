import uuid

from fastapi.testclient import TestClient
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from app.main import app

client = TestClient(app)


def test_app_error_uses_unified_format() -> None:
    response = client.get("/api/v1/test-error")
    assert response.status_code == 409
    body = response.json()
    assert body == {
        "data": None,
        "code": 409,
        "message": "演示冲突",
        "request_id": response.headers["x-request-id"],
    }


def test_client_request_id_is_honored() -> None:
    request_id = str(uuid.uuid4())
    response = client.get(
        "/api/v1/health", headers={"X-Request-ID": request_id}
    )
    assert response.headers["x-request-id"] == request_id


def test_invalid_request_id_generates_new_one() -> None:
    response = client.get(
        "/api/v1/health", headers={"X-Request-ID": "not-a-uuid"}
    )
    assert response.headers["x-request-id"] != "not-a-uuid"
    uuid.UUID(response.headers["x-request-id"])


def test_request_completion_log_contains_safe_request_metadata() -> None:
    request_id = str(uuid.uuid4())

    with capture_logs(processors=[merge_contextvars]) as logs:
        response = client.get(
            "/api/v1/health?token=secret-value",
            headers={"X-Request-ID": request_id},
        )

    records = [
        record
        for record in logs
        if record.get("event") == "http_request_completed"
    ]
    assert len(records) == 1
    record = records[0]
    assert record["request_id"] == request_id
    assert record["method"] == "GET"
    assert record["path"] == "/api/v1/health"
    assert record["status_code"] == response.status_code == 200
    assert record["duration_ms"] >= 0
    assert "secret-value" not in str(record)


def test_missing_route_returns_unified_404() -> None:
    response = client.get("/api/v1/not-exists")
    assert response.status_code == 404
    body = response.json()
    assert body["data"] is None
    assert body["code"] == 404
    assert body["message"] == "Not Found"
    assert body["request_id"] == response.headers["x-request-id"]


def test_validation_error_returns_unified_422() -> None:
    response = client.post("/api/v1/echo", json={})
    assert response.status_code == 422
    body = response.json()
    assert body["data"] is None
    assert body["code"] == 422
    assert body["message"]
    assert body["request_id"] == response.headers["x-request-id"]


def test_unhandled_error_returns_unified_500() -> None:
    @app.get("/api/v1/test-crash")
    def crash() -> None:
        raise RuntimeError("boom")

    crash_client = TestClient(app, raise_server_exceptions=False)
    response = crash_client.get("/api/v1/test-crash")
    assert response.status_code == 500
    assert response.json() == {
        "data": None,
        "code": 500,
        "message": "Internal Server Error",
        "request_id": response.headers["x-request-id"],
    }


def test_unhandled_error_log_contains_request_id_and_stack() -> None:
    @app.get("/api/v1/test-log-crash")
    def crash_for_log() -> None:
        raise RuntimeError("logged boom")

    crash_client = TestClient(app, raise_server_exceptions=False)
    with capture_logs(processors=[merge_contextvars]) as logs:
        response = crash_client.get("/api/v1/test-log-crash")

    records = [
        record
        for record in logs
        if record.get("event") == "unhandled_error"
    ]
    assert len(records) == 1
    assert records[0]["request_id"] == response.headers["x-request-id"]
    assert isinstance(records[0]["exc_info"], RuntimeError)
