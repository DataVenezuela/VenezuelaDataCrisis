from __future__ import annotations

from fastapi.testclient import TestClient

from api.app.main import app


client = TestClient(app)


def test_persons_list_contract() -> None:
    response = client.get("/v1/persons")

    assert response.status_code == 200
    assert response.json() == {"items": [], "limit": 50, "offset": 0}


def test_person_detail_returns_404_when_missing() -> None:
    response = client.get("/v1/persons/missing-id")

    assert response.status_code == 404


def test_events_list_contract() -> None:
    response = client.get("/v1/events")

    assert response.status_code == 200
    assert response.json() == {"items": [], "limit": 50, "offset": 0}


def test_acopio_list_contract() -> None:
    response = client.get("/v1/acopio")

    assert response.status_code == 200
    assert response.json() == {"items": [], "limit": 50, "offset": 0}


def test_stats_contract() -> None:
    response = client.get("/v1/stats")

    assert response.status_code == 200
    assert response.json() == {
        "persons": {
            "total": 0,
            "missing": 0,
            "found": 0,
            "injured": 0,
            "deceased": 0,
            "unknown": 0,
        },
        "events": {"total": 0, "active": 0, "monitoring": 0, "closed": 0},
        "acopio": {"total": 0, "active": 0, "full": 0, "closed": 0, "unverified": 0},
    }
