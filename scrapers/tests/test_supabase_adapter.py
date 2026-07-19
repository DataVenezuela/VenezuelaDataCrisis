"""Tests offline del adapter Supabase PostgREST del consolidation job (#91).

Ningun test hace red real: el httpx.Client se construye con un
``httpx.MockTransport`` inyectado via el constructor del adapter (mismo patron
que test_staging_exporter / test_consolidation_job). Cubre:
  - Config from_env: dry-run intencional, config parcial, HTTPS obligatorio.
  - fetch_aportes_page: paginacion por cursor keyset (created_at, id), filtros
    PostgREST correctos (SIN consolidated_at), orden, limit, mapeo de columnas
    reales (raw_json -> payload) y degradacion segura de trust_tier.
  - upsert_canonical: on_conflict=dedup_hash + Prefer merge-duplicates y
    proyeccion SOLO sobre columnas canonicas reales.
  - winner-selection final: menor tier gana; desempate fetched_at desc; luego
    confidence_score desc.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from scrapers.jobs.consolidation_job import default_tier_rank, pick_winner
from scrapers.jobs.ports import Record
from scrapers.jobs.supabase_adapter import (
    _MAX_RETRIES,
    SupabaseConsolidationAdapter,
    SupabaseConsolidationConfig,
)

_URL = "https://proj.supabase.co"
# apikey (publishable) y Bearer (JWT) son credenciales DISTINTAS por diseno
# (patron #200): los tests las mantienen separadas para no volver al falso-verde
# de reusar la misma key en ambos headers.
_PUBLISHABLE_KEY = "publishable-key-xyz"
_CONSOLIDATION_JWT = "consolidation-jwt-abc"


def _adapter(handler: Any) -> SupabaseConsolidationAdapter:
    client = httpx.Client(
        base_url=_URL,
        headers={
            "apikey": _PUBLISHABLE_KEY,
            "Authorization": f"Bearer {_CONSOLIDATION_JWT}",
        },
        transport=httpx.MockTransport(handler),
    )
    return SupabaseConsolidationAdapter(client)


# --- Config from_env --------------------------------------------------------

def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_CONSOLIDATION_JWT", raising=False)


def test_from_env_sin_variables_es_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    assert SupabaseConsolidationConfig.from_env() is None


def test_from_env_config_parcial_es_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("SUPABASE_URL", _URL)
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _PUBLISHABLE_KEY)
    # falta SUPABASE_CONSOLIDATION_JWT => dry-run
    assert SupabaseConsolidationConfig.from_env() is None


def test_from_env_rechaza_http_plano(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("SUPABASE_URL", "http://proj.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _PUBLISHABLE_KEY)
    monkeypatch.setenv("SUPABASE_CONSOLIDATION_JWT", _CONSOLIDATION_JWT)
    assert SupabaseConsolidationConfig.from_env() is None


def test_from_env_ok_con_https(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("SUPABASE_URL", _URL + "/")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _PUBLISHABLE_KEY)
    monkeypatch.setenv("SUPABASE_CONSOLIDATION_JWT", _CONSOLIDATION_JWT)
    config = SupabaseConsolidationConfig.from_env()
    assert config is not None
    assert config.supabase_url == _URL  # trailing slash removido
    assert config.publishable_key == _PUBLISHABLE_KEY
    assert config.consolidation_jwt == _CONSOLIDATION_JWT


def test_from_config_headers_apikey_publishable_bearer_jwt() -> None:
    """El adapter real manda apikey=publishable y Bearer=JWT (NO la misma key).

    Verifica el patron #200: from_config traduce la config a headers distintos
    por credencial (apikey != Bearer), sin service_role. Se lee el default de
    headers del httpx.Client que arma from_config, sin abrir red.
    """
    config = SupabaseConsolidationConfig(
        supabase_url=_URL,
        publishable_key=_PUBLISHABLE_KEY,
        consolidation_jwt=_CONSOLIDATION_JWT,
    )
    adapter = SupabaseConsolidationAdapter.from_config(config)
    try:
        headers = adapter._client.headers
        assert headers["apikey"] == _PUBLISHABLE_KEY
        assert headers["Authorization"] == f"Bearer {_CONSOLIDATION_JWT}"
        # blindaje anti falso-verde: apikey y Bearer NO son el mismo valor.
        assert headers["apikey"] != _CONSOLIDATION_JWT
    finally:
        adapter.close()


# --- fetch_aportes_page -----------------------------------------------------

def test_fetch_envia_filtros_correctos() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url
        return httpx.Response(200, json=[])

    adapter = _adapter(handler)
    adapter.fetch_aportes_page("Event", 250, ("2026-06-24T10:00:00Z", "ap-9"))

    query = parse_qs(urlparse(str(captured["url"])).query)
    assert urlparse(str(captured["url"])).path == "/rest/v1/aportes"
    # NO se filtra por consolidated_at (columna inexistente en el schema real);
    # la paginacion la lleva el cursor keyset (created_at, id).
    assert "consolidated_at" not in query
    assert query["entity_type"] == ["eq.event"]  # slug del enum del backend
    assert query["or"] == [
        "(created_at.gt.2026-06-24T10:00:00Z,"
        "and(created_at.eq.2026-06-24T10:00:00Z,id.gt.ap-9))"
    ]
    assert query["order"] == ["created_at.asc,id.asc"]
    assert query["limit"] == ["250"]
    assert query["select"] == ["*"]


def test_fetch_acopio_usa_slug_acopio() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url
        return httpx.Response(200, json=[])

    adapter = _adapter(handler)
    adapter.fetch_aportes_page("AcopioCenter", 10, ("", ""))
    query = parse_qs(urlparse(str(captured["url"])).query)
    assert query["entity_type"] == ["eq.acopio"]


def test_fetch_mapea_columnas_reales_a_record() -> None:
    row = {
        "id": "ap-1",
        "created_at": "2026-06-24T10:00:00Z",
        "source_id": "src-a",
        "entity_type": "event",
        "dedup_hash": "h1",
        "raw_json": {"name": "Sismo", "event_type": "earthquake"},
        "trust_tier": "A",
        "fetched_at": "2026-06-24T09:59:00Z",
        "confidence_score": 0.8,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[row])

    adapter = _adapter(handler)
    records = adapter.fetch_aportes_page("Event", 10, ("", ""))

    assert len(records) == 1
    rec = records[0]
    assert rec["id"] == "ap-1"
    assert rec["dedup_hash"] == "h1"
    # raw_json -> payload (unico rename semantico).
    assert rec["payload"] == {"name": "Sismo", "event_type": "earthquake"}
    assert rec["trust_tier"] == "A"
    assert rec["fetched_at"] == "2026-06-24T09:59:00Z"
    assert rec["confidence_score"] == 0.8


def test_fetch_trust_tier_ausente_degrada_seguro() -> None:
    # aportes.trust_tier NO existe todavia en el schema real; si la respuesta no
    # lo trae, el Record queda con trust_tier vacio (rango peor en pick_winner).
    row = {
        "id": "ap-1",
        "created_at": "2026-06-24T10:00:00Z",
        "entity_type": "event",
        "dedup_hash": "h1",
        "raw_json": {},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[row])

    adapter = _adapter(handler)
    rec = adapter.fetch_aportes_page("Event", 10, ("", ""))[0]
    assert rec["trust_tier"] == ""
    assert rec["fetched_at"] is None
    assert rec["confidence_score"] is None


def test_fetch_entity_type_no_soportado_lanza() -> None:
    adapter = _adapter(lambda request: httpx.Response(200, json=[]))
    with pytest.raises(ValueError):
        adapter.fetch_aportes_page("Person", 10, ("", ""))


# --- upsert_canonical -------------------------------------------------------

def test_upsert_usa_on_conflict_y_prefer_merge() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url
        captured["prefer"] = request.headers.get("Prefer")
        captured["body"] = json.loads(request.content)
        return httpx.Response(201)

    adapter = _adapter(handler)
    record: Record = {
        "dedup_hash": "h1",
        "name": "Sismo Demo",
        "event_type": "earthquake",
        "occurred_at": "2026-06-24T10:00:00Z",
        "status": "active",
        # metadata interna del job: NO son columnas y deben descartarse.
        "dedup_version": "v1",
        "winner_aporte_id": "ap-1",
        # columna inexistente: no debe filtrarse al backend.
        "columna_inventada": "x",
    }
    adapter.upsert_canonical("Event", record)

    parsed = urlparse(str(captured["url"]))
    assert parsed.path == "/rest/v1/events"
    assert parse_qs(parsed.query)["on_conflict"] == ["dedup_hash"]
    assert "resolution=merge-duplicates" in captured["prefer"]
    assert "return=minimal" in captured["prefer"]
    body = captured["body"]
    assert body["dedup_hash"] == "h1"
    assert body["name"] == "Sismo Demo"
    assert body["event_type"] == "earthquake"
    assert body["status"] == "active"
    # metadata interna y columnas inventadas descartadas (fidelidad de schema).
    assert "dedup_version" not in body
    assert "winner_aporte_id" not in body
    assert "columna_inventada" not in body


def test_upsert_acopio_apunta_a_acopio_centers() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url
        captured["body"] = json.loads(request.content)
        return httpx.Response(201)

    adapter = _adapter(handler)
    adapter.upsert_canonical(
        "AcopioCenter",
        {"dedup_hash": "h2", "name": "Centro", "status": "active", "event_id": "ev-1"},
    )
    assert urlparse(str(captured["url"])).path == "/rest/v1/acopio_centers"
    assert captured["body"]["event_id"] == "ev-1"


def test_upsert_sin_dedup_hash_falla() -> None:
    adapter = _adapter(lambda request: httpx.Response(201))
    with pytest.raises(ValueError):
        adapter.upsert_canonical("Event", {"name": "X"})


# --- errores HTTP y reintentos (media #3) -----------------------------------

def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    # Neutraliza el backoff para que los tests de retry sean rapidos y
    # deterministas (no dormimos entre intentos).
    monkeypatch.setattr("scrapers.jobs.supabase_adapter.time.sleep", lambda _n: None)


def test_status_transitorio_reintenta_y_luego_exito(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 503 en los primeros dos intentos, 200 en el tercero: el adapter reintenta
    # y termina devolviendo el resultado sin error.
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={"message": "unavailable"})
        return httpx.Response(200, json=[])

    adapter = _adapter(handler)
    assert adapter.fetch_aportes_page("Event", 10, ("", "")) == []
    assert calls["n"] == 3


def test_retry_exhaustion_lanza_http_status_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 503 persistente: agota _MAX_RETRIES intentos y raise_for_status propaga.
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"message": "down"})

    adapter = _adapter(handler)
    with pytest.raises(httpx.HTTPStatusError):
        adapter.fetch_aportes_page("Event", 10, ("", ""))
    assert calls["n"] == _MAX_RETRIES


@pytest.mark.parametrize("status", [401, 403, 422])
def test_status_no_retryable_no_reintenta(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    # 4xx no transitorio: NO reintenta (un solo intento) y raise_for_status
    # propaga de inmediato.
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(status, json={"message": "no"})

    adapter = _adapter(handler)
    with pytest.raises(httpx.HTTPStatusError):
        adapter.fetch_aportes_page("Event", 10, ("", ""))
    assert calls["n"] == 1


def test_error_de_red_reintenta_y_relanza(monkeypatch: pytest.MonkeyPatch) -> None:
    # Error de transporte (NetworkError): reintenta _MAX_RETRIES veces y, si no
    # hay response, relanza la ultima excepcion.
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("conexion rechazada")

    adapter = _adapter(handler)
    with pytest.raises(httpx.ConnectError):
        adapter.fetch_aportes_page("Event", 10, ("", ""))
    assert calls["n"] == _MAX_RETRIES


# --- winner-selection final (decision del equipo #82) -----------------------

def _rec(**kw: Any) -> Record:
    base: dict[str, Any] = {
        "id": kw.get("id", "x"),
        "trust_tier": kw.get("trust_tier", "B"),
        "fetched_at": kw.get("fetched_at"),
        "confidence_score": kw.get("confidence_score"),
        "created_at": kw.get("created_at", "2026-01-01T00:00:00Z"),
        "source_id": kw.get("source_id", "s"),
    }
    return base


def test_winner_menor_tier_gana() -> None:
    group = [
        _rec(id="d", trust_tier="D"),
        _rec(id="a", trust_tier="A"),
        _rec(id="c", trust_tier="C"),
    ]
    assert pick_winner(group)["id"] == "a"  # A=1 gana (menor)


def test_winner_desempate_fetched_at_mas_reciente() -> None:
    # Mismo tier: gana el fetched_at mas reciente.
    group = [
        _rec(id="viejo", trust_tier="B", fetched_at="2026-06-01T00:00:00Z"),
        _rec(id="nuevo", trust_tier="B", fetched_at="2026-06-10T00:00:00Z"),
    ]
    assert pick_winner(group)["id"] == "nuevo"


def test_winner_desempate_confidence_score_cuando_fetched_at_empata() -> None:
    # Mismo tier y mismo fetched_at: gana el confidence_score mayor.
    group = [
        _rec(
            id="baja",
            trust_tier="B",
            fetched_at="2026-06-10T00:00:00Z",
            confidence_score=0.4,
        ),
        _rec(
            id="alta",
            trust_tier="B",
            fetched_at="2026-06-10T00:00:00Z",
            confidence_score=0.9,
        ),
    ]
    assert pick_winner(group)["id"] == "alta"


def test_winner_tier_precede_a_fetched_at() -> None:
    # El tier manda por encima del fetched_at: A gana aunque sea mas viejo.
    group = [
        _rec(id="a_viejo", trust_tier="A", fetched_at="2026-01-01T00:00:00Z"),
        _rec(id="d_nuevo", trust_tier="D", fetched_at="2026-12-31T00:00:00Z"),
    ]
    assert pick_winner(group)["id"] == "a_viejo"


def test_winner_es_determinista_independiente_del_orden() -> None:
    group = [
        _rec(id="a", trust_tier="A", fetched_at="2026-06-01T00:00:00Z"),
        _rec(id="b", trust_tier="A", fetched_at="2026-06-05T00:00:00Z"),
    ]
    assert pick_winner(group)["id"] == pick_winner(list(reversed(group)))["id"] == "b"


def test_default_tier_rank_orden() -> None:
    assert default_tier_rank("A") < default_tier_rank("B") < default_tier_rank("C") < \
        default_tier_rank("D")
    assert default_tier_rank("?") > default_tier_rank("D")


# --- fetch_person_candidates ------------------------------------------------

def test_fetch_person_candidates_envia_filtros_correctos() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url
        return httpx.Response(200, json=[])

    adapter = _adapter(handler)
    adapter.fetch_person_candidates(
        block_keys=["phon:ev1:abc123", "ced:ev1:hmac456"],
    )

    query = parse_qs(urlparse(str(captured["url"])).query)
    assert urlparse(str(captured["url"])).path == "/rest/v1/aportes"
    # NO se filtra por consolidated_at: el dedup debe ver todos los aportes del
    # bloque, incluidos los ya procesados en corridas previas.
    assert "consolidated_at" not in query
    assert query["entity_type"] == ["eq.person"]
    assert "or" in query
    or_val = query["or"][0]
    assert 'block_keys.cs.["phon:ev1:abc123"]' in or_val
    assert 'block_keys.cs.["ced:ev1:hmac456"]' in or_val


def test_fetch_person_candidates_vacio_no_llama() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=[])

    adapter = _adapter(handler)
    result = adapter.fetch_person_candidates(block_keys=[])
    assert result == []
    assert calls == 0


def test_fetch_person_candidates_mapea_records() -> None:
    row = {
        "id": "ap-p1",
        "created_at": "2026-06-24T10:00:00Z",
        "source_id": "src-a",
        "entity_type": "person",
        "dedup_hash": None,
        "raw_json": {"full_name": "Ana Gonzalez", "event_id": "ev1"},
        "trust_tier": "B",
        "block_keys": ["phon:ev1:abc123"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[row])

    adapter = _adapter(handler)
    records = adapter.fetch_person_candidates(
        block_keys=["phon:ev1:abc123"],
    )

    assert len(records) == 1
    rec = records[0]
    assert rec["id"] == "ap-p1"
    assert rec["payload"] == {"full_name": "Ana Gonzalez", "event_id": "ev1"}
    assert rec["trust_tier"] == "B"


def test_fetch_person_candidates_un_solo_block_key() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url
        return httpx.Response(200, json=[])

    adapter = _adapter(handler)
    adapter.fetch_person_candidates(block_keys=["phon:ev2:xyz"])

    or_val = parse_qs(urlparse(str(captured["url"])).query)["or"][0]
    assert or_val == '(block_keys.cs.["phon:ev2:xyz"])'


# --- Cursor durable por entity_type (option B, #93) -------------------------

def test_read_cursor_devuelve_frontera_y_filtra_por_slug() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url
        return httpx.Response(200, json=[{
            "cursor_created_at": "2024-05-01T00:00:00Z",
            "cursor_id": "c-42",
        }])

    adapter = _adapter(handler)
    cursor = adapter.read_cursor("Event")

    assert cursor == ("2024-05-01T00:00:00Z", "c-42")
    query = parse_qs(urlparse(str(captured["url"])).query)
    # Se consulta consolidation_state filtrando por el slug de la DB (event), no
    # por el nombre interno (Event).
    assert urlparse(str(captured["url"])).path == "/rest/v1/consolidation_state"
    assert query["entity_type"] == ["eq.event"]


def test_read_cursor_vacio_devuelve_none() -> None:
    adapter = _adapter(lambda request: httpx.Response(200, json=[]))
    assert adapter.read_cursor("AcopioCenter") is None


def test_read_cursor_tabla_ausente_degrada_a_none() -> None:
    adapter = _adapter(lambda request: httpx.Response(404, json={"message": "no relation"}))
    assert adapter.read_cursor("Event") is None
    assert adapter._cursor_unavailable is True


def test_read_cursor_sin_permiso_degrada_a_none() -> None:
    adapter = _adapter(lambda request: httpx.Response(403, json={"message": "permission denied"}))
    assert adapter.read_cursor("Event") is None
    assert adapter._cursor_unavailable is True


def test_write_cursor_upsert_por_entity_type() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url
        captured["body"] = json.loads(request.content)
        captured["prefer"] = request.headers.get("Prefer")
        return httpx.Response(204)

    adapter = _adapter(handler)
    ok = adapter.write_cursor("AcopioCenter", "2024-06-01T00:00:00Z", "c-99")

    assert ok is True
    assert "on_conflict=entity_type" in str(captured["url"])
    row = captured["body"][0]
    assert row["entity_type"] == "acopio"
    assert row["cursor_created_at"] == "2024-06-01T00:00:00Z"
    assert row["cursor_id"] == "c-99"
    # updated_at explicito para que el UPDATE de merge-duplicates lo refresque.
    assert "updated_at" in row
    assert "resolution=merge-duplicates" in captured["prefer"]


def test_write_cursor_tabla_ausente_no_reintenta() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, json={"message": "no relation"})

    adapter = _adapter(handler)
    assert adapter.write_cursor("Event", "2024-06-01T00:00:00Z", "c-1") is False
    # Marcada como no disponible => una segunda escritura no toca la red.
    assert adapter.write_cursor("Event", "2024-06-02T00:00:00Z", "c-2") is False
    assert calls["n"] == 1
