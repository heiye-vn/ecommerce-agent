import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    return TestClient(app)


def test_health_returns_service_and_dependencies(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "commerce-agent-backend",
        "dependencies": ["postgresql", "redis", "rabbitmq"],
    }


def test_health_live_returns_ok(client: TestClient) -> None:
    response = client.get("/api/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "commerce-agent-backend"}


def test_health_ready_reports_degraded_without_database_engine(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.main import app

    if hasattr(app.state, "database_engine"):
        monkeypatch.delattr(app.state, "database_engine")
    if hasattr(app.state, "settings"):
        monkeypatch.delattr(app.state, "settings")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("RABBITMQ_URL", "")

    response = client.get("/api/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["postgresql"] == "unconfigured"
