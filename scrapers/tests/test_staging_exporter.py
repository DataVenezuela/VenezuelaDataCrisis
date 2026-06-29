"""
scrapers/tests/test_staging_exporter.py
=========================================
Tests del StagingExporter, 100% offline.

Ningun test hace red real: el httpx.Client se construye con un
``_RecordingTransport`` (subclase de httpx.BaseTransport) inyectado via el
parametro ``client`` del constructor. El transport responde a /api/aportes y
a /api/source_watermarks y registra los bodies para los asserts.
"""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import patch

import httpx

from scrapers.dedup import specs
from scrapers.exporters.staging_exporter import (
    ExportResult,
    StagingConfig,
    StagingExporter,
    compute_external_id,
)

_EVENT_ID = "8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a"


class _RecordingTransport(httpx.BaseTransport):
    """Captura POSTs a /api/aportes y PUTs a /api/source_watermarks."""

    def __init__(self, aportes_status: int = 201, watermark_status: int = 200) -> None:
        self.aportes_status = aportes_status
        self.watermark_status = watermark_status
        self.posts: list[dict[str, Any]] = []
        self.watermark_puts: list[dict[str, Any]] = []
        self.watermark_gets: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/aportes":
            self.posts.append(json.loads(request.content))
            return httpx.Response(self.aportes_status, json={"ok": True})
        if path.startswith("/api/source_watermarks"):
            if request.method == "GET":
                self.watermark_gets.append(path)
                return httpx.Response(404)
            self.watermark_puts.append(json.loads(request.content))
            return httpx.Response(self.watermark_status, json={"ok": True})
        return httpx.Response(404)


def _exporter(transport: _RecordingTransport) -> StagingExporter:
    cfg = StagingConfig(api_key="k", base_url="https://staging.test", source_slug="demo")
    client = httpx.Client(base_url="https://staging.test", transport=transport)
    return StagingExporter(cfg, client=client, run_id="run-1")


def _person(name: str, hmac: str | None = None, det: str | None = "detid123") -> dict[str, Any]:
    return {
        "_entity_type": "Person",
        "full_name": name,
        "event_id": _EVENT_ID,
        "last_known_location": "Lara",
        "deterministic_id": det,
        "cedula_hmac": hmac,
        "fuente": "x",
        "status": "missing",
    }


def _event() -> dict[str, Any]:
    return {
        "_entity_type": "Event",
        "event_type": "earthquake",
        "location_text": "Ciudad Demo, Estado Demo",
        "date_iso": "2026-06-24T14:32:00Z",
        "description": "Sismo demo reportado",
        "fuente": "x",
    }


def _acopio() -> dict[str, Any]:
    return {
        "_entity_type": "AcopioCenter",
        "name": "Centro de Acopio Demo",
        "event_id": _EVENT_ID,
        "location_text": "Ciudad Demo, Estado Demo",
        "fuente": "x",
    }


# --- payload ----------------------------------------------------------------

