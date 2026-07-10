"""
scrapers/tests/test_materializer.py
===================================
Tests del SilverMaterializer (silver 1:1: aportes -> persons/acopio_centers +
seed del catalogo events), 100% offline.

Ningun test hace red real: el httpx.Client se construye con un ``_FakeSupabase``
(subclase de httpx.BaseTransport) inyectado via el parametro ``client`` del
constructor. El transport simula PostgREST con estado en memoria:

  - GET  /rest/v1/aportes         -> pagina los aportes configurados (limit/offset).
  - POST /rest/v1/events          -> upsert idempotente por event_id (DO NOTHING).
  - POST /rest/v1/persons         -> upsert idempotente por person_record_id.
  - POST /rest/v1/acopio_centers  -> upsert idempotente por acopio_id.

Invariantes que se prueban (issue #257):
  - Proyeccion 1:1: exactamente una fila tipada por aporte, PK = aportes.id.
  - El catalogo ``events`` se siembra ANTES de proyectar (FK persons/acopio).
  - Los aportes de tipo ``event`` NO se proyectan (Fase 1: catalogo compartido).
  - Re-correr es idempotente: sin filas duplicadas ni churned.
  - PII (full_name / cedula) NUNCA se loguea.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import httpx

from scrapers.exporters.staging_exporter import StagingConfig
from scrapers.jobs.materializer import SilverMaterializer

_EVENT_ID = "8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a"
_TEST_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2NyYXBlcl9pbmdlc3QifQ.test"

# Marcadores de PII en claro: si aparecen en un log, el materializer los filtra.
_PII_NAME = "JUAN-FICTICIO-NOMBRE-COMPLETO-PII"
_PII_CEDULA = "CEDULA-V-12345678-PII-EN-CLARO"


def _person_aporte(
    aporte_id: str,
    *,
    full_name: str = _PII_NAME,
    event_id: str = _EVENT_ID,
    **overrides: Any,
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "full_name": full_name,
        "event_id": event_id,
        "cedula_hmac": "a1b2c3d4e5f6",
        "cedula_masked": "V-****5678",
        "status": "missing",
        "trust_tier": "B",
        "confidence_score": 0.42,
        "last_known_location": "Chacao, Miranda",
        "age_range": {"min": 30, "max": 40},
        "is_minor": False,
        "fuente": "demo",
    }
    raw.update(overrides)
    return {"id": aporte_id, "entity_type": "person", "raw_json": raw}


def _acopio_aporte(
    aporte_id: str, *, event_id: str = _EVENT_ID, **overrides: Any
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "name": "Refugio Ficticio",
        "event_id": event_id,
        "location_text": "Plaza Ficticia, Chacao",
        "coordinates": {"lat": 10.5, "lon": -66.9},
        "status": "active",
        "trust_tier": "C",
        "confidence_score": 0.31,
        "fuente": "demo",
    }
    raw.update(overrides)
    return {"id": aporte_id, "entity_type": "acopio", "raw_json": raw}


def _event_aporte(aporte_id: str) -> dict[str, Any]:
    return {
        "id": aporte_id,
        "entity_type": "event",
        "raw_json": {
            "event_type": "earthquake",
            "description": "Sismo ficticio de prueba",
            "event_id": _EVENT_ID,
            "fuente": "demo",
        },
    }


class _FakeSupabase(httpx.BaseTransport):
    """PostgREST en memoria: pagina aportes y hace upsert DO-NOTHING por PK.

    ``return=representation`` devuelve SOLO las filas realmente insertadas (las
    que colisionan por PK devuelven [] como haria ON CONFLICT DO NOTHING), asi
    los tests pueden medir cuantas filas nuevas se proyectaron por corrida.
    """

    def __init__(self, aportes: list[dict[str, Any]]) -> None:
        self.aportes = aportes
        # created_at sintetico y ordenable si el fixture no lo trae: el materializer
        # ahora pagina por keyset (created_at, id), no por offset.
        for i, ap in enumerate(aportes):
            ap.setdefault("created_at", f"2026-07-01T00:00:00.{i:06d}Z")
        self.events: dict[str, dict[str, Any]] = {}
        self.persons: dict[str, dict[str, Any]] = {}
        self.acopios: dict[str, dict[str, Any]] = {}
        # Bitacora ordenada de paths POSTeados, para el test de orden FK.
        self.post_order: list[str] = []
        self.aportes_gets = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/rest/v1/aportes":
            return self._get_aportes(request)
        if request.method == "POST":
            store = {
                "/rest/v1/events": (self.events, "event_id"),
                "/rest/v1/persons": (self.persons, "person_record_id"),
                "/rest/v1/acopio_centers": (self.acopios, "acopio_id"),
            }.get(path)
            if store is not None:
                self.post_order.append(path)
                return self._upsert(request, *store)
        # La tabla del cursor (silver_materialize_state) no existe por defecto:
        # 404 => el materializer se degrada a scan completo (comportamiento previo).
        return httpx.Response(404, json={"path": path})

    @staticmethod
    def _sort_key(ap: dict[str, Any]) -> tuple[str, str]:
        return (str(ap.get("created_at", "")), str(ap.get("id")))

    @staticmethod
    def _parse_keyset(or_expr: str) -> tuple[str, str]:
        # or=(created_at.gt.TS,and(created_at.eq.TS,id.gt.ID))
        ts = re.search(r"created_at\.gt\.([^,]+)", or_expr)
        cid = re.search(r"id\.gt\.([^)]+)", or_expr)
        return (ts.group(1) if ts else "", cid.group(1) if cid else "")

    def _get_aportes(self, request: httpx.Request) -> httpx.Response:
        self.aportes_gets += 1
        qs = parse_qs(urlparse(str(request.url)).query)
        limit = int(qs.get("limit", ["1000"])[0])
        rows = sorted(self.aportes, key=self._sort_key)
        or_expr = qs.get("or", [None])[0]
        if or_expr:
            cursor = self._parse_keyset(or_expr)
            rows = [ap for ap in rows if self._sort_key(ap) > cursor]
        return httpx.Response(200, json=rows[:limit])

    def _upsert(
        self, request: httpx.Request, store: dict[str, dict[str, Any]], pk: str
    ) -> httpx.Response:
        body = json.loads(request.content)
        rows = body if isinstance(body, list) else [body]
        inserted: list[dict[str, Any]] = []
        for row in rows:
            key = str(row[pk])
            if key not in store:
                store[key] = row
                inserted.append(row)
        return httpx.Response(201, json=inserted)


def _materializer(transport: httpx.BaseTransport) -> SilverMaterializer:
    cfg = StagingConfig(
        supabase_url="https://project.supabase.co",
        publishable_key="k",
        ingest_jwt=_TEST_JWT,
    )
    client = httpx.Client(base_url="https://project.supabase.co", transport=transport)
    return SilverMaterializer(cfg, client=client)


# --- proyeccion persons -----------------------------------------------------


class TestProjectPerson:
    def test_projects_one_row_pk_is_aporte_id(self) -> None:
        t = _FakeSupabase([_person_aporte("ap-1")])
        _materializer(t).materialize(event_id=_EVENT_ID)
        assert list(t.persons.keys()) == ["ap-1"]
        row = t.persons["ap-1"]
        assert row["person_record_id"] == "ap-1"
        assert row["entity_type"] == "person"

    def test_maps_fields_from_raw_json(self) -> None:
        t = _FakeSupabase([_person_aporte("ap-1")])
        _materializer(t).materialize(event_id=_EVENT_ID)
        row = t.persons["ap-1"]
        assert row["full_name"] == _PII_NAME
        assert row["event_id"] == _EVENT_ID
        assert row["cedula_hmac"] == "a1b2c3d4e5f6"
        assert row["cedula_masked"] == "V-****5678"
        assert row["status"] == "missing"
        assert row["trust_tier"] == "B"
        assert row["confidence_score"] == 0.42
        assert row["last_known_location"] == "Chacao, Miranda"
        assert row["age_range"] == {"min": 30, "max": 40}
        assert row["is_minor"] is False

    def test_event_id_comes_from_raw_json(self) -> None:
        other = "11111111-2222-3333-4444-555555555555"
        t = _FakeSupabase([_person_aporte("ap-1", event_id=other)])
        _materializer(t).materialize(event_id=_EVENT_ID)
        assert t.persons["ap-1"]["event_id"] == other

    def test_does_not_leak_internal_meta_keys(self) -> None:
        # raw_json nunca deberia llevar claves _*, pero si las lleva no se copian.
        t = _FakeSupabase([_person_aporte("ap-1", _source_record_id="x", _artifact_id="y")])
        _materializer(t).materialize(event_id=_EVENT_ID)
        row = t.persons["ap-1"]
        assert not any(k.startswith("_") for k in row)


# --- proyeccion acopio_centers ----------------------------------------------


class TestProjectAcopio:
    def test_projects_one_row_pk_is_aporte_id(self) -> None:
        t = _FakeSupabase([_acopio_aporte("ap-9")])
        _materializer(t).materialize(event_id=_EVENT_ID)
        assert list(t.acopios.keys()) == ["ap-9"]
        row = t.acopios["ap-9"]
        assert row["acopio_id"] == "ap-9"
        assert row["entity_type"] == "acopio"
        assert row["name"] == "Refugio Ficticio"
        assert row["location_text"] == "Plaza Ficticia, Chacao"
        assert row["coordinates"] == {"lat": 10.5, "lon": -66.9}
        assert row["event_id"] == _EVENT_ID


# --- catalogo events --------------------------------------------------------


class TestEventCatalog:
    def test_seeds_one_catalog_row_with_config_event_id(self) -> None:
        t = _FakeSupabase([_person_aporte("ap-1")])
        _materializer(t).materialize(event_id=_EVENT_ID)
        assert _EVENT_ID in t.events
        seeded = t.events[_EVENT_ID]
        assert seeded["event_id"] == _EVENT_ID
        # event_type es integer NOT NULL en la BD: debe ir un valor entero.
        assert isinstance(seeded["event_type"], int)

    def test_event_seeded_before_projecting(self) -> None:
        # FK: persons.event_id / acopio_centers.event_id referencian events.
        # La fila del catalogo debe existir antes de proyectar.
        t = _FakeSupabase([_person_aporte("ap-1"), _acopio_aporte("ap-2")])
        _materializer(t).materialize(event_id=_EVENT_ID)
        first_typed = next(
            i for i, p in enumerate(t.post_order)
            if p in ("/rest/v1/persons", "/rest/v1/acopio_centers")
        )
        first_event = t.post_order.index("/rest/v1/events")
        assert first_event < first_typed

    def test_event_aportes_are_not_projected(self) -> None:
        # Fase 1: ningun parser emite Event; el aporte 'event' no proyecta fila
        # por-aporte (events es catalogo compartido, no proyeccion 1:1).
        t = _FakeSupabase([_event_aporte("ap-ev"), _person_aporte("ap-1")])
        _materializer(t).materialize(event_id=_EVENT_ID)
        assert "ap-ev" not in t.persons
        assert "ap-ev" not in t.acopios
        # Solo la fila de catalogo sembrada existe, no una por el aporte 'event'.
        assert list(t.events.keys()) == [_EVENT_ID]


# --- idempotencia -----------------------------------------------------------


class TestIdempotency:
    def test_rerun_no_duplicate_rows(self) -> None:
        aportes = [_person_aporte("ap-1"), _acopio_aporte("ap-2")]
        t = _FakeSupabase(aportes)
        m = _materializer(t)
        r1 = m.materialize(event_id=_EVENT_ID)
        r2 = m.materialize(event_id=_EVENT_ID)
        assert len(t.persons) == 1
        assert len(t.acopios) == 1
        assert len(t.events) == 1
        # Primera corrida proyecta 1+1; la segunda no proyecta filas nuevas.
        assert r1.persons_projected == 1
        assert r1.acopio_projected == 1
        assert r2.persons_projected == 0
        assert r2.acopio_projected == 0

    def test_result_counts_reflect_projection(self) -> None:
        t = _FakeSupabase([_person_aporte("ap-1"), _person_aporte("ap-2"), _acopio_aporte("ap-3")])
        r = _materializer(t).materialize(event_id=_EVENT_ID)
        assert r.persons_projected == 2
        assert r.acopio_projected == 1
        assert r.events_skipped == 0


# --- resiliencia: una fila mala no tumba el lote entero ----------------------


class TestPoisonRowFallback:
    def test_bad_row_isolated_good_rows_still_projected(self) -> None:
        # PostgREST ejecuta un POST batch como un unico INSERT: una sola fila que
        # viola una constraint (p.ej. un enum) aborta todo el lote. El materializer
        # debe reintentar fila a fila y salvar las buenas.
        class _RejectsBadPerson(_FakeSupabase):
            def _upsert(self, request, store, pk):  # type: ignore[no-untyped-def]
                if request.url.path == "/rest/v1/persons":
                    body = json.loads(request.content)
                    if any(r["person_record_id"] == "ap-bad" for r in body):
                        return httpx.Response(400, json={"message": "invalid enum"})
                return super()._upsert(request, store, pk)

        t = _RejectsBadPerson([_person_aporte("ap-good"), _person_aporte("ap-bad")])
        r = _materializer(t).materialize(event_id=_EVENT_ID)
        assert "ap-good" in t.persons
        assert "ap-bad" not in t.persons
        assert r.persons_projected == 1
        assert any("ap-bad" in e for e in r.errors)

    def test_event_id_falls_back_to_config_when_missing_in_raw(self) -> None:
        aporte = _person_aporte("ap-1")
        del aporte["raw_json"]["event_id"]
        t = _FakeSupabase([aporte])
        _materializer(t).materialize(event_id=_EVENT_ID)
        assert t.persons["ap-1"]["event_id"] == _EVENT_ID


# --- paginacion -------------------------------------------------------------


class TestPagination:
    def test_projects_across_multiple_pages(self) -> None:
        aportes = [_person_aporte(f"ap-{i}") for i in range(250)]
        t = _FakeSupabase(aportes)
        _materializer(t).materialize(event_id=_EVENT_ID, batch_size=100)
        assert len(t.persons) == 250
        assert t.aportes_gets >= 3  # 100 + 100 + 50 -> al menos 3 GETs


# --- PII: nunca se loguea ---------------------------------------------------


class TestNoPIIInLogs:
    def test_never_logs_full_name_or_cedula(self, caplog: Any) -> None:
        t = _FakeSupabase([_person_aporte("ap-1", full_name=_PII_NAME, cedula_masked=_PII_CEDULA)])
        with caplog.at_level("DEBUG", logger="scrapers.jobs.materializer"):
            _materializer(t).materialize(event_id=_EVENT_ID)
        for rec in caplog.records:
            msg = rec.getMessage()
            assert _PII_NAME not in msg
            assert _PII_CEDULA not in msg

    def test_failed_post_does_not_log_pii(self, caplog: Any) -> None:
        class _FailPersons(_FakeSupabase):
            def _upsert(self, request, store, pk):  # type: ignore[no-untyped-def]
                if request.url.path == "/rest/v1/persons":
                    return httpx.Response(500, json={"message": "boom"})
                return super()._upsert(request, store, pk)

        t = _FailPersons([_person_aporte("ap-1")])
        with caplog.at_level("DEBUG", logger="scrapers.jobs.materializer"):
            with patch("scrapers.adapters._shared.time.sleep", lambda *_: None):
                _materializer(t).materialize(event_id=_EVENT_ID)
        for rec in caplog.records:
            assert _PII_NAME not in rec.getMessage()


# --- dry-run ----------------------------------------------------------------


class TestDryRun:
    def test_disabled_makes_no_network_calls(self) -> None:
        m = SilverMaterializer(None)
        assert m.enabled is False
        result = m.materialize(event_id=_EVENT_ID)  # no debe lanzar ni abrir red
        assert result.persons_projected == 0
        assert result.acopio_projected == 0

    def test_from_env_none_enters_dry_run(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            m = SilverMaterializer(StagingConfig.from_env())
        assert m.enabled is False


# --- ciclo de vida ----------------------------------------------------------


class TestLifecycle:
    def test_does_not_close_injected_client(self) -> None:
        t = _FakeSupabase([])
        client = httpx.Client(base_url="https://project.supabase.co", transport=t)
        cfg = StagingConfig(
            supabase_url="https://project.supabase.co", publishable_key="k", ingest_jwt=_TEST_JWT
        )
        m = SilverMaterializer(cfg, client=client)
        m.close()
        resp = client.get("/rest/v1/aportes")
        assert resp.status_code == 200
        client.close()


# --- H1: seed fallido no silencia el aporte_id ------------------------------


class TestSeedFailure:
    def test_seed_failure_records_aporte_id_in_errors(self) -> None:
        # Cuando _seed_event falla (5xx), el aporte_id que quedo sin proyectar
        # debe aparecer en result.errors, no desaparecer silenciosamente.
        other_event_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        class _FailEvents(_FakeSupabase):
            def _upsert(self, request, store, pk):  # type: ignore[no-untyped-def]
                if request.url.path == "/rest/v1/events" and other_event_id in str(
                    request.content
                ):
                    return httpx.Response(500, json={"message": "transitorio"})
                return super()._upsert(request, store, pk)

        # Aporte con un event_id distinto al de config => dispara el seed secundario.
        aporte = _person_aporte("ap-silenced", event_id=other_event_id)
        t = _FailEvents([aporte])
        with patch("scrapers.adapters._shared.time.sleep", lambda *_: None):
            r = _materializer(t).materialize(event_id=_EVENT_ID)

        assert "ap-1" not in t.persons and "ap-silenced" not in t.persons
        # El aporte_id debe aparecer en errors, no quedar invisible.
        assert any("ap-silenced" in e for e in r.errors)

    def test_seed_failure_marks_page_not_ok(self) -> None:
        other_event_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        class _FailEvents(_FakeSupabase):
            def _upsert(self, request, store, pk):  # type: ignore[no-untyped-def]
                if request.url.path == "/rest/v1/events" and other_event_id in str(
                    request.content
                ):
                    return httpx.Response(503, json={"message": "transitorio"})
                return super()._upsert(request, store, pk)

        aporte = _person_aporte("ap-fail", event_id=other_event_id)
        good = _person_aporte("ap-good")  # usa event_id de config, seed ya hecho
        t = _FailEvents([good, aporte])
        with patch("scrapers.adapters._shared.time.sleep", lambda *_: None):
            r = _materializer(t).materialize(event_id=_EVENT_ID)

        # ap-good proyecta (su event_id ya fue sembrado); ap-fail no.
        assert "ap-good" in t.persons
        assert "ap-fail" not in t.persons
        # errors contiene tanto el error de seed como el del aporte silenciado.
        assert any("ap-fail" in e for e in r.errors)


# --- Part A: batch heterogeneo se acepta con missing=default ------------------


class TestBatchHeterogeneousKeys:
    """PGRST102: PostgREST rechaza un bulk insert cuyas filas no comparten el mismo
    set de claves, salvo con ``Prefer: missing=default``. Como cada aporte copia
    solo sus columnas presentes (``_typed_payload``), las filas del batch difieren;
    sin el preferente el materializer caia a fila-a-fila (lento => timeout del cron).
    """

    class _RequiresUniformKeys(_FakeSupabase):
        def _upsert(self, request, store, pk):  # type: ignore[no-untyped-def]
            if request.url.path == "/rest/v1/persons":
                prefer = request.headers.get("Prefer", "")
                body = json.loads(request.content)
                keysets = {frozenset(r.keys()) for r in body}
                if len(keysets) > 1 and "missing=default" not in prefer:
                    return httpx.Response(
                        400,
                        json={"code": "PGRST102", "message": "All object keys must match"},
                    )
            return super()._upsert(request, store, pk)

    def test_heterogeneous_rows_projected_in_single_batch(self, caplog: Any) -> None:
        # ap-1 trae cedula/age; ap-2 no => key sets distintos en el mismo batch.
        a1 = _person_aporte("ap-1")
        a2 = _person_aporte("ap-2", cedula_hmac=None, cedula_masked=None, age_range=None)
        t = self._RequiresUniformKeys([a1, a2])
        with caplog.at_level("WARNING", logger="scrapers.jobs.materializer"):
            r = _materializer(t).materialize(event_id=_EVENT_ID)
        # Ambos se proyectan en UN batch: sin PGRST102 y sin fallback fila-a-fila.
        assert len(t.persons) == 2
        assert r.persons_projected == 2
        assert not any("fila a fila" in rec.getMessage() for rec in caplog.records)

    class _RejectsHeterogeneousAlways(_FakeSupabase):
        """Modela el PostgREST DESPLEGADO: rechaza un bulk con claves dispares
        AUNQUE lleve ``missing=default`` (el preferente no se honra en prod). El
        materializer debe homogeneizar el batch en el cliente (agrupar por set de
        claves) y proyectar todo sin caer a fila-a-fila."""

        def _upsert(self, request, store, pk):  # type: ignore[no-untyped-def]
            if request.url.path == "/rest/v1/persons":
                body = json.loads(request.content)
                if len({frozenset(r.keys()) for r in body}) > 1:
                    return httpx.Response(
                        400,
                        json={"code": "PGRST102", "message": "All object keys must match"},
                    )
            return super()._upsert(request, store, pk)

    def test_projects_all_when_missing_default_is_ignored(self, caplog: Any) -> None:
        # Filas con sets de claves distintos + un server que ignora missing=default.
        a1 = _person_aporte("ap-1")
        a2 = _person_aporte("ap-2", cedula_hmac=None, cedula_masked=None, age_range=None)
        a3 = _person_aporte("ap-3")  # misma firma que ap-1 => mismo grupo
        t = self._RejectsHeterogeneousAlways([a1, a2, a3])
        with caplog.at_level("WARNING", logger="scrapers.jobs.materializer"):
            r = _materializer(t).materialize(event_id=_EVENT_ID)
        # Todo se proyecta pese a que el server rechaza batches heterogeneos, y
        # sin caer a fila-a-fila: el cliente ya mando grupos homogeneos.
        assert len(t.persons) == 3
        assert r.persons_projected == 3
        assert not any("fila a fila" in rec.getMessage() for rec in caplog.records)
        # Omitir una columna preserva su DEFAULT: ap-2 no manda cedula_hmac.
        assert "cedula_hmac" not in t.persons["ap-2"]

    def test_prefer_header_carries_missing_default(self) -> None:
        captured: list[str] = []

        class _Capture(_FakeSupabase):
            def _upsert(self, request, store, pk):  # type: ignore[no-untyped-def]
                if request.url.path == "/rest/v1/persons":
                    captured.append(request.headers.get("Prefer", ""))
                return super()._upsert(request, store, pk)

        t = _Capture([_person_aporte("ap-1")])
        _materializer(t).materialize(event_id=_EVENT_ID)
        assert captured and all("missing=default" in p for p in captured)


# --- Part B: cursor incremental (no reescanea lo ya proyectado) ---------------


class _CursorFake(_FakeSupabase):
    """_FakeSupabase + tabla silver_materialize_state (una fila) en memoria."""

    def __init__(self, aportes: list[dict[str, Any]]) -> None:
        super().__init__(aportes)
        self.cursor_row: dict[str, Any] | None = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/v1/silver_materialize_state":
            if request.method == "GET":
                return httpx.Response(200, json=[self.cursor_row] if self.cursor_row else [])
            if request.method == "POST":
                body = json.loads(request.content)
                row = body[0] if isinstance(body, list) else body
                self.cursor_row = {
                    "cursor_created_at": row["cursor_created_at"],
                    "cursor_id": row["cursor_id"],
                }
                return httpx.Response(201, json=[])
        return super().handle_request(request)


class TestIncrementalCursor:
    def test_persists_cursor_at_last_projected_aporte(self) -> None:
        aportes = [_person_aporte(f"ap-{i}") for i in range(5)]
        t = _CursorFake(aportes)
        _materializer(t).materialize(event_id=_EVENT_ID, batch_size=2)
        assert len(t.persons) == 5
        assert t.cursor_row is not None
        # La frontera durable apunta al ultimo aporte (mayor created_at, id).
        last = max(aportes, key=t._sort_key)
        assert t.cursor_row["cursor_id"] == last["id"]

    def test_rerun_does_not_rescan_already_projected(self) -> None:
        aportes = [_person_aporte(f"ap-{i}") for i in range(5)]
        t = _CursorFake(aportes)
        m = _materializer(t)
        m.materialize(event_id=_EVENT_ID, batch_size=2)
        t.aportes_gets = 0
        r2 = m.materialize(event_id=_EVENT_ID, batch_size=2)
        # Nada nuevo: no reproyecta y hace UN solo GET (la cola vacia del keyset),
        # no un re-scan de todas las paginas desde el principio.
        assert r2.persons_projected == 0
        assert t.aportes_gets == 1

    def test_resumes_from_cursor_and_projects_only_new(self) -> None:
        aportes = [_person_aporte(f"ap-{i}") for i in range(3)]
        t = _CursorFake(aportes)
        m = _materializer(t)
        m.materialize(event_id=_EVENT_ID, batch_size=10)
        assert len(t.persons) == 3
        # Llega un aporte mas nuevo (created_at posterior al cursor).
        t.aportes.append({
            "id": "ap-new",
            "entity_type": "person",
            "raw_json": {"full_name": _PII_NAME, "event_id": _EVENT_ID, "status": "missing"},
            "created_at": "2026-07-01T00:00:00.900000Z",
        })
        r2 = m.materialize(event_id=_EVENT_ID, batch_size=10)
        assert "ap-new" in t.persons
        assert r2.persons_projected == 1

    def test_batch_network_failure_does_not_advance_cursor(self) -> None:
        # Una pagina que falla por red (POST -> None tras agotar reintentos) NO debe
        # avanzar el cursor durable: la proxima corrida la reintegra.
        class _NetFailPersons(_CursorFake):
            fail = True

            def handle_request(self, request):  # type: ignore[no-untyped-def]
                if (
                    self.fail
                    and request.method == "POST"
                    and request.url.path == "/rest/v1/persons"
                ):
                    raise httpx.ConnectError("boom")
                return super().handle_request(request)

        aportes = [_person_aporte(f"ap-{i}") for i in range(2)]
        t = _NetFailPersons(aportes)
        with patch("scrapers.adapters._shared.time.sleep", lambda *_: None):
            m = _materializer(t)
            m.materialize(event_id=_EVENT_ID, batch_size=10)
            assert t.cursor_row is None  # congelado: no avanzo pese al fallo
            assert len(t.persons) == 0
            # Se restablece la red: la re-corrida proyecta desde el principio.
            t.fail = False
            r2 = m.materialize(event_id=_EVENT_ID, batch_size=10)
        assert len(t.persons) == 2
        assert r2.persons_projected == 2
        assert t.cursor_row is not None
