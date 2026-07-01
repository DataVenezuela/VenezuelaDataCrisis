"""
scrapers/tests/test_staging_exporter.py
=========================================
Tests del StagingExporter, 100% offline.

Ningun test hace red real: el httpx.Client se construye con un
``_RecordingTransport`` (subclase de httpx.BaseTransport) inyectado via el
parametro ``client`` del constructor. El transport responde a
POST /rest/v1/aportes (upsert masivo, body = array JSON) y a
GET/POST /rest/v1/source_watermarks (lectura/escritura del watermark via
PostgREST) y registra los bodies para los asserts.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any
from unittest.mock import patch

import httpx

from scrapers.dedup import specs
from scrapers.exporters.staging_exporter import (
    ExportResult,
    StagingConfig,
    StagingExporter,
    _apply_safety_margin,
    _BATCH_SIZE,
    compute_external_id,
)

_EVENT_ID = "8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a"


class _RecordingTransport(httpx.BaseTransport):
    """Captura upserts a /rest/v1/aportes y lecturas/escrituras de watermark.

    ``posts`` queda aplanado (un item por record, en el orden en que llegaron
    los batches) para que los tests de payload/idempotencia/block-keys que
    solo mandan 1 record no tengan que lidiar con la estructura de batch.
    ``batches`` conserva la agrupacion real (una lista por request de POST).
    """

    def __init__(self, aportes_status: int = 201, watermark_status: int = 200) -> None:
        self.aportes_status = aportes_status
        self.watermark_status = watermark_status
        self.posts: list[dict[str, Any]] = []
        self.batches: list[list[dict[str, Any]]] = []
        # source_slug se infiere del body del POST (columna "slug").
        self.watermark_puts: list[dict[str, Any]] = []
        self.watermark_gets: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/rest/v1/aportes":
            batch = json.loads(request.content)
            self.batches.append(batch)
            self.posts.extend(batch)
            return httpx.Response(self.aportes_status, json={"ok": True})
        if path == "/rest/v1/source_watermarks":
            if request.method == "GET":
                slug = request.url.params.get("slug", "")
                self.watermark_gets.append(slug.removeprefix("eq."))
                return httpx.Response(200, json=[])
            body = json.loads(request.content)
            self.watermark_puts.append(
                {"source_slug": body.get("slug"), "watermarkAt": body.get("watermark_at")}
            )
            return httpx.Response(self.watermark_status, json={"ok": True})
        return httpx.Response(404)


def _exporter(transport: httpx.BaseTransport) -> StagingExporter:
    cfg = StagingConfig(
        supabase_url="https://staging.test", supabase_service_role_key="k"
    )
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
        _exporter(t).export_source([_person("Juan")], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        body = t.posts[0]
        always_present = {
            "run_id", "entity_type", "external_id", "dedup_version",
            "block_keys", "content_hash", "source_slug", "raw_json",
        }
        assert always_present.issubset(body.keys())

    def test_optional_keys_present_as_null_not_omitted(self) -> None:
        """A diferencia del contrato Zod anterior, PostgREST exige que todas
        las filas de un mismo batch tengan las mismas keys: los opcionales se
        mandan siempre presentes, con null cuando faltan."""
        t = _RecordingTransport()
        _exporter(t).export_source(
            [_person("Juan", det=None)], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"]
        )
        body = t.posts[0]
        for key in ("dedup_hash", "source_record_id", "source_url", "parser_version", "normalizer_version"):
            assert key in body

    def test_data_strips_internal_keys(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source([_person("Juan")], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        data = t.posts[0]["raw_json"]
        assert all(not k.startswith("_") for k in data)
        assert "full_name" in data

    def test_entity_type_is_slug(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source([_person("Juan")], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert t.posts[0]["entity_type"] == "person"

    def test_run_id_propagated(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source([_person("Juan")], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert t.posts[0]["run_id"] == "run-1"

    def test_dedup_version_person(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source([_person("Juan")], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert t.posts[0]["dedup_version"] == "person-detid-v1"

    def test_content_hash_has_64_hexchars(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source([_person("Juan")], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert re.fullmatch(r"[0-9a-f]{64}", t.posts[0]["content_hash"])

    def test_dedup_hash_null_when_no_deterministic_id(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source(
            [_person("Juan", det=None)], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"]
        )
        assert t.posts[0]["dedup_hash"] is None

    def test_entity_type_acopio_uses_acopio_slug(self) -> None:
        # Verifica que AcopioCenter mapea a "acopio" (no "acopio_center")
        t = _RecordingTransport()
        _exporter(t).export_source([_acopio()], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert t.posts[0]["entity_type"] == "acopio"


# --- fingerprint compartido Event/AcopioCenter (eficiencia, issue #125) ------

class TestSharedFingerprint:
    """Event/AcopioCenter: external_id y dedup_hash derivan del mismo fingerprint."""

    def test_event_external_id_equals_dedup_hash(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source([_event()], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        body = t.posts[0]
        assert body["external_id"] == body["dedup_hash"]

    def test_event_external_id_is_fingerprint_v1(self) -> None:
        rec = _event()
        t = _RecordingTransport()
        _exporter(t).export_source([rec], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        body = t.posts[0]
        expected = specs.event_dedup_key(rec)
        assert body["external_id"] == expected
        assert body["dedup_hash"] == expected

    def test_acopio_external_id_equals_dedup_hash(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source([_acopio()], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        body = t.posts[0]
        assert body["external_id"] == body["dedup_hash"]

    def test_acopio_external_id_is_fingerprint_v1(self) -> None:
        rec = _acopio()
        t = _RecordingTransport()
        _exporter(t).export_source([rec], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        body = t.posts[0]
        expected = specs.acopio_dedup_key(rec)
        assert body["external_id"] == expected
        assert body["dedup_hash"] == expected

    def test_values_match_legacy_separate_computation(self) -> None:
        """Equivalencia exacta con el computo separado previo (sin cambios)."""
        for rec in (_event(), _acopio()):
            entity_type = rec["_entity_type"]
            t = _RecordingTransport()
            _exporter(t).export_source([rec], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
            body = t.posts[0]
            assert body["external_id"] == compute_external_id(rec, entity_type)
            assert body["dedup_hash"] == specs.dedup_key(rec, entity_type)


# --- idempotencia -----------------------------------------------------------

class TestIdempotency:
    def test_idempotent_external_id_same_across_runs(self) -> None:
        t1, t2 = _RecordingTransport(), _RecordingTransport()
        _exporter(t1).export_source([_person("Juan")], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        _exporter(t2).export_source([_person("Juan")], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert t1.posts[0]["external_id"] == t2.posts[0]["external_id"]

    def test_person_external_id_is_deterministic_id(self) -> None:
        rec = _person("Juan", det="abc999")
        assert compute_external_id(rec, "Person") == "abc999"

    def test_person_external_id_fallback_to_hmac(self) -> None:
        rec = _person("Juan", hmac="hmac-1", det=None)
        eid = compute_external_id(rec, "Person")
        assert eid and len(eid) == 64  # sha256 hex
        assert compute_external_id(_person("Juan", hmac="hmac-1", det=None), "Person") == eid

    def test_person_external_id_fallback_distinguishes_records(self) -> None:
        a = compute_external_id(_person("Juan", det=None), "Person")
        b = compute_external_id(_person("Ana", det=None), "Person")
        assert a != b


# --- block keys -------------------------------------------------------------

class TestBlockKeys:
    def test_person_with_hmac_has_ced_block_key(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source([_person("Juan", hmac="abc")], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        keys = t.posts[0]["block_keys"]
        assert any(k.startswith(f"ced:{_EVENT_ID}:abc") for k in keys)

    def test_person_without_hmac_only_phonetic_block_key(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source([_person("Juan")], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        keys = t.posts[0]["block_keys"]
        assert all(not k.startswith("ced:") for k in keys)
        assert any(k.startswith("phon:") for k in keys)


# --- watermark --------------------------------------------------------------

class TestWatermark:
    def test_watermark_advances_on_full_success(self) -> None:
        t = _RecordingTransport(aportes_status=201)
        _exporter(t).export_source(
            [_person("Juan")],
            source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z", "2026-06-24T16:00:00Z"],
        )
        assert t.watermark_puts
        assert t.watermark_puts[-1]["watermarkAt"] == "2026-06-24T15:55:00Z"
        assert t.watermark_puts[-1]["source_slug"] == "demo"

    def test_watermark_not_set_on_post_failure(self) -> None:
        t = _RecordingTransport(aportes_status=500)
        res = _exporter(t).export_source(
            [_person("Juan")],
            source_slug="demo", source_fetched_ats=["2026-06-24T16:00:00Z"],
        )
        assert res.errors
        assert t.watermark_puts == []

    def test_watermark_not_set_without_fetched_ats(self) -> None:
        t = _RecordingTransport(aportes_status=201)
        _exporter(t).export_source([_person("Juan")], source_slug="demo", source_fetched_ats=[])
        assert t.watermark_puts == []

    def test_watermark_advance_is_monotonic_across_runs(self) -> None:
        t = _RecordingTransport(aportes_status=201)
        exp = _exporter(t)
        exp.export_source(
            [_person("Juan")], source_slug="demo", source_fetched_ats=["2026-06-24T16:00:00Z"]
        )
        exp.export_source(
            [_person("Ana")], source_slug="demo", source_fetched_ats=["2026-06-24T16:01:00Z"]
        )
        assert [p["watermarkAt"] for p in t.watermark_puts] == [
            "2026-06-24T15:55:00Z",
            "2026-06-24T15:56:00Z",
        ]

    def test_post_targets_watermarks_table_with_snake_case_body(self) -> None:
        """Contrato PostgREST: POST /rest/v1/source_watermarks con body
        {"slug": ..., "watermark_at": ...} y Prefer: resolution=merge-duplicates."""
        captured: dict[str, Any] = {}

        class _Transport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                if request.url.path == "/rest/v1/aportes":
                    return httpx.Response(201, json={"ok": True})
                if request.method == "POST" and request.url.path == "/rest/v1/source_watermarks":
                    captured["path"] = request.url.path
                    captured["body"] = json.loads(request.content)
                    captured["prefer"] = request.headers.get("prefer")
                    return httpx.Response(200, json={"ok": True})
                return httpx.Response(404)

        _exporter(_Transport()).export_source(
            [_person("Juan")], source_slug="fuente-x", source_fetched_ats=["2026-06-24T16:00:00Z"]
        )
        assert captured["path"] == "/rest/v1/source_watermarks"
        assert captured["body"] == {"slug": "fuente-x", "watermark_at": "2026-06-24T15:55:00Z"}
        assert "resolution=merge-duplicates" in captured["prefer"]

    def test_watermark_is_per_source_slug(self) -> None:
        """Dos fuentes en la misma corrida avanzan watermarks independientes."""
        t = _RecordingTransport(aportes_status=201)
        exp = _exporter(t)
        exp.export_source(
            [_person("Juan")], source_slug="fuente-a", source_fetched_ats=["2026-06-24T10:00:00Z"]
        )
        exp.export_source(
            [_person("Ana")], source_slug="fuente-b", source_fetched_ats=["2026-06-24T20:00:00Z"]
        )
        slugs_to_watermark = {p["source_slug"]: p["watermarkAt"] for p in t.watermark_puts}
        assert slugs_to_watermark == {
            "fuente-a": "2026-06-24T09:55:00Z",
            "fuente-b": "2026-06-24T19:55:00Z",
        }


# --- margen de seguridad del watermark ---------------------------------------

class TestSafetyMargin:
    def test_subtracts_five_minutes(self) -> None:
        assert _apply_safety_margin("2026-06-24T16:00:00Z") == "2026-06-24T15:55:00Z"

    def test_crosses_day_boundary(self) -> None:
        assert _apply_safety_margin("2026-06-24T00:02:00Z") == "2026-06-23T23:57:00Z"

    def test_malformed_input_returned_unchanged(self) -> None:
        assert _apply_safety_margin("no-es-una-fecha") == "no-es-una-fecha"


# --- get_watermark (lectura previa al fetch) ---------------------------------

class TestGetWatermark:
    def test_returns_default_when_no_row_matches(self) -> None:
        """PostgREST responde 200 con lista vacia cuando el filtro no matchea
        ninguna fila (a diferencia del 404 de la vieja API de dataVenezuela)."""
        t = _RecordingTransport()
        assert _exporter(t).get_watermark("fuente-nueva") == "1970-01-01T00:00:00Z"
        assert t.watermark_gets == ["fuente-nueva"]

    def test_returns_persisted_value(self) -> None:
        class _Transport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                if request.url.path == "/rest/v1/source_watermarks":
                    assert request.url.params.get("slug") == "eq.fuente-a"
                    return httpx.Response(200, json=[{"watermark_at": "2026-06-20T00:00:00Z"}])
                return httpx.Response(404)

        assert _exporter(_Transport()).get_watermark("fuente-a") == "2026-06-20T00:00:00Z"

    def test_returns_default_when_disabled(self) -> None:
        exp = StagingExporter(None)
        assert exp.get_watermark("fuente-a") == "1970-01-01T00:00:00Z"

    def test_returns_default_on_http_error(self) -> None:
        class _FailingTransport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("sin red")

        assert _exporter(_FailingTransport()).get_watermark("fuente-a") == "1970-01-01T00:00:00Z"

    def test_returns_default_on_malformed_json_body(self) -> None:
        """200 con body no-JSON no debe propagar json.JSONDecodeError (fail-open)."""
        class _Transport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, content=b"not json")

        assert _exporter(_Transport()).get_watermark("fuente-a") == "1970-01-01T00:00:00Z"

    def test_returns_default_on_non_list_json_body(self) -> None:
        """200 con JSON valido pero no-lista (ej. dict de error) tampoco debe propagar."""
        class _Transport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={"watermark_at": "2026-06-20T00:00:00Z"})

        assert _exporter(_Transport()).get_watermark("fuente-a") == "1970-01-01T00:00:00Z"


# --- auth ---------------------------------------------------------------

class TestAuth:
    def test_uses_apikey_and_bearer_headers(self) -> None:
        """Contrato Supabase/PostgREST: apikey + Authorization: Bearer con la
        service_role key, no x-api-key."""
        cfg = StagingConfig(supabase_url="https://staging.test", supabase_service_role_key="secret-key")
        exp = StagingExporter(cfg, run_id="run-1")
        assert exp._client is not None
        assert exp._client.headers["apikey"] == "secret-key"
        assert exp._client.headers["authorization"] == "Bearer secret-key"
        assert "x-api-key" not in exp._client.headers
        exp.close()


# --- clasificacion de respuestas --------------------------------------------

class TestResponseClassification:
    def test_201_counts_as_sent(self) -> None:
        t = _RecordingTransport(aportes_status=201)
        res = _exporter(t).export_source([_person("Juan")], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert res.sent == 1 and res.duplicates == 0 and res.errors == []

    def test_204_counts_as_sent(self) -> None:
        """return=minimal tipicamente responde 201; 204 tambien se acepta."""
        t = _RecordingTransport(aportes_status=204)
        res = _exporter(t).export_source([_person("Juan")], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert res.sent == 1 and res.errors == []

    def test_500_counts_as_error_without_raising(self) -> None:
        t = _RecordingTransport(aportes_status=500)
        res = _exporter(t).export_source([_person("Juan")], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert len(res.errors) >= 1 and res.sent == 0


# --- retry del POST ---------------------------------------------------------

class _FlakyTransport(httpx.BaseTransport):
    """Devuelve los status de ``aportes_sequence`` en orden para /rest/v1/aportes."""

    def __init__(self, aportes_sequence: list[int]) -> None:
        self.aportes_sequence = aportes_sequence
        self.attempts = 0
        self.watermark_puts: list[dict[str, Any]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/rest/v1/aportes":
            idx = min(self.attempts, len(self.aportes_sequence) - 1)
            status = self.aportes_sequence[idx]
            self.attempts += 1
            return httpx.Response(status, json={"ok": True})
        if path == "/rest/v1/source_watermarks":
            if request.method == "GET":
                return httpx.Response(200, json=[])
            self.watermark_puts.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)


class TestPostRetry:
    def test_503_then_200_ends_as_sent(self) -> None:
        t = _FlakyTransport([503, 200])
        cfg = StagingConfig(supabase_url="https://staging.test", supabase_service_role_key="k")
        client = httpx.Client(base_url="https://staging.test", transport=t)
        exp = StagingExporter(cfg, client=client, run_id="run-1")
        with patch("scrapers.exporters.staging_exporter.time.sleep", lambda *_: None):
            res = exp.export_source(
                [_person("Juan")], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"]
            )
        assert res.sent == 1
        assert res.errors == []
        assert t.attempts == 2  # 503 reintentado, luego 200

    def test_persistent_503_ends_as_error(self) -> None:
        t = _FlakyTransport([503])
        cfg = StagingConfig(supabase_url="https://staging.test", supabase_service_role_key="k")
        client = httpx.Client(base_url="https://staging.test", transport=t)
        exp = StagingExporter(cfg, client=client, run_id="run-1")
        with patch("scrapers.exporters.staging_exporter.time.sleep", lambda *_: None):
            res = exp.export_source(
                [_person("Juan")], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"]
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
            source_slug="demo", source_fetched_ats=["2026-06-24T16:00:00Z"],
            source_errors=["menor descartado por proteccion fail-closed"],
        )
        assert res.sent == 1
        assert t.watermark_puts == []

    def test_empty_source_errors_allow_watermark_advance(self) -> None:
        t = _RecordingTransport(aportes_status=201)
        _exporter(t).export_source(
            [_person("Juan")],
            source_slug="demo", source_fetched_ats=["2026-06-24T16:00:00Z"],
            source_errors=[],
        )
        assert t.watermark_puts
        assert t.watermark_puts[-1]["watermarkAt"] == "2026-06-24T15:55:00Z"


# --- batching y paralelismo --------------------------------------------------

class TestBatching:
    def test_records_chunked_into_batches_of_default_size(self) -> None:
        n = _BATCH_SIZE * 2 + 7
        records = [_person(f"P{i}", det=f"det{i}") for i in range(n)]
        t = _RecordingTransport(aportes_status=201)
        res = _exporter(t).export_source(
            records, source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"]
        )
        assert res.sent == n
        assert res.errors == []
        assert len(t.batches) == 3
        assert [len(b) for b in t.batches] == [_BATCH_SIZE, _BATCH_SIZE, 7]

    def test_single_batch_for_small_source(self) -> None:
        records = [_person(f"P{i}", det=f"det{i}") for i in range(5)]
        t = _RecordingTransport(aportes_status=201)
        _exporter(t).export_source(records, source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert len(t.batches) == 1
        assert len(t.batches[0]) == 5

    def test_contadores_correctos_con_multiples_workers(self) -> None:
        records = [_person(f"P{i}", det=f"det{i}") for i in range(_BATCH_SIZE * 4)]
        t = _RecordingTransport(aportes_status=201)
        res = _exporter(t).export_source(
            records,
            source_slug="demo",
            source_fetched_ats=["2026-06-24T15:00:00Z"],
            max_concurrent_posts=4,
        )
        assert res.sent == _BATCH_SIZE * 4
        assert res.duplicates == 0
        assert res.errors == []
        assert len(t.batches) == 4

    def test_batch_failure_counts_whole_batch_as_error(self) -> None:
        """PostgREST inserta el batch como una sola transaccion: falla atomico,
        no hay exito parcial fila-por-fila como con el POST individual viejo."""
        records = [_person(f"P{i}", det=f"det{i}") for i in range(10)]
        t = _RecordingTransport(aportes_status=500)
        res = _exporter(t).export_source(
            records, source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"], max_concurrent_posts=2
        )
        assert res.sent == 0
        assert len(res.errors) == 1
        assert "10 registros" in res.errors[0]

    def test_upsert_uses_merge_duplicates_prefer_header(self) -> None:
        captured_headers: list[str | None] = []

        class _Transport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                if request.url.path == "/rest/v1/aportes":
                    captured_headers.append(request.headers.get("prefer"))
                    return httpx.Response(201, json={"ok": True})
                return httpx.Response(200, json=[])

        _exporter(_Transport()).export_source(
            [_person("Juan")], source_slug="demo", source_fetched_ats=["2026-06-24T15:00:00Z"]
        )
        assert captured_headers == ["resolution=merge-duplicates,return=minimal"]


# --- dry-run ----------------------------------------------------------------

class TestDryRun:
    def test_dry_run_disabled_sends_nothing(self) -> None:
        exp = StagingExporter(None, run_id="run-1")
        res = exp.export_source([_person("Juan")], source_slug="demo", source_fetched_ats=["2026-06-24T16:00:00Z"])
        assert res.sent == 0 and res.duplicates == 0 and res.errors == []

    def test_dry_run_builds_payload_without_network(self) -> None:
        # No transport, no cliente: en dry-run no se abre conexion alguna.
        exp = StagingExporter(None)
        assert exp.enabled is False
        res = exp.export_source([_person("Juan", hmac="abc")], source_slug="demo", source_fetched_ats=["2026-06-24T16:00:00Z"])
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
        env = {"SUPABASE_URL": "https://x.supabase.co"}
        with patch.dict(os.environ, env, clear=True):
            with caplog.at_level("ERROR", logger="scrapers.exporters.staging_exporter"):
                assert StagingConfig.from_env() is None
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert errors
        assert "SUPABASE_SERVICE_ROLE_KEY" in errors[0].getMessage()

    def test_from_env_builds_config_when_present(self) -> None:
        env = {
            "SUPABASE_URL": "https://x.supabase.co/",
            "SUPABASE_SERVICE_ROLE_KEY": "k",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = StagingConfig.from_env()
        assert cfg is not None
        assert cfg.supabase_url == "https://x.supabase.co"  # rstrip('/')

    def test_from_env_rejects_plain_http(self, caplog: Any) -> None:
        # http:// expondría la service_role key y PII en claro: debe degradar a dry-run.
        env = {
            "SUPABASE_URL": "http://x.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "k",
        }
        with patch.dict(os.environ, env, clear=True):
            with caplog.at_level("ERROR", logger="scrapers.exporters.staging_exporter"):
                assert StagingConfig.from_env() is None
        assert any("https" in r.getMessage() for r in caplog.records if r.levelname == "ERROR")


# --- ciclo de vida ----------------------------------------------------------

class TestLifecycle:
    def test_does_not_close_injected_client(self) -> None:
        t = _RecordingTransport()
        client = httpx.Client(base_url="https://staging.test", transport=t)
        exp = StagingExporter(
            StagingConfig(supabase_url="https://staging.test", supabase_service_role_key="k"),
            client=client,
        )
        exp.close()
        # El cliente inyectado sigue usable (no fue cerrado por el exporter).
        resp = client.get("/rest/v1/source_watermarks", params={"slug": "eq.demo"})
        assert resp.status_code == 200
        client.close()
