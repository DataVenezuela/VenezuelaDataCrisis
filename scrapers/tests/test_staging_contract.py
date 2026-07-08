"""
scrapers/tests/test_staging_contract.py
==========================================
Test de contrato del payload contra el ``aportes`` canonico.

Valida que los payloads que genera ``_build_payload`` tengan las columnas
canonicas (segun ``docs/schema.md`` y ``docs/specs/db-scraper-contract.md``, tras
el cutover Bronze de #256) y que el batch upsert use ``on_conflict`` resoluble.

Cutover #256: el payload emite ``artifact_id`` (FK NOT NULL -> raw_artifacts) y
ya NO emite ``run_id``/``scraper_id``/``source_url``/``parser_version`` (la
procedencia de corrida y la URL viven en ``raw_artifacts``).
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from scrapers.exporters.staging_exporter import StagingConfig, StagingExporter

_EVENT_ID = "8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a"
_ARTIFACT_UUID = "c1d2e3f4-a5b6-7890-cdef-1234567890ab"

# Columnas del ``aportes`` canonico que puede emitir _build_payload
# (docs/schema.md). id/created_at los genera la DB.
_APORTES_COLUMNS = {
    "entity_type", "external_id", "dedup_hash", "dedup_version",
    "block_keys", "content_hash", "source_id", "artifact_id",
    "raw_json", "source_record_id", "normalizer_version",
}

# El watermark vive en public.sources (columna watermark_at), keyeado por
# source_id (el filtro va en la URL, no en el body). ADR 0009.
_WATERMARK_BODY_COLUMNS = {"watermark_at"}

# Columnas que produce _build_payload (obligatorias + opcionales)
_PAYLOAD_REQUIRED = {"entity_type", "external_id", "dedup_version", "block_keys",
                     "content_hash", "source_id", "artifact_id", "raw_json"}
_PAYLOAD_OPTIONAL = {"dedup_hash", "source_record_id", "normalizer_version"}

# Claves legacy que el cutover #256 elimino del payload de aportes.
_LEGACY_DROPPED = {"run_id", "scraper_id", "source_url", "parser_version"}


def _person(det: str | None = "detid123") -> dict[str, Any]:
    return {
        "_entity_type": "Person",
        "_artifact_id": _ARTIFACT_UUID,
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
    # _build_payload ya no hace ningun GET (source_id llega resuelto); el
    # transport nunca se invoca, se deja trivial.
    client = httpx.Client(
        base_url="https://project.supabase.co",
        transport=httpx.MockTransport(lambda r: httpx.Response(404)),
    )
    return StagingExporter(cfg, client=client, run_id="run-test")


class TestPayloadContract:
    """Valida columnas del payload contra el aportes canonico."""

    def test_all_payload_keys_are_valid_columns(self) -> None:
        exp = _exporter_for_payload()
        payload = exp._build_payload(_person(), "demo_src")
        all_keys = set(payload.keys())
        invalid = all_keys - _APORTES_COLUMNS
        assert not invalid, f"keys del payload que no son columnas de aportes: {invalid}"

    def test_required_keys_always_present(self) -> None:
        exp = _exporter_for_payload()
        payload = exp._build_payload(_person(), "demo_src")
        missing = _PAYLOAD_REQUIRED - set(payload.keys())
        assert not missing, f"faltan keys requeridas en payload: {missing}"

    def test_artifact_id_is_present_and_from_meta(self) -> None:
        exp = _exporter_for_payload()
        payload = exp._build_payload(_person(), "demo_src")
        assert payload["artifact_id"] == _ARTIFACT_UUID

    def test_legacy_provenance_keys_dropped(self) -> None:
        exp = _exporter_for_payload()
        payload = exp._build_payload(_person(), "demo_src")
        present_legacy = _LEGACY_DROPPED & set(payload.keys())
        assert not present_legacy, f"claves legacy no deben viajar: {present_legacy}"

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
    """El watermark vive en sources.watermark_at, keyeado por source_id (ADR 0009)."""

    def test_watermark_targets_sources_table(self) -> None:
        from scrapers.exporters.staging_exporter import _SOURCES_PATH
        assert _SOURCES_PATH == "/rest/v1/sources"

    def test_watermark_write_is_patch_with_only_watermark_at(self) -> None:
        """El PATCH del watermark filtra por source_id (URL) y el body solo trae
        watermark_at: ni slug ni source_id viajan en el cuerpo."""
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/rest/v1/aportes":
                return httpx.Response(201, json={})
            if request.url.path == "/rest/v1/sources" and request.method == "PATCH":
                captured["source_id"] = request.url.params.get("source_id")
                captured["body"] = json.loads(request.content)
                # return=representation: fila actualizada (array no vacio).
                return httpx.Response(200, json=[captured["body"]])
            return httpx.Response(200, json=[{"watermark_at": None}])

        cfg = StagingConfig(
            supabase_url="https://project.supabase.co", publishable_key="k", ingest_jwt="jwt"
        )
        client = httpx.Client(
            base_url="https://project.supabase.co", transport=httpx.MockTransport(handler)
        )
        exp = StagingExporter(cfg, client=client, run_id="r")
        exp.export_source(
            [_person()], source_id="fuente-x", source_fetched_ats=["2026-06-24T16:00:00Z"]
        )
        assert captured["source_id"] == "eq.fuente-x"
        assert set(captured["body"]) == _WATERMARK_BODY_COLUMNS


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
