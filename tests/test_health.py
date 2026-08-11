from fastapi.testclient import TestClient

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
