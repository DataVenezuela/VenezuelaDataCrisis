from __future__ import annotations

from fastapi.testclient import TestClient

from api.app.main import app


def test_openapi_is_available() -> None:
    response = TestClient(app).get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "VZLA_DEDUP Public API"


def test_openapi_excludes_sensitive_internal_fields() -> None:
    schema_text = TestClient(app).get("/openapi.json").text

    for field in (
        "cedula_hmac",
        "contact_hmac",
        "raw_json",
        "raw_text",
        "scraper_id",
        "partner_api_keys",
    ):
        assert field not in schema_text