class TestPayload:
    def test_payload_has_all_required_keys(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source([_person("Juan")], source_fetched_ats=["2026-06-24T15:00:00Z"])
        body = t.posts[0]
        required = {
            "run_id", "entity_type", "external_id", "dedup_hash", "dedup_version",
            "block_keys", "content_hash", "source_slug", "source_record_id",
            "source_url", "parser_version", "normalizer_version", "data",
        }
        assert required.issubset(body.keys())

    def test_data_strips_internal_keys(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source([_person("Juan")], source_fetched_ats=["2026-06-24T15:00:00Z"])
        data = t.posts[0]["data"]
        assert all(not k.startswith("_") for k in data)
        assert "full_name" in data

    def test_entity_type_is_slug(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source([_person("Juan")], source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert t.posts[0]["entity_type"] == "person"

    def test_run_id_propagated(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source([_person("Juan")], source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert t.posts[0]["run_id"] == "run-1"

    def test_dedup_version_person(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source([_person("Juan")], source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert t.posts[0]["dedup_version"] == "person-detid-v1"

    def test_content_hash_has_sha256_prefix(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source([_person("Juan")], source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert t.posts[0]["content_hash"].startswith("sha256:")

    def test_dedup_hash_null_when_no_deterministic_id(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source(
            [_person("Juan", det=None)], source_fetched_ats=["2026-06-24T15:00:00Z"]
        )
        # dedup_hash None se serializa como JSON null.
        assert t.posts[0]["dedup_hash"] is None


# --- fingerprint compartido Event/AcopioCenter (eficiencia, issue #125) ------

class TestSharedFingerprint:
    """Event/AcopioCenter: external_id y dedup_hash derivan del mismo fingerprint.

    El fingerprint se calcula una sola vez en _build_payload; estos tests
    verifican que los valores resultantes no cambian (siguen siendo el
    fingerprint v1) y que ambas keys coinciden entre si.
    """

    def test_event_external_id_equals_dedup_hash(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source([_event()], source_fetched_ats=["2026-06-24T15:00:00Z"])
        body = t.posts[0]
        assert body["external_id"] == body["dedup_hash"]

    def test_event_external_id_is_fingerprint_v1(self) -> None:
        rec = _event()
        t = _RecordingTransport()
        _exporter(t).export_source([rec], source_fetched_ats=["2026-06-24T15:00:00Z"])
        body = t.posts[0]
        expected = specs.event_dedup_key(rec)
        assert body["external_id"] == expected
        assert body["dedup_hash"] == expected

    def test_acopio_external_id_equals_dedup_hash(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source([_acopio()], source_fetched_ats=["2026-06-24T15:00:00Z"])
        body = t.posts[0]
        assert body["external_id"] == body["dedup_hash"]

    def test_acopio_external_id_is_fingerprint_v1(self) -> None:
        rec = _acopio()
        t = _RecordingTransport()
        _exporter(t).export_source([rec], source_fetched_ats=["2026-06-24T15:00:00Z"])
        body = t.posts[0]
        expected = specs.acopio_dedup_key(rec)
        assert body["external_id"] == expected
        assert body["dedup_hash"] == expected

    def test_values_match_legacy_separate_computation(self) -> None:
        """Equivalencia exacta con el computo separado previo (sin cambios)."""
        for rec in (_event(), _acopio()):
            entity_type = rec["_entity_type"]
            t = _RecordingTransport()
            _exporter(t).export_source([rec], source_fetched_ats=["2026-06-24T15:00:00Z"])
            body = t.posts[0]
            assert body["external_id"] == compute_external_id(rec, entity_type)
            assert body["dedup_hash"] == specs.dedup_key(rec, entity_type)


# --- idempotencia -----------------------------------------------------------

class TestIdempotency:
    def test_idempotent_external_id_same_across_runs(self) -> None:
        t1, t2 = _RecordingTransport(), _RecordingTransport()
        _exporter(t1).export_source([_person("Juan")], source_fetched_ats=["2026-06-24T15:00:00Z"])
        _exporter(t2).export_source([_person("Juan")], source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert t1.posts[0]["external_id"] == t2.posts[0]["external_id"]

    def test_person_external_id_is_deterministic_id(self) -> None:
        rec = _person("Juan", det="abc999")
        assert compute_external_id(rec, "Person") == "abc999"

    def test_person_external_id_fallback_to_hmac(self) -> None:
        rec = _person("Juan", hmac="hmac-1", det=None)
        eid = compute_external_id(rec, "Person")
        assert eid and len(eid) == 64  # sha256 hex
        # estable
        assert compute_external_id(_person("Juan", hmac="hmac-1", det=None), "Person") == eid

    def test_person_external_id_fallback_distinguishes_records(self) -> None:
        a = compute_external_id(_person("Juan", det=None), "Person")
        b = compute_external_id(_person("Ana", det=None), "Person")
        assert a != b


# --- block keys -------------------------------------------------------------

class TestBlockKeys:
    def test_person_with_hmac_has_ced_block_key(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source([_person("Juan", hmac="abc")], source_fetched_ats=["2026-06-24T15:00:00Z"])
        keys = t.posts[0]["block_keys"]
        assert any(k.startswith(f"ced:{_EVENT_ID}:abc") for k in keys)

    def test_person_without_hmac_only_phonetic_block_key(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source([_person("Juan")], source_fetched_ats=["2026-06-24T15:00:00Z"])
        keys = t.posts[0]["block_keys"]
        assert all(not k.startswith("ced:") for k in keys)
        assert any(k.startswith("phon:") for k in keys)


# --- watermark --------------------------------------------------------------

class TestWatermark:
    def test_watermark_advances_on_full_success(self) -> None:
        t = _RecordingTransport(aportes_status=201)
        _exporter(t).export_source(
            [_person("Juan")],
            source_fetched_ats=["2026-06-24T15:00:00Z", "2026-06-24T16:00:00Z"],
        )
        assert t.watermark_puts
        assert t.watermark_puts[-1]["watermark_at"] == "2026-06-24T16:00:00Z"
        assert t.watermark_puts[-1]["source_slug"] == "demo"

    def test_watermark_not_set_on_post_failure(self) -> None:
        t = _RecordingTransport(aportes_status=500)
        res = _exporter(t).export_source(
            [_person("Juan")],
            source_fetched_ats=["2026-06-24T16:00:00Z"],
        )
        assert res.errors
        assert t.watermark_puts == []

    def test_watermark_not_set_without_fetched_ats(self) -> None:
        t = _RecordingTransport(aportes_status=201)
        _exporter(t).export_source([_person("Juan")], source_fetched_ats=[])
        assert t.watermark_puts == []


# --- clasificacion de respuestas --------------------------------------------

class TestResponseClassification:
    def test_201_counts_as_sent(self) -> None:
        t = _RecordingTransport(aportes_status=201)
        res = _exporter(t).export_source([_person("Juan")], source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert res.sent == 1 and res.duplicates == 0 and res.errors == []

    def test_409_counts_as_duplicate(self) -> None:
        t = _RecordingTransport(aportes_status=409)
        res = _exporter(t).export_source([_person("Juan")], source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert res.duplicates == 1 and res.sent == 0

    def test_500_counts_as_error_without_raising(self) -> None:
        t = _RecordingTransport(aportes_status=500)
        res = _exporter(t).export_source([_person("Juan")], source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert len(res.errors) >= 1 and res.sent == 0


# --- retry del POST ---------------------------------------------------------

class _FlakyTransport(httpx.BaseTransport):
    """Devuelve los status de ``aportes_sequence`` en orden para /api/aportes."""

    def __init__(self, aportes_sequence: list[int]) -> None:
        self.aportes_sequence = aportes_sequence
        self.attempts = 0
        self.watermark_puts: list[dict[str, Any]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/aportes":
            idx = min(self.attempts, len(self.aportes_sequence) - 1)
            status = self.aportes_sequence[idx]
            self.attempts += 1
            return httpx.Response(status, json={"ok": True})
        if path.startswith("/api/source_watermarks"):
            if request.method == "GET":
                return httpx.Response(404)
            self.watermark_puts.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)


class TestPostRetry:
    def test_503_then_200_ends_as_sent(self) -> None:
        t = _FlakyTransport([503, 200])
        cfg = StagingConfig(api_key="k", base_url="https://staging.test", source_slug="demo")
        client = httpx.Client(base_url="https://staging.test", transport=t)
        exp = StagingExporter(cfg, client=client, run_id="run-1")
        with patch("scrapers.exporters.staging_exporter.time.sleep", lambda *_: None):
            res = exp.export_source(
                [_person("Juan")], source_fetched_ats=["2026-06-24T15:00:00Z"]
            )
        assert res.sent == 1
        assert res.errors == []
        assert t.attempts == 2  # 503 reintentado, luego 200

    def test_persistent_503_ends_as_error(self) -> None:
        t = _FlakyTransport([503])
        cfg = StagingConfig(api_key="k", base_url="https://staging.test", source_slug="demo")
        client = httpx.Client(base_url="https://staging.test", transport=t)
        exp = StagingExporter(cfg, client=client, run_id="run-1")
        with patch("scrapers.exporters.staging_exporter.time.sleep", lambda *_: None):
            res = exp.export_source(
                [_person("Juan")], source_fetched_ats=["2026-06-24T15:00:00Z"]
            )
        assert res.sent == 0
        assert res.errors
        assert t.watermark_puts == []


# --- source_errors bloquean el watermark (C6) -------------------------------

class TestSourceErrorsWatermark:
    def test_source_errors_block_watermark_advance(self) -> None:
        t = _RecordingTransport(aportes_status=201)
        res = _exporter(t).export_source(
            [_person("Juan")],
            source_fetched_ats=["2026-06-24T16:00:00Z"],
            source_errors=["menor descartado por proteccion fail-closed"],
        )
        # El POST fue exitoso, pero el watermark NO avanza por source_errors.
        assert res.sent == 1
        assert t.watermark_puts == []

    def test_empty_source_errors_allow_watermark_advance(self) -> None:
        t = _RecordingTransport(aportes_status=201)
        _exporter(t).export_source(
            [_person("Juan")],
            source_fetched_ats=["2026-06-24T16:00:00Z"],
            source_errors=[],
        )
        assert t.watermark_puts
        assert t.watermark_puts[-1]["watermark_at"] == "2026-06-24T16:00:00Z"


# --- dry-run ----------------------------------------------------------------

class TestDryRun:
    def test_dry_run_disabled_sends_nothing(self) -> None:
        exp = StagingExporter(None, run_id="run-1")
        res = exp.export_source([_person("Juan")], source_fetched_ats=["2026-06-24T16:00:00Z"])
        assert res.sent == 0 and res.duplicates == 0 and res.errors == []

    def test_dry_run_builds_payload_without_network(self) -> None:
        # No transport, no cliente: en dry-run no se abre conexion alguna.
        exp = StagingExporter(None)
        assert exp.enabled is False
        res = exp.export_source([_person("Juan", hmac="abc")], source_fetched_ats=["2026-06-24T16:00:00Z"])
        assert isinstance(res, ExportResult)

    def test_from_env_none_when_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert StagingConfig.from_env() is None

    def test_from_env_no_vars_logs_info(self, caplog: Any) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with caplog.at_level("INFO", logger="scrapers.exporters.staging_exporter"):
                assert StagingConfig.from_env() is None
        assert any(r.levelname == "INFO" for r in caplog.records)
        assert not any(r.levelname == "ERROR" for r in caplog.records)

    def test_from_env_partial_config_logs_error(self, caplog: Any) -> None:
        env = {"STAGING_API_KEY": "k", "STAGING_BASE_URL": "https://staging.test"}
        with patch.dict(os.environ, env, clear=True):
            with caplog.at_level("ERROR", logger="scrapers.exporters.staging_exporter"):
                assert StagingConfig.from_env() is None
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert errors
        assert "STAGING_SOURCE_SLUG" in errors[0].getMessage()

    def test_from_env_builds_config_when_present(self) -> None:
        env = {
            "STAGING_API_KEY": "k",
            "STAGING_BASE_URL": "https://staging.test/",
            "STAGING_SOURCE_SLUG": "demo",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = StagingConfig.from_env()
        assert cfg is not None
        assert cfg.base_url == "https://staging.test"  # rstrip('/')


# --- ciclo de vida ----------------------------------------------------------

class TestLifecycle:
    def test_does_not_close_injected_client(self) -> None:
        t = _RecordingTransport()
        client = httpx.Client(base_url="https://staging.test", transport=t)
        exp = StagingExporter(
            StagingConfig(api_key="k", base_url="https://staging.test", source_slug="demo"),
            client=client,
        )
        exp.close()
        # El cliente inyectado sigue usable (no fue cerrado por el exporter).
        resp = client.get("/api/source_watermarks/demo")
        assert resp.status_code == 404
        client.close()
