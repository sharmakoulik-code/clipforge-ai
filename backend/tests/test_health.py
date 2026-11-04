from fastapi.testclient import TestClient

from app.main import app
from app.models.models import Base

client = TestClient(app)


def test_root_is_available() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_is_available() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_checks_database() -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}


def test_initial_model_inventory_is_present() -> None:
    expected = {
        "users", "projects", "transcripts", "clips", "processing_jobs",
        "processing_stages", "processing_checkpoints", "credit_wallets",
        "credit_transactions", "subscriptions", "payments", "clip_edits",
        "clip_render_versions", "webhook_events", "usage_records",
        "system_configurations",
    }
    assert expected == set(Base.metadata.tables)
