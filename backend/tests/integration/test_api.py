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


def test_datasets_empty_then_registered(client: TestClient, settings: Settings) -> None:
    assert client.get("/api/v1/datasets").json() == []

    from app.analysis.registry import register_dataset
    from app.db.session import create_app_engine, create_session_factory

    engine = create_app_engine(settings.database_url)
    with create_session_factory(engine).begin() as session:
        source_id = register_dataset(session, "adult_sample", settings.data_dir).id
    engine.dispose()

    (listed,) = client.get("/api/v1/datasets").json()
    assert listed["id"] == source_id
    assert listed["row_count"] == 50
    assert listed["root_view_id"].startswith("view_")
    assert {"name": "sex", "kind": "categorical", "dtype": "category"} in listed["columns"]
    assert client.get(f"/api/v1/datasets/{source_id}").json() == listed


def test_unknown_dataset_returns_404_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/datasets/src_nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "UNKNOWN_SOURCE"
