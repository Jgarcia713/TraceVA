from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_health_ok_on_migrated_database(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["schema_up_to_date"] is True
    assert body["schema_revision"] == body["schema_head"]
    assert response.headers["X-Request-ID"].startswith("req_")


def test_health_degraded_when_not_migrated(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        body = client.get("/api/v1/health").json()
    assert body["status"] == "degraded"
    assert body["schema_revision"] is None
    assert body["schema_up_to_date"] is False


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "NOT_FOUND"
    assert error["request_id"] == response.headers["X-Request-ID"]
