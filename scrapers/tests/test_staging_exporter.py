"""
scrapers/tests/test_staging_exporter.py
=========================================
Tests del StagingExporter, 100% offline.

Ningun test hace red real: el httpx.Client se construye con un
``_RecordingTransport`` (subclase de httpx.BaseTransport) inyectado via el
parametro ``client`` del constructor. El transport responde a /rest/v1/aportes
y al watermark en /rest/v1/sources (GET select=watermark_at + PATCH) y registra
los bodies para los asserts.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from scrapers.dedup import specs
from scrapers.exporters.staging_exporter import (
    ExportResult,
    StagingConfig,
    StagingExporter,
    _apply_safety_margin,
)

_EVENT_ID = "8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a"


# artifact_id (FK NOT NULL -> raw_artifacts). El pipeline lo stampa como
# _artifact_id tras registrar la pagina en Bronze; los helpers lo incluyen para
# reflejar el flujo real. Es meta-campo (_-prefijo): no entra en raw_json ni
# altera content_hash / external_id.
_ARTIFACT_UUID = "c1d2e3f4-a5b6-7890-cdef-1234567890ab"

def _source_id_from_url(url: httpx.URL) -> str | None:
    """Extrae X de un filtro PostgREST ``source_id=eq.X`` en la query."""
    value = url.params.get("source_id")
    if value and value.startswith("eq."):
        return value[len("eq.") :]
    return None


class _RecordingTransport(httpx.BaseTransport):
    """Captura POSTs a /rest/v1/aportes y el watermark (PATCH a /rest/v1/sources).

    El watermark vive en ``sources.watermark_at`` (ya no en una tabla aparte):
    - lectura: GET /rest/v1/sources?source_id=eq.X&select=watermark_at
    - escritura: PATCH /rest/v1/sources?source_id=eq.X {"watermark_at": ...}
    Cada PATCH se registra en ``watermark_posts`` con ``source_id`` (del filtro de
    la URL) + el body, para que los asserts verifiquen ambos.
    """

    def __init__(self, aportes_status: int = 201, watermark_at: str | None = None) -> None:
        self.aportes_status = aportes_status
        self.persisted_watermark = watermark_at
        self.batch_posts: list[list[dict[str, Any]]] = []
        self.watermark_posts: list[dict[str, Any]] = []
        self.watermark_gets: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/rest/v1/aportes":
            body = json.loads(request.content)
            if isinstance(body, list):
                self.batch_posts.append(body)
            else:
                self.batch_posts.append([body])
            return httpx.Response(self.aportes_status, json={})
        if path == "/rest/v1/sources":
            if request.method == "GET":
                self.watermark_gets.append(str(request.url))
                return httpx.Response(200, json=[{"watermark_at": self.persisted_watermark}])
            if request.method == "PATCH":
                body = json.loads(request.content)
                self.watermark_posts.append(
                    {"source_id": _source_id_from_url(request.url), **body}
                )
                # return=representation: 200 con la fila actualizada (array no vacio).
                return httpx.Response(200, json=[body])
            return httpx.Response(404)
        return httpx.Response(404)


_TEST_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2NyYXBlcl9pbmdlc3QifQ.test"

def _exporter(transport: httpx.BaseTransport) -> StagingExporter:
    cfg = StagingConfig(supabase_url="https://project.supabase.co", publishable_key="k", ingest_jwt=_TEST_JWT)
    client = httpx.Client(base_url="https://project.supabase.co", transport=transport)
    return StagingExporter(cfg, client=client, run_id="run-1")


def _person(name: str, hmac: str | None = None, det: str | None = "detid123") -> dict[str, Any]:
    return {
        "_entity_type": "Person",
        "_artifact_id": _ARTIFACT_UUID,
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
        "_artifact_id": _ARTIFACT_UUID,
        "event_type": "earthquake",
        "location_text": "Ciudad Demo, Estado Demo",
        "date_iso": "2026-06-24T14:32:00Z",
        "description": "Sismo demo reportado",
        "fuente": "x",
    }


def _acopio() -> dict[str, Any]:
    return {
        "_entity_type": "AcopioCenter",
        "_artifact_id": _ARTIFACT_UUID,
        "name": "Centro de Acopio Demo",
        "event_id": _EVENT_ID,
        "location_text": "Ciudad Demo, Estado Demo",
        "fuente": "x",
    }


# --- payload ----------------------------------------------------------------


class TestPayload:
    def _export_one(self, rec: dict[str, Any]) -> dict[str, Any]:
        t = _RecordingTransport()
        _exporter(t).export_source([rec], source_id="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        return t.batch_posts[0][0]

    def test_payload_has_all_required_keys(self) -> None:
        body = self._export_one(_person("Juan"))
        # aportes canonico (issue #256): artifact_id presente, sin run_id/scraper_id.
        always_present = {
            "entity_type", "external_id", "dedup_version", "block_keys",
            "content_hash", "source_id", "artifact_id", "raw_json",
        }
        assert always_present.issubset(body.keys())

    def test_data_strips_internal_keys(self) -> None:
        body = self._export_one(_person("Juan"))
        data = body["raw_json"]
        assert all(not k.startswith("_") for k in data)
        assert "full_name" in data

    def test_entity_type_is_slug(self) -> None:
        body = self._export_one(_person("Juan"))
        assert body["entity_type"] == "person"

    def test_artifact_id_emitted_from_meta_field(self) -> None:
        body = self._export_one(_person("Juan"))
        assert body["artifact_id"] == _ARTIFACT_UUID

    def test_legacy_provenance_keys_dropped(self) -> None:
        # issue #256: la corrida/URL viven en raw_artifacts (via artifact_id),
        # ya no en el aporte. Estas claves NO deben aparecer en el payload.
        body = self._export_one(_person("Juan"))
        for legacy in ("run_id", "scraper_id", "source_url", "parser_version"):
            assert legacy not in body

    def test_artifact_id_not_leaked_into_raw_json(self) -> None:
        body = self._export_one(_person("Juan"))
        assert "_artifact_id" not in body["raw_json"]
        assert "artifact_id" not in body["raw_json"]

    def test_missing_artifact_id_fails_closed_when_enabled(self) -> None:
        # Sin _artifact_id, un exporter enabled NO envia el registro (fail-closed:
        # no puede existir un aporte sin su raw_artifact). El registro cuenta como
        # error y el watermark no avanza (sent==0).
        rec = _person("Juan")
        del rec["_artifact_id"]
        t = _RecordingTransport()
        res = _exporter(t).export_source(
            [rec], source_id="demo", source_fetched_ats=["2026-06-24T16:00:00Z"]
        )
        assert res.sent == 0
        assert res.errors
        assert any("artifact_id" in e for e in res.errors)
        assert t.batch_posts == []
        assert t.watermark_posts == []

    def test_dedup_version_person(self) -> None:
        body = self._export_one(_person("Juan"))
        assert body["dedup_version"] == "person-detid-v1"

    def test_content_hash_has_64_hexchars(self) -> None:
        body = self._export_one(_person("Juan"))
        assert re.fullmatch(r"[0-9a-f]{64}", body["content_hash"])

    def test_dedup_hash_absent_when_no_deterministic_id(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source(
            [_person("Juan", det=None)], source_id="demo", source_fetched_ats=["2026-06-24T15:00:00Z"]
        )
        body = t.batch_posts[0][0]
        assert "dedup_hash" not in body

    def test_entity_type_acopio_uses_acopio_slug(self) -> None:
        body = self._export_one(_acopio())
        assert body["entity_type"] == "acopio"


class TestSharedFingerprint:
    """Issue #255: external_id es por-registro-de-fuente para TODO tipo.

    El fingerprint v1 (Event/AcopioCenter) y el deterministic_id (Person)
    siguen viajando en dedup_hash para linkear en gold, pero ya NO son la
    identidad del aporte: external_id nunca vuelve a ser el fingerprint.
    """

    def _export_and_get(self, rec: dict[str, Any]) -> dict[str, Any]:
        t = _RecordingTransport()
        _exporter(t).export_source([rec], source_id="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        return t.batch_posts[0][0]

    def test_event_dedup_hash_is_fingerprint_v1(self) -> None:
        rec = _event()
        body = self._export_and_get(rec)
        assert body["dedup_hash"] == specs.event_dedup_key(rec)

    def test_event_external_id_is_not_the_fingerprint(self) -> None:
        rec = _event()
        body = self._export_and_get(rec)
        assert body["external_id"] != specs.event_dedup_key(rec)
        assert body["external_id"] != body["dedup_hash"]

    def test_acopio_dedup_hash_is_fingerprint_v1(self) -> None:
        rec = _acopio()
        body = self._export_and_get(rec)
        assert body["dedup_hash"] == specs.acopio_dedup_key(rec)

    def test_acopio_external_id_is_not_the_fingerprint(self) -> None:
        rec = _acopio()
        body = self._export_and_get(rec)
        assert body["external_id"] != specs.acopio_dedup_key(rec)
        assert body["external_id"] != body["dedup_hash"]

    def test_external_id_is_64_hexchars_for_all_types(self) -> None:
        for rec in (_person("Juan"), _event(), _acopio()):
            body = self._export_and_get(rec)
            assert re.fullmatch(r"[0-9a-f]{64}", body["external_id"])


# --- silver nunca colapsa (issue #255) --------------------------------------


class TestSilverNeverCollapses:
    """La identidad del aporte es el registro-fuente, nunca su contenido.

    Dos registros DISTINTOS de una misma fuente que comparten cedula,
    fingerprint o nombre deben producir DOS aportes (dos external_id), no uno.
    Las senales de dedup siguen en block_keys/dedup_hash para linkear en gold.
    """

    def _export(self, recs: list[dict[str, Any]], slug: str = "demo") -> list[dict[str, Any]]:
        t = _RecordingTransport()
        _exporter(t).export_source(recs, source_id=slug, source_fetched_ats=["2026-06-24T15:00:00Z"])
        return [payload for batch in t.batch_posts for payload in batch]

    def test_two_persons_same_cedula_distinct_records_two_aportes(self) -> None:
        # Misma cedula_hmac (mismo signal fuerte de fingerprint) pero distinto
        # contenido, sin source_record_id nativo: antes colapsaban a un aporte,
        # ahora son dos. La cedula sigue en block_keys para linkearlos en gold.
        a = _person("Juan Perez", hmac="ced-abc", det=None)
        b = _person("Juana Perez", hmac="ced-abc", det=None)
        bodies = self._export([a, b])
        assert len({body["external_id"] for body in bodies}) == 2
        for body in bodies:
            assert any(k.startswith(f"ced:{_EVENT_ID}:ced-abc") for k in body["block_keys"])

    def test_two_persons_same_deterministic_id_distinct_records_two_aportes(self) -> None:
        # Mismo deterministic_id via distinto _source_record_id de la misma fuente.
        a = _person("Juan", det="same-det")
        a["_source_record_id"] = "rec-1"
        b = _person("Juan", det="same-det")
        b["_source_record_id"] = "rec-2"
        bodies = self._export([a, b])
        assert len({body["external_id"] for body in bodies}) == 2
        # El deterministic_id compartido sigue en dedup_hash para el linkeo.
        assert bodies[0]["dedup_hash"] == bodies[1]["dedup_hash"] == "same-det"

    def test_two_events_same_fingerprint_distinct_content_two_aportes(self) -> None:
        # Mismo event_type/location/hora (mismo fingerprint, que excluye
        # description) pero description distinta: dos aportes, un dedup_hash.
        a = _event()
        b = _event()
        b["description"] = "Otra descripcion del mismo sismo"
        bodies = self._export([a, b])
        assert len({body["external_id"] for body in bodies}) == 2
        assert bodies[0]["dedup_hash"] == bodies[1]["dedup_hash"]

    def test_two_acopios_same_fingerprint_distinct_content_two_aportes(self) -> None:
        # Mismo name/location/event (mismo fingerprint) pero needs distintos.
        a = _acopio()
        b = _acopio()
        b["needs"] = ["agua", "medicinas"]
        bodies = self._export([a, b])
        assert len({body["external_id"] for body in bodies}) == 2
        assert bodies[0]["dedup_hash"] == bodies[1]["dedup_hash"]


# --- idempotencia -----------------------------------------------------------


class TestIdempotency:
    def test_idempotent_external_id_same_across_runs(self) -> None:
        t1, t2 = _RecordingTransport(), _RecordingTransport()
        _exporter(t1).export_source([_person("Juan")], source_id="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        _exporter(t2).export_source([_person("Juan")], source_id="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert t1.batch_posts[0][0]["external_id"] == t2.batch_posts[0][0]["external_id"]

    def test_idempotent_event_external_id_same_across_runs(self) -> None:
        t1, t2 = _RecordingTransport(), _RecordingTransport()
        _exporter(t1).export_source([_event()], source_id="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        _exporter(t2).export_source([_event()], source_id="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert t1.batch_posts[0][0]["external_id"] == t2.batch_posts[0][0]["external_id"]


# --- source_record_id -------------------------------------------------------


class TestSourceRecordId:
    def _export_one(self, rec: dict[str, Any], slug: str = "encuentralos_tecnosoft") -> dict[str, Any]:
        t = _RecordingTransport()
        _exporter(t).export_source([rec], source_id=slug, source_fetched_ats=["2026-06-24T15:00:00Z"])
        return t.batch_posts[0][0]

    def test_source_record_id_used_as_external_id_base(self) -> None:
        import hashlib
        native_id = "892170c1-962c-4566-9331-3c98bb76c7ec"
        rec = _person("Juan Demo")
        rec["_source_record_id"] = native_id
        body = self._export_one(rec, slug="encuentralos_tecnosoft")
        seed = f"person|encuentralos_tecnosoft|{native_id}"
        expected = hashlib.sha256(seed.encode()).hexdigest()
        assert body["external_id"] == expected

    def test_source_record_id_overrides_deterministic_id(self) -> None:
        """_source_record_id tiene prioridad sobre deterministic_id."""
        rec1 = _person("Juan Demo", det="same_det")
        rec1["_source_record_id"] = "uuid-000001"
        rec2 = _person("Juan Demo", det="same_det")
        rec2["_source_record_id"] = "uuid-000002"
        body1 = self._export_one(rec1)
        body2 = self._export_one(rec2)
        assert body1["external_id"] != body2["external_id"]

    def test_source_record_id_stored_in_payload(self) -> None:
        """_source_record_id se persiste en la columna source_record_id."""
        rec = _person("Juan Demo")
        rec["_source_record_id"] = "test-uuid-999"
        body = self._export_one(rec)
        assert body.get("source_record_id") == "test-uuid-999"

    def test_without_source_record_id_falls_back_to_content_hash(self) -> None:
        # Sin _source_record_id nativo, external_id se ancla al content_hash del
        # registro (no al deterministic_id): dos registros con el mismo
        # deterministic_id pero distinto contenido no colapsan.
        import hashlib

        rec = _person("Juan Demo", det="detid-fallback")
        body = self._export_one(rec, slug="demo")
        assert body["external_id"] != "detid-fallback"
        expected = hashlib.sha256(
            f"person|demo|{body['content_hash']}".encode()
        ).hexdigest()
        assert body["external_id"] == expected

    def test_external_id_is_64_hexchars(self) -> None:
        rec = _person("Juan Demo")
        rec["_source_record_id"] = "some-native-uuid"
        body = self._export_one(rec)
        assert re.fullmatch(r"[0-9a-f]{64}", body["external_id"])


# --- block keys -------------------------------------------------------------

class TestBlockKeys:
    def test_person_with_hmac_has_ced_block_key(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source([_person("Juan", hmac="abc")], source_id="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        keys = t.batch_posts[0][0]["block_keys"]
        assert any(k.startswith(f"ced:{_EVENT_ID}:abc") for k in keys)

    def test_person_without_hmac_only_phonetic_block_key(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source([_person("Juan")], source_id="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        keys = t.batch_posts[0][0]["block_keys"]
        assert all(not k.startswith("ced:") for k in keys)
        assert any(k.startswith("phon:") for k in keys)


# --- watermark --------------------------------------------------------------


class TestWatermark:
    def test_watermark_advances_on_full_success(self) -> None:
        t = _RecordingTransport(aportes_status=201)
        _exporter(t).export_source(
            [_person("Juan")],
            source_id="demo",
            source_fetched_ats=["2026-06-24T15:00:00Z", "2026-06-24T16:00:00Z"],
        )
        assert t.watermark_posts
        assert t.watermark_posts[-1]["source_id"] == "demo"
        assert t.watermark_posts[-1]["watermark_at"] == "2026-06-24T15:55:00Z"

    def test_watermark_not_set_on_post_failure(self) -> None:
        t = _RecordingTransport(aportes_status=500)
        res = _exporter(t).export_source(
            [_person("Juan")],
            source_id="demo",
            source_fetched_ats=["2026-06-24T16:00:00Z"],
        )
        assert res.errors
        assert t.watermark_posts == []

    def test_watermark_not_set_without_fetched_ats(self) -> None:
        t = _RecordingTransport(aportes_status=201)
        _exporter(t).export_source([_person("Juan")], source_id="demo", source_fetched_ats=[])
        assert t.watermark_posts == []

    def test_watermark_zero_rows_matched_reports_error(self) -> None:
        """Un PATCH que no matchea ninguna fila (p.ej. source_id no-UUID) devuelve
        200 con un array vacio; no debe tratarse como exito silencioso."""

        class _NoRowsTransport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                if request.url.path == "/rest/v1/aportes":
                    return httpx.Response(201, json={})
                if request.url.path == "/rest/v1/sources":
                    if request.method == "PATCH":
                        # return=representation con 0 filas afectadas.
                        return httpx.Response(200, json=[])
                    return httpx.Response(200, json=[{"watermark_at": None}])
                return httpx.Response(404)

        res = _exporter(_NoRowsTransport()).export_source(
            [_person("Juan")],
            source_id="not-a-uuid",
            source_fetched_ats=["2026-06-24T16:00:00Z"],
        )
        assert any("watermark" in e for e in res.errors)

    def test_watermark_advance_is_monotonic_across_runs(self) -> None:
        t = _RecordingTransport(aportes_status=201)
        exp = _exporter(t)
        exp.export_source(
            [_person("Juan")], source_id="demo", source_fetched_ats=["2026-06-24T16:00:00Z"]
        )
        exp.export_source(
            [_person("Ana")], source_id="demo", source_fetched_ats=["2026-06-24T16:01:00Z"]
        )
        assert [p["watermark_at"] for p in t.watermark_posts] == [
            "2026-06-24T15:55:00Z",
            "2026-06-24T15:56:00Z",
        ]

    def test_patch_posts_watermark_at_to_sources(self) -> None:
        captured: dict[str, Any] = {}

        class _Transport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                if request.url.path == "/rest/v1/aportes":
                    return httpx.Response(201, json={})
                if request.method == "PATCH" and request.url.path == "/rest/v1/sources":
                    captured["source_id"] = _source_id_from_url(request.url)
                    captured["body"] = json.loads(request.content)
                    captured["headers"] = dict(request.headers)
                    return httpx.Response(200, json=[captured["body"]])
                return httpx.Response(404)

        _exporter(_Transport()).export_source(
            [_person("Juan")], source_id="fuente-x", source_fetched_ats=["2026-06-24T16:00:00Z"]
        )
        # El source_id va en el filtro de la URL; el body solo trae watermark_at.
        assert captured["source_id"] == "fuente-x"
        assert captured["body"] == {"watermark_at": "2026-06-24T15:55:00Z"}
        # return=representation permite verificar que la fila realmente se actualizo.
        assert "return=representation" in captured["headers"].get("prefer", "")

    def test_watermark_is_per_source_id(self) -> None:
        t = _RecordingTransport(aportes_status=201)
        exp = _exporter(t)
        exp.export_source(
            [_person("Juan")], source_id="fuente-a", source_fetched_ats=["2026-06-24T10:00:00Z"]
        )
        exp.export_source(
            [_person("Ana")], source_id="fuente-b", source_fetched_ats=["2026-06-24T20:00:00Z"]
        )
        ids_to_watermark = {p["source_id"]: p["watermark_at"] for p in t.watermark_posts}
        assert ids_to_watermark == {
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
    def test_returns_default_on_empty_response(self) -> None:
        t = _RecordingTransport()
        assert _exporter(t).get_watermark("fuente-nueva") == "1970-01-01T00:00:00Z"

    def test_returns_persisted_value(self) -> None:
        class _Transport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                if request.url.path == "/rest/v1/sources":
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
        class _Transport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, content=b"not json")

        assert _exporter(_Transport()).get_watermark("fuente-a") == "1970-01-01T00:00:00Z"

    def test_returns_default_on_non_list_json_body(self) -> None:
        class _Transport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={"watermark_at": "2026-06-20T00:00:00Z"})

        assert _exporter(_Transport()).get_watermark("fuente-a") == "1970-01-01T00:00:00Z"

    def test_raises_on_401(self) -> None:
        class _Transport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(401)

        with pytest.raises(PermissionError, match="scraper_ingest"):
            _exporter(_Transport()).get_watermark("fuente-a")

    def test_raises_on_403(self) -> None:
        class _Transport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(403)

        with pytest.raises(PermissionError, match="scraper_ingest"):
            _exporter(_Transport()).get_watermark("fuente-a")


# --- auth ---------------------------------------------------------------

class TestAuth:
    def test_uses_apikey_and_bearer_headers(self) -> None:
        cfg = StagingConfig(
            supabase_url="https://project.supabase.co",
            publishable_key="sb_publishable_test",
            ingest_jwt=_TEST_JWT,
        )
        exp = StagingExporter(cfg, run_id="run-1")
        assert exp._client is not None
        assert exp._client.headers["apikey"] == "sb_publishable_test"
        assert exp._client.headers["Authorization"] == f"Bearer {_TEST_JWT}"
        exp.close()


# --- clasificacion de respuestas --------------------------------------------

class TestResponseClassification:
    def test_201_counts_as_sent(self) -> None:
        t = _RecordingTransport(aportes_status=201)
        res = _exporter(t).export_source([_person("Juan")], source_id="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert res.sent == 1 and res.duplicates == 0 and res.errors == []

    def test_500_counts_as_error_without_raising(self) -> None:
        t = _RecordingTransport(aportes_status=500)
        res = _exporter(t).export_source([_person("Juan")], source_id="demo", source_fetched_ats=["2026-06-24T15:00:00Z"])
        assert len(res.errors) >= 1 and res.sent == 0


# --- retry del POST ---------------------------------------------------------

class _FlakyTransport(httpx.BaseTransport):
    def __init__(self, aportes_sequence: list[int]) -> None:
        self.aportes_sequence = aportes_sequence
        self.attempts = 0
        self.watermark_posts: list[dict[str, Any]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/rest/v1/aportes":
            idx = min(self.attempts, len(self.aportes_sequence) - 1)
            status = self.aportes_sequence[idx]
            self.attempts += 1
            return httpx.Response(status, json={})
        if path == "/rest/v1/sources":
            if request.method == "PATCH":
                body = json.loads(request.content)
                self.watermark_posts.append(
                    {"source_id": _source_id_from_url(request.url), **body}
                )
                return httpx.Response(200, json=[body])
            return httpx.Response(200, json=[{"watermark_at": None}])
        return httpx.Response(404)


class TestPostRetry:
    def test_503_then_200_ends_as_sent(self) -> None:
        t = _FlakyTransport([503, 200])
        cfg = StagingConfig(supabase_url="https://project.supabase.co", publishable_key="k", ingest_jwt=_TEST_JWT)
        client = httpx.Client(base_url="https://project.supabase.co", transport=t)
        exp = StagingExporter(cfg, client=client, run_id="run-1")
        with patch("scrapers.adapters._shared.time.sleep", lambda *_: None):
            res = exp.export_source(
                [_person("Juan")], source_id="demo", source_fetched_ats=["2026-06-24T15:00:00Z"]
            )
        assert res.sent == 1
        assert res.errors == []
        assert t.attempts == 2

    def test_persistent_503_ends_as_error(self) -> None:
        t = _FlakyTransport([503])
        cfg = StagingConfig(supabase_url="https://project.supabase.co", publishable_key="k", ingest_jwt=_TEST_JWT)
        client = httpx.Client(base_url="https://project.supabase.co", transport=t)
        exp = StagingExporter(cfg, client=client, run_id="run-1")
        with patch("scrapers.adapters._shared.time.sleep", lambda *_: None):
            res = exp.export_source(
                [_person("Juan")], source_id="demo", source_fetched_ats=["2026-06-24T15:00:00Z"]
            )
        assert res.sent == 0
        assert res.errors
        assert t.watermark_posts == []


# --- source_errors bloquean el watermark (C6) -------------------------------

class TestSourceErrorsWatermark:
    def test_source_errors_block_watermark_advance(self) -> None:
        t = _RecordingTransport(aportes_status=201)
        res = _exporter(t).export_source(
            [_person("Juan")],
            source_id="demo",
            source_fetched_ats=["2026-06-24T16:00:00Z"],
            source_errors=["menor descartado por proteccion fail-closed"],
        )
        assert res.sent == 1
        assert t.watermark_posts == []

    def test_empty_source_errors_allow_watermark_advance(self) -> None:
        t = _RecordingTransport(aportes_status=201)
        _exporter(t).export_source(
            [_person("Juan")],
            source_id="demo",
            source_fetched_ats=["2026-06-24T16:00:00Z"],
            source_errors=[],
        )
        assert t.watermark_posts
        assert t.watermark_posts[-1]["watermark_at"] == "2026-06-24T15:55:00Z"


# --- batch export ------------------------------------------------------------

class TestBatchExport:
    def test_chunks_records_into_batches(self) -> None:
        records = [_person(f"P{i}", det=f"det{i}") for i in range(7)]
        t = _RecordingTransport()
        _exporter(t).export_source(
            records,
            source_id="demo",
            source_fetched_ats=["2026-06-24T15:00:00Z"],
            batch_size=3,
        )
        assert len(t.batch_posts) == 3  # ceil(7/3) = 3 requests
        assert len(t.batch_posts[0]) == 3
        assert len(t.batch_posts[1]) == 3
        assert len(t.batch_posts[2]) == 1

    def test_contadores_correctos(self) -> None:
        records = [_person(f"P{i}", det=f"det{i}") for i in range(10)]
        t = _RecordingTransport()
        res = _exporter(t).export_source(
            records,
            source_id="demo",
            source_fetched_ats=["2026-06-24T15:00:00Z"],
            batch_size=4,
        )
        assert res.sent == 10
        assert res.errors == []
        assert len(t.batch_posts) == 3

    def test_batch_exitoso_no_reintenta_individual(self) -> None:
        records = [_person(f"P{i}", det=f"det{i}") for i in range(3)]
        t = _RecordingTransport()  # aportes_status=201 por default
        res = _exporter(t).export_source(
            records,
            source_id="demo",
            source_fetched_ats=["2026-06-24T15:00:00Z"],
            batch_size=10,  # un solo chunk de 3
        )
        assert res.sent == 3
        assert res.errors == []
        assert len(t.batch_posts) == 1  # exactamente 1 request, no 1+3

    def test_avanza_watermark_si_todo_ok(self) -> None:
        t = _RecordingTransport()
        _exporter(t).export_source(
            [_person("Juan")],
            source_id="demo",
            source_fetched_ats=["2026-06-24T16:00:00Z"],
        )
        assert t.watermark_posts
        assert t.watermark_posts[-1]["source_id"] == "demo"
        assert t.watermark_posts[-1]["watermark_at"] == "2026-06-24T15:55:00Z"

    def test_error_http_bloquea_watermark(self) -> None:
        t = _RecordingTransport(aportes_status=500)
        res = _exporter(t).export_source(
            [_person("Juan")],
            source_id="demo",
            source_fetched_ats=["2026-06-24T16:00:00Z"],
        )
        assert res.errors
        assert t.watermark_posts == []

    def test_source_errors_bloquean_watermark(self) -> None:
        t = _RecordingTransport()
        res = _exporter(t).export_source(
            [_person("Juan")],
            source_id="demo",
            source_fetched_ats=["2026-06-24T16:00:00Z"],
            source_errors=["menor descartado"],
        )
        assert res.sent == 1
        assert t.watermark_posts == []

    def test_watermark_patch_401_registra_error_con_detalle(self) -> None:
        class _Transport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                if request.url.path == "/rest/v1/aportes":
                    return httpx.Response(201, json={})
                if request.url.path == "/rest/v1/sources":
                    if request.method == "PATCH":
                        return httpx.Response(401)
                    return httpx.Response(200, json=[{"watermark_at": None}])
                return httpx.Response(404)

        res = _exporter(_Transport()).export_source(
            [_person("Juan")],
            source_id="demo",
            source_fetched_ats=["2026-06-24T16:00:00Z"],
        )
        assert res.sent == 1
        assert any("401" in e for e in res.errors)

    def test_watermark_patch_filters_by_source_id(self) -> None:
        captured_query: list[str] = []

        class _Transport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                if request.url.path == "/rest/v1/aportes":
                    return httpx.Response(201, json={})
                if request.url.path == "/rest/v1/sources":
                    if request.method == "PATCH":
                        captured_query.append(str(request.url.query))
                        return httpx.Response(200, json=[{"watermark_at": None}])
                    return httpx.Response(200, json=[{"watermark_at": None}])
                return httpx.Response(404)

        _exporter(_Transport()).export_source(
            [_person("Juan")],
            source_id="demo",
            source_fetched_ats=["2026-06-24T16:00:00Z"],
        )
        # El watermark ahora se escribe con PATCH filtrando por source_id (UUID),
        # ya no con un upsert on_conflict=slug sobre source_watermarks.
        assert captured_query and "source_id=eq.demo" in captured_query[0]

    def test_dry_run_no_envia_nada(self) -> None:
        exp = StagingExporter(None, run_id="run-1")
        res = exp.export_source(
            [_person("Juan")],
            source_id="demo",
            source_fetched_ats=["2026-06-24T16:00:00Z"],
        )
        assert res.sent == 0
        assert res.errors == []


# --- fallback batch → individual --------------------------------------------

class TestBatchFallback:
    """Un batch rechazado (4xx) reintenta cada registro individualmente."""

    def test_batch_400_retries_individually_valid_records_counted(self) -> None:
        """400 en batch → fallback individual; registros válidos cuentan como sent."""

        class _Transport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                if request.url.path == "/rest/v1/sources":
                    if request.method == "PATCH":
                        return httpx.Response(200, json=[{"watermark_at": None}])
                    return httpx.Response(200, json=[{"watermark_at": None}])
                if request.url.path == "/rest/v1/aportes":
                    body = json.loads(request.content)
                    if len(body) > 1:
                        return httpx.Response(400, json={"message": "constraint violation"})
                    return httpx.Response(201, json={})
                return httpx.Response(404)

        records = [_person(f"P{i}", det=f"det{i}") for i in range(5)]
        res = _exporter(_Transport()).export_source(
            records, source_id="demo",
            source_fetched_ats=["2026-06-24T15:00:00Z"], batch_size=10,
        )
        assert res.sent == 5
        assert res.errors == []

    def test_batch_400_one_bad_record_others_sent(self) -> None:
        """Un registro inválido genera error pero no bloquea los demás del batch."""
        import hashlib

        # external_id ya no es el deterministic_id; se ancla al registro-fuente.
        # El registro malo trae un _source_record_id, asi su external_id es
        # predecible y el transport falso lo puede rechazar puntualmente.
        bad_external_id = hashlib.sha256(b"person|demo|rec-bad").hexdigest()

        class _Transport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                if request.url.path == "/rest/v1/sources":
                    if request.method == "PATCH":
                        return httpx.Response(200, json=[{"watermark_at": None}])
                    return httpx.Response(200, json=[{"watermark_at": None}])
                if request.url.path == "/rest/v1/aportes":
                    body = json.loads(request.content)
                    if len(body) > 1:
                        return httpx.Response(400, json={"message": "batch rejected"})
                    if body[0].get("external_id") == bad_external_id:
                        return httpx.Response(400, json={"message": "bad record"})
                    return httpx.Response(201, json={})
                return httpx.Response(404)

        bad = _person("Bad", det="det-bad")
        bad["_source_record_id"] = "rec-bad"
        records = [
            _person("Good1", det="det-ok-1"),
            bad,
            _person("Good2", det="det-ok-2"),
        ]
        res = _exporter(_Transport()).export_source(
            records, source_id="demo",
            source_fetched_ats=["2026-06-24T15:00:00Z"], batch_size=10,
        )
        assert res.sent == 2
        assert len(res.errors) == 1
        assert bad_external_id in res.errors[0]


# --- dry-run ----------------------------------------------------------------

class TestDryRun:
    def test_dry_run_disabled_sends_nothing(self) -> None:
        exp = StagingExporter(None, run_id="run-1")
        res = exp.export_source([_person("Juan")], source_id="demo", source_fetched_ats=["2026-06-24T16:00:00Z"])
        assert res.sent == 0 and res.duplicates == 0 and res.errors == []

    def test_dry_run_builds_payload_without_network(self) -> None:
        exp = StagingExporter(None)
        assert exp.enabled is False
        res = exp.export_source([_person("Juan", hmac="abc")], source_id="demo", source_fetched_ats=["2026-06-24T16:00:00Z"])
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
        env = {"SUPABASE_URL": "https://project.supabase.co"}
        with patch.dict(os.environ, env, clear=True):
            with caplog.at_level("ERROR", logger="scrapers.exporters.staging_exporter"):
                assert StagingConfig.from_env() is None
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert errors
        assert "SUPABASE_PUBLISHABLE_KEY" in errors[0].getMessage()

    def test_from_env_builds_config_when_present(self) -> None:
        env = {
            "SUPABASE_URL": "https://project.supabase.co",
            "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_test",
            "SUPABASE_INGEST_JWT": _TEST_JWT,
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = StagingConfig.from_env()
        assert cfg is not None
        assert cfg.supabase_url == "https://project.supabase.co"
        assert cfg.publishable_key == "sb_publishable_test"
        assert cfg.ingest_jwt == _TEST_JWT

    def test_from_env_uses_consolidation_jwt_when_present(self) -> None:
        consolidation_jwt = "eyJhbGciOiJIUzI1NiJ9.consolidation"
        env = {
            "SUPABASE_URL": "https://project.supabase.co",
            "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_test",
            "SUPABASE_CONSOLIDATION_JWT": consolidation_jwt,
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = StagingConfig.from_env()
        assert cfg is not None
        assert cfg.ingest_jwt == consolidation_jwt

    def test_from_env_consolidation_jwt_takes_priority_over_ingest(self) -> None:
        consolidation_jwt = "eyJhbGciOiJIUzI1NiJ9.consolidation"
        env = {
            "SUPABASE_URL": "https://project.supabase.co",
            "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_test",
            "SUPABASE_CONSOLIDATION_JWT": consolidation_jwt,
            "SUPABASE_INGEST_JWT": _TEST_JWT,
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = StagingConfig.from_env()
        assert cfg is not None
        assert cfg.ingest_jwt == consolidation_jwt

    def test_from_env_rejects_plain_http(self, caplog: Any) -> None:
        env = {
            "SUPABASE_URL": "http://project.supabase.co",
            "SUPABASE_PUBLISHABLE_KEY": "k",
            "SUPABASE_INGEST_JWT": _TEST_JWT,
        }
        with patch.dict(os.environ, env, clear=True):
            with caplog.at_level("ERROR", logger="scrapers.exporters.staging_exporter"):
                assert StagingConfig.from_env() is None
        assert any("https" in r.getMessage() for r in caplog.records if r.levelname == "ERROR")

# --- paralelismo ----------------------------------------------------------
class TestConcurrentExport:
    """max_concurrent_posts > 1 activa el ThreadPoolExecutor."""

    def test_multiples_batches_paralelo_cuentan_todos(self) -> None:
        records = [_person(f"P{i}", det=f"det{i}") for i in range(20)]
        t = _RecordingTransport()
        res = _exporter(t).export_source(
            records, source_id="demo",
            source_fetched_ats=["2026-06-24T15:00:00Z"],
            batch_size=5, max_concurrent_posts=4,
        )
        assert res.sent == 20
        assert res.errors == []
        assert len(t.batch_posts) == 4  # ceil(20/5)

    def test_max_concurrent_posts_ausente_es_secuencial(self) -> None:
        """Sin max_concurrent_posts (o =1), mismo comportamiento que antes."""
        records = [_person(f"P{i}", det=f"det{i}") for i in range(5)]
        t = _RecordingTransport()
        res = _exporter(t).export_source(
            records, source_id="demo",
            source_fetched_ats=["2026-06-24T15:00:00Z"], batch_size=5,
        )
        assert res.sent == 5

# --- watermark parcial (#217) -----------------------------------------------


class _PartialInsertTransport(httpx.BaseTransport):
    """Devuelve 201 para el primer POST a /aportes y 500 para los siguientes."""

    def __init__(self) -> None:
        self._aportes_calls = 0
        self.watermark_posts: list[dict[str, Any]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/v1/sources":
            if request.method == "PATCH":
                body = json.loads(request.content)
                self.watermark_posts.append(body)
                return httpx.Response(200, json=[body])
            return httpx.Response(200, json=[{"watermark_at": None}])
        if request.url.path == "/rest/v1/aportes":
            self._aportes_calls += 1
            return httpx.Response(201 if self._aportes_calls == 1 else 500, json={})
        return httpx.Response(404)


class TestPartialWatermark:
    """Cobertura del avance parcial de watermark (issue #217)."""

    def test_watermark_avanza_con_insert_parcial(self) -> None:
        """sent>0 aunque haya errores de insert → watermark avanza."""
        t = _PartialInsertTransport()
        res = _exporter(t).export_source(
            [_person("Juan", det="d1"), _person("Ana", det="d2")],
            source_id="demo",
            source_fetched_ats=["2026-06-24T16:00:00Z"],
            batch_size=1,
        )
        assert res.sent == 1
        assert res.errors
        assert t.watermark_posts, "watermark debe avanzar cuando al menos un registro fue enviado"
        assert t.watermark_posts[-1]["watermark_at"] == "2026-06-24T15:55:00Z"

    def test_watermark_no_avanza_si_sent_cero(self) -> None:
        """sent==0 → watermark no avanza aunque haya fetched_ats."""
        t = _RecordingTransport(aportes_status=500)
        res = _exporter(t).export_source(
            [_person("Juan", det="d1")],
            source_id="demo",
            source_fetched_ats=["2026-06-24T16:00:00Z"],
        )
        assert res.sent == 0
        assert t.watermark_posts == []

    def test_watermark_no_avanza_si_source_errors_con_sent_positivo(self) -> None:
        """source_errors bloquea watermark incluso cuando sent>0."""
        t = _RecordingTransport(aportes_status=201)
        res = _exporter(t).export_source(
            [_person("Juan", det="d1")],
            source_id="demo",
            source_fetched_ats=["2026-06-24T16:00:00Z"],
            source_errors=["proteccion de menores fail-closed"],
        )
        assert res.sent == 1
        assert t.watermark_posts == []

    def test_fallback_individual_parcial_avanza_watermark(self) -> None:
        """Bulk 400 → fallback individual → 1 ok, 1 error → watermark avanza."""

        class _BulkFail(httpx.BaseTransport):
            def __init__(self) -> None:
                self._individual_calls = 0
                self.watermark_posts: list[dict[str, Any]] = []

            def handle_request(self, request: httpx.Request) -> httpx.Response:
                if request.url.path == "/rest/v1/sources":
                    if request.method == "PATCH":
                        body = json.loads(request.content)
                        self.watermark_posts.append(body)
                        return httpx.Response(200, json=[body])
                    return httpx.Response(200, json=[{"watermark_at": None}])
                if request.url.path == "/rest/v1/aportes":
                    body = json.loads(request.content)
                    if len(body) > 1:
                        return httpx.Response(400, json={"message": "bulk rejected"})
                    self._individual_calls += 1
                    return httpx.Response(201 if self._individual_calls == 1 else 500, json={})
                return httpx.Response(404)

        t = _BulkFail()
        res = _exporter(t).export_source(
            [_person("P1", det="d1"), _person("P2", det="d2")],
            source_id="demo",
            source_fetched_ats=["2026-06-24T16:00:00Z"],
            batch_size=10,
        )
        assert res.sent == 1
        assert len(res.errors) == 1
        assert t.watermark_posts, "watermark debe avanzar: bulk 400 + fallback parcial"


# --- ciclo de vida ----------------------------------------------------------

class TestLifecycle:
    def test_does_not_close_injected_client(self) -> None:
        t = _RecordingTransport()
        client = httpx.Client(base_url="https://project.supabase.co", transport=t)
        exp = StagingExporter(
            StagingConfig(supabase_url="https://project.supabase.co", publishable_key="k", ingest_jwt=_TEST_JWT),
            client=client,
        )
        exp.close()
        # El cliente inyectado sigue usable (no fue cerrado por el exporter).
        resp = client.get("/rest/v1/sources?select=watermark_at")
        assert resp.status_code == 200
        client.close()
