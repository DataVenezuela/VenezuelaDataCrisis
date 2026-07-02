"""
scrapers/tests/test_staging_contract.py
==========================================
Test de contrato del payload contra el schema real de dataVenezuela.

Valida que los payloads que genera _build_payload tengan las columnas
correctas (según las migraciones 0001_init.sql y 0008_ingesta_staging_dedup.sql)
y que el batch upsert use on_conflict resoluble.
"""

from __future__ import annotations

from typing import Any

import httpx

from scrapers.exporters.staging_exporter import StagingConfig, StagingExporter

_EVENT_ID = "8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a"

# Columnas reales de public.aportes según 0001_init + 0008_ingesta_staging_dedup
_APORTES_COLUMNS = {
    "run_id", "entity_type", "external_id", "dedup_hash", "dedup_version",
    "block_keys", "content_hash", "source_id", "scraper_id",
    "raw_json", "source_record_id", "source_url",
    "parser_version", "normalizer_version",
}

# Columnas de public.source_watermarks según 0008
_WATERMARK_COLUMNS = {"source_slug", "watermark_at"}

# Columnas que produce _build_payload (obligatorias + opcionales)
_PAYLOAD_REQUIRED = {"run_id", "entity_type", "external_id", "dedup_version",
                     "block_keys", "content_hash", "source_id", "scraper_id", "raw_json"}
_PAYLOAD_OPTIONAL = {"dedup_hash", "source_record_id", "source_url",
                     "parser_version", "normalizer_version"}


def _person(det: str | None = "detid123") -> dict[str, Any]:
    return {
        "_entity_type": "Person",
        "full_name": "JUAN DEMO",
        "event_id": _EVENT_ID,
        "last_known_location": "Lara",
        "deterministic_id": det,
        "fuente": "x",
        "status": "missing",
    }


def _exporter_for_payload() -> StagingExporter:
    cfg = StagingConfig(
        supabase_url="https://project.supabase.co",
        publishable_key="k",
        ingest_jwt="jwt",
    )
    client = httpx.Client(
        base_url="https://project.supabase.co",
        transport=httpx.MockTransport(lambda r: (
            httpx.Response(200, json=[{"id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}])
            if r.url.path == "/rest/v1/sources"
            else httpx.Response(404)
        )),
    )
    return StagingExporter(cfg, client=client, run_id="run-test")


class TestPayloadContract:
    """Valida columnas del payload contra el schema real de aportes."""

    def test_all_payload_keys_are_valid_columns(self) -> None:
        exp = _exporter_for_payload()
        payload = exp._build_payload(_person(), "demo_src")
        all_keys = set(payload.keys())
        # Todas las keys del payload deben ser columnas reales de aportes
        invalid = all_keys - _APORTES_COLUMNS
        assert not invalid, f"keys del payload que no son columnas de aportes: {invalid}"

    def test_required_keys_always_present(self) -> None:
        exp = _exporter_for_payload()
        payload = exp._build_payload(_person(), "demo_src")
        missing = _PAYLOAD_REQUIRED - set(payload.keys())
        assert not missing, f"faltan keys requeridas en payload: {missing}"

    def test_optional_keys_omitted_when_none(self) -> None:
        exp = _exporter_for_payload()
        payload = exp._build_payload(_person(), "demo_src")
        for key in _PAYLOAD_OPTIONAL:
            assert key not in payload or payload[key] is not None, (
                f"{key} debe omitirse o tener valor, no null"
            )

    def test_no_source_slug_in_payload(self) -> None:
        """source_slug es string, la DB espera source_id uuid."""
        exp = _exporter_for_payload()
        payload = exp._build_payload(_person(), "demo_src")
        assert "source_slug" not in payload
        assert "source_id" in payload

    def test_dedup_hash_absent_when_no_deterministic_id(self) -> None:
        exp = _exporter_for_payload()
        payload = exp._build_payload(_person(det=None), "demo_src")
        assert "dedup_hash" not in payload


class TestWatermarkContract:
    """Valida columnas del watermark contra el schema real de source_watermarks."""

    def test_watermark_body_uses_source_slug(self) -> None:
        """La PK de source_watermarks es source_slug, no slug."""
        from scrapers.exporters.staging_exporter import _WATERMARKS_PATH as wp
        assert "source_watermarks" in wp


class TestOnConflict:
    """Valida que el upsert use on_conflict resoluble."""

    def test_upsert_url_has_on_conflict(self) -> None:
        from scrapers.exporters.staging_exporter import _APORTES_UPSERT_PATH as path
        assert "on_conflict=" in path, (
            "el upsert debe especificar on_conflict para que PostgREST "
            "pueda resolver merge-duplicates"
        )
        assert "on_conflict=source_id,external_id" in path, (
            f"path debe contener on_conflict=source_id,external_id, got: {path}"
        )

    def test_scraper_id_is_constant(self) -> None:
        from scrapers.exporters.staging_exporter import _SCRAPER_ID
        assert _SCRAPER_ID, "scraper_id debe ser un UUID no vacio"
        assert _SCRAPER_ID.count("-") == 4, "scraper_id debe tener formato UUID"
