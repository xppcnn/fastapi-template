import uuid

from fastapi.testclient import TestClient

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
