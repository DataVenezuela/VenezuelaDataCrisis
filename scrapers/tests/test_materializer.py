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
        return httpx.Response(404, json={"path": path})

    def _get_aportes(self, request: httpx.Request) -> httpx.Response:
        self.aportes_gets += 1
        qs = parse_qs(urlparse(str(request.url)).query)
        limit = int(qs.get("limit", ["1000"])[0])
        offset = int(qs.get("offset", ["0"])[0])
        page = self.aportes[offset : offset + limit]
        return httpx.Response(200, json=page)

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


class TestSeedFailure:
    def test_seed_failure_aborts_projection(self) -> None:
        # Si la fila de catalogo events NO se puede sembrar, proyectar persons/acopio
        # con event_id apuntando a una fila inexistente rompe la FK. En vez de
        # emitir proyecciones condenadas (batch 23503 -> reintento fila a fila que
        # tambien falla), el materializer aborta ANTES de escanear aportes y registra
        # el error, para no reportar la corrida como exitosa.
        class _FailEvents(_FakeSupabase):
            def _upsert(self, request, store, pk):  # type: ignore[no-untyped-def]
                if request.url.path == "/rest/v1/events":
                    return httpx.Response(500, json={"message": "boom"})
                return super()._upsert(request, store, pk)

        t = _FailEvents([_person_aporte("ap-1"), _acopio_aporte("ap-2")])
        with patch("scrapers.jobs.materializer.time.sleep", lambda *_: None):
            r = _materializer(t).materialize(event_id=_EVENT_ID)
        assert r.persons_projected == 0
        assert r.acopio_projected == 0
        assert t.persons == {}
        assert t.acopios == {}
        # Aborto ANTES de escanear aportes: ni un GET a /aportes ni proyeccion.
        assert t.aportes_gets == 0
        assert any("event" in e for e in r.errors)


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

    def test_empty_200_page_ends_cleanly(self) -> None:
        # Fin-de-datos legitimo: la pagina extra tras un multiplo exacto del batch
        # devuelve 200 con []. Eso corta el paginado SIN registrar error.
        aportes = [_person_aporte("ap-0"), _person_aporte("ap-1")]
        t = _FakeSupabase(aportes)
        r = _materializer(t).materialize(event_id=_EVENT_ID, batch_size=1)
        assert len(t.persons) == 2
        assert r.errors == []


# --- fetch transitorio: reintenta, y nunca reporta un scan truncado como limpio -


class TestFetchRetry:
    def test_transient_get_failure_is_retried_and_recorded(self) -> None:
        # Un 503 transitorio a mitad del paginado NO puede confundirse con
        # fin-de-datos (que tambien seria una lista vacia): se reintenta y, si
        # persiste, se registra un error y se corta el scan (la corrida deja de
        # reportarse limpia). Antes se devolvia [] silenciosamente y se perdia
        # la cola de aportes sin senal alguna.
        class _FlakyGet(_FakeSupabase):
            def _get_aportes(self, request: httpx.Request) -> httpx.Response:
                qs = parse_qs(urlparse(str(request.url)).query)
                offset = int(qs.get("offset", ["0"])[0])
                if offset > 0:
                    self.aportes_gets += 1
                    return httpx.Response(503, json={"message": "throttled"})
                return super()._get_aportes(request)

        t = _FlakyGet([_person_aporte("ap-0"), _person_aporte("ap-1")])
        with patch("scrapers.jobs.materializer.time.sleep", lambda *_: None):
            r = _materializer(t).materialize(event_id=_EVENT_ID, batch_size=1)
        # La primera pagina si proyecto; la segunda fallo su fetch.
        assert "ap-0" in t.persons
        assert "ap-1" not in t.persons
        # El fallo se refleja: la corrida no es "limpia".
        assert r.errors
        assert any("503" in e or "aportes" in e for e in r.errors)
        # El offset que fallo se reintento (mas de un GET a ese offset).
        assert t.aportes_gets >= 5


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
            with patch("scrapers.jobs.materializer.time.sleep", lambda *_: None):
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
