"""Tests offline del adapter Supabase PostgREST del consolidation job (#91).

Ningun test hace red real: el httpx.Client se construye con un
``httpx.MockTransport`` inyectado via el constructor del adapter (mismo patron
que test_staging_exporter / test_consolidation_job). Cubre:
  - Config from_env: dry-run intencional, config parcial, HTTPS obligatorio.
  - fetch_unconsolidated: filtros PostgREST correctos, orden, limit, mapeo de
    columnas reales (raw_json -> payload) y degradacion segura de trust_tier.
  - upsert_canonical: on_conflict=dedup_hash + Prefer merge-duplicates y
    proyeccion SOLO sobre columnas canonicas reales.
  - mark_consolidated: PATCH con id=in.(...) y chunking.
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
    SupabaseConsolidationAdapter,
    SupabaseConsolidationConfig,
)

_URL = "https://proj.supabase.co"
_KEY = "service-key-xyz"


def _adapter(handler: Any) -> SupabaseConsolidationAdapter:
    client = httpx.Client(
        base_url=_URL,
        headers={"apikey": _KEY, "Authorization": f"Bearer {_KEY}"},
        transport=httpx.MockTransport(handler),
    )
    return SupabaseConsolidationAdapter(client)


# --- Config from_env --------------------------------------------------------

def test_from_env_sin_variables_es_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    assert SupabaseConsolidationConfig.from_env() is None


def test_from_env_config_parcial_es_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", _URL)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    assert SupabaseConsolidationConfig.from_env() is None


def test_from_env_rechaza_http_plano(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "http://proj.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", _KEY)
    assert SupabaseConsolidationConfig.from_env() is None


def test_from_env_ok_con_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", _URL + "/")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", _KEY)
    config = SupabaseConsolidationConfig.from_env()
    assert config is not None
    assert config.supabase_url == _URL  # trailing slash removido
    assert config.supabase_key == _KEY


# --- fetch_unconsolidated ---------------------------------------------------

def test_fetch_envia_filtros_correctos() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url
        return httpx.Response(200, json=[])

    adapter = _adapter(handler)
    adapter.fetch_unconsolidated("Event", batch_size=250)

    query = parse_qs(urlparse(str(captured["url"])).query)
    assert urlparse(str(captured["url"])).path == "/rest/v1/aportes"
    assert query["consolidated_at"] == ["is.null"]
    assert query["entity_type"] == ["eq.event"]  # slug del enum del backend
    assert query["order"] == ["created_at.asc,id.asc"]
    assert query["limit"] == ["250"]
    assert query["select"] == ["*"]


def test_fetch_acopio_usa_slug_acopio() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url
        return httpx.Response(200, json=[])

    adapter = _adapter(handler)
    adapter.fetch_unconsolidated("AcopioCenter", batch_size=10)
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
    records = adapter.fetch_unconsolidated("Event", batch_size=10)

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
    rec = adapter.fetch_unconsolidated("Event", batch_size=10)[0]
    assert rec["trust_tier"] == ""
    assert rec["fetched_at"] is None
    assert rec["confidence_score"] is None


def test_fetch_entity_type_no_soportado_lanza() -> None:
    adapter = _adapter(lambda request: httpx.Response(200, json=[]))
    with pytest.raises(ValueError):
        adapter.fetch_unconsolidated("Person", batch_size=10)


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


# --- mark_consolidated ------------------------------------------------------

def test_mark_consolidated_patch_con_in_filter() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "url": str(request.url),
                "method": request.method,
                "body": json.loads(request.content),
                "prefer": request.headers.get("Prefer"),
            }
        )
        return httpx.Response(204)

    adapter = _adapter(handler)
    adapter.mark_consolidated(["ap-1", "ap-2", "ap-3"])

    assert len(captured) == 1
    call = captured[0]
    assert call["method"] == "PATCH"
    parsed = urlparse(call["url"])
    assert parsed.path == "/rest/v1/aportes"
    assert parse_qs(parsed.query)["id"] == ["in.(ap-1,ap-2,ap-3)"]
    assert "consolidated_at" in call["body"]
    assert "return=minimal" in call["prefer"]


def test_mark_consolidated_vacio_no_llama() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(204)

    adapter = _adapter(handler)
    adapter.mark_consolidated([])
    assert calls == 0


def test_mark_consolidated_chunkea() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(204)

    adapter = _adapter(handler)
    ids = [f"ap-{i}" for i in range(250)]
    adapter.mark_consolidated(ids)
    # 250 ids en chunks de 100 -> 3 requests.
    assert len(calls) == 3
    first_ids = parse_qs(urlparse(calls[0]).query)["id"][0]
    assert first_ids.count(",") == 99  # 100 ids => 99 comas


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
