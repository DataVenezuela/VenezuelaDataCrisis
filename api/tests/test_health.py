from __future__ import annotations

from fastapi.testclient import TestClient

from api.app.main import app


def test_health_returns_service_status() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "service": "vzla-dedup-api",
        "version": "0.1.0",
    }
