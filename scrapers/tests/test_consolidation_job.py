"""Tests offline del job de consolidacion (#91): Event/AcopioCenter.

Todo corre contra `FakeInMemoryAdapter`, sin red ni DB real. Cubre los
criterios de aceptacion de #91: 3 aportes con el mismo dedup_hash producen 1
fila canonica y los 3 quedan marcados; gana el de mayor tier; re-correr es
idempotente; --dry-run no muta el fake; interrupcion+reintento procesa solo lo
pendiente.
"""

from __future__ import annotations

import logging
import json
from typing import Any

import httpx
import pytest

import scrapers.jobs.consolidation_job as consolidation_job

from scrapers.jobs.consolidation_job import (
    PersonConsolidationConfig,
    SupabasePersonDedupAdapter,
    canonical_from_winner,
    consolidate_entity_type,
    default_tier_rank,
    group_by_dedup_hash,
    main,
    pick_winner,
    run_person_consolidation,
)
from scrapers.jobs.ports import ConsolidationDataPort, FakeInMemoryAdapter, Record

_HASH = "hash-evento-demo"
_HASH_B = "hash-evento-demo-2"


def _event_aporte(
    aporte_id: str,
    dedup_hash: str = _HASH,
    trust_tier: str = "D",
    source_id: str = "fuente-x",
    created_at: str = "2026-06-24T14:00:00Z",
) -> Record:
    return {
        "id": aporte_id,
        "entity_type": "Event",
        "dedup_hash": dedup_hash,
        "trust_tier": trust_tier,
        "source_id": source_id,
        "created_at": created_at,
        "payload": {
            "event_type": "earthquake",
            "location_text": "Ciudad Demo, Estado Demo",
            "date_iso": "2026-06-24T14:32:00Z",
        },
    }


def test_fake_adapter_satisface_el_protocolo() -> None:
    adapter = FakeInMemoryAdapter()
    assert isinstance(adapter, ConsolidationDataPort)


def test_tres_aportes_mismo_hash_una_fila_y_tres_marcados() -> None:
    aportes = [_event_aporte(f"a{i}") for i in range(3)]
    adapter = FakeInMemoryAdapter(aportes)

    summary = consolidate_entity_type(adapter, "Event", batch_size=10)

    assert summary["groups"] == 1
    assert summary["aportes"] == 3
    # Una sola fila canonica por dedup_hash.
    assert len(adapter.canonical) == 1
    assert ("Event", _HASH) in adapter.canonical
    # Los tres aportes quedan marcados como consolidados.
    assert adapter.consolidated_ids == {"a0", "a1", "a2"}


def test_gana_el_de_menor_tier() -> None:
    # Decision del equipo (#82): A=1..D=4, GANA EL MENOR (A es la fuente mas
    # confiable), asi que el aporte con tier A gana el grupo.
    aportes = [
        _event_aporte("baja", trust_tier="D", source_id="social"),
        _event_aporte("alta", trust_tier="A", source_id="oficial"),
        _event_aporte("media", trust_tier="C", source_id="ong"),
    ]
    adapter = FakeInMemoryAdapter(aportes)

    consolidate_entity_type(adapter, "Event", batch_size=10)

    canonical = adapter.canonical[("Event", _HASH)]
    assert canonical["winner_aporte_id"] == "alta"
    # Aun asi los tres aportes del grupo quedan marcados.
    assert adapter.consolidated_ids == {"baja", "alta", "media"}


def test_recorrer_dos_veces_es_idempotente() -> None:
    aportes = [_event_aporte(f"a{i}", trust_tier="B") for i in range(3)]
    adapter = FakeInMemoryAdapter(aportes)

    first = consolidate_entity_type(adapter, "Event", batch_size=10)
    canonical_after_first = dict(adapter.canonical)
    upserts_after_first = adapter.upsert_calls

    second = consolidate_entity_type(adapter, "Event", batch_size=10)

    assert first["groups"] == 1
    # La segunda corrida no encuentra pendientes: no agrupa, no upserta, no marca.
    assert second["groups"] == 0
    assert second["upserts"] == 0
    assert second["marked"] == 0
    assert adapter.upsert_calls == upserts_after_first
    assert adapter.canonical == canonical_after_first
    assert len(adapter.canonical) == 1


def test_dry_run_no_muta_el_fake() -> None:
    aportes = [_event_aporte(f"a{i}") for i in range(3)]
    adapter = FakeInMemoryAdapter(aportes)

    summary = consolidate_entity_type(adapter, "Event", batch_size=10, dry_run=True)

    # Se planifico el grupo pero NO se escribio nada.
    assert summary["groups"] == 1
    assert summary["upserts"] == 0
    assert summary["marked"] == 0
    assert adapter.canonical == {}
    assert adapter.consolidated_ids == set()
    assert adapter.upsert_calls == 0
    assert adapter.mark_calls == 0


def test_interrupcion_y_reintento_procesa_solo_lo_pendiente() -> None:
    # Grupo 1 ya consolidado (simula una corrida previa interrumpida tras el
    # primer grupo); grupo 2 pendiente.
    aportes = [
        _event_aporte("g1a", dedup_hash=_HASH),
        _event_aporte("g1b", dedup_hash=_HASH),
        _event_aporte("g2a", dedup_hash=_HASH_B),
        _event_aporte("g2b", dedup_hash=_HASH_B),
    ]
    adapter = FakeInMemoryAdapter(aportes)
    adapter.mark_consolidated(["g1a", "g1b"])
    # Simula que la fila canonica del grupo 1 ya existia.
    adapter.canonical[("Event", _HASH)] = {"dedup_hash": _HASH, "winner_aporte_id": "g1a"}
    marks_before = adapter.mark_calls

    summary = consolidate_entity_type(adapter, "Event", batch_size=10)

    # Solo se procesa el grupo pendiente.
    assert summary["groups"] == 1
    assert summary["aportes"] == 2
    assert adapter.consolidated_ids == {"g1a", "g1b", "g2a", "g2b"}
    assert len(adapter.canonical) == 2
    assert ("Event", _HASH_B) in adapter.canonical
    # No re-marca los ya consolidados del grupo 1.
    assert adapter.mark_calls == marks_before + 1


def test_batches_pequenos_procesan_todo() -> None:
    # 3 grupos distintos, batch_size=1 fuerza multiples iteraciones.
    aportes = [
        _event_aporte("h1", dedup_hash="h1"),
        _event_aporte("h2", dedup_hash="h2"),
        _event_aporte("h3", dedup_hash="h3"),
    ]
    adapter = FakeInMemoryAdapter(aportes)

    summary = consolidate_entity_type(adapter, "Event", batch_size=1)

    assert summary["groups"] == 3
    assert summary["batches"] == 3
    assert len(adapter.canonical) == 3
    assert adapter.consolidated_ids == {"h1", "h2", "h3"}


def test_aportes_sin_dedup_hash_se_ignoran(caplog) -> None:
    aportes = [
        _event_aporte("ok"),
        {"id": "sin_hash", "entity_type": "Event", "dedup_hash": None, "payload": {}},
    ]
    adapter = FakeInMemoryAdapter(aportes)

    caplog.set_level(logging.WARNING, logger="scrapers.jobs.consolidation_job")
    summary = consolidate_entity_type(adapter, "Event", batch_size=10)

    assert summary["groups"] == 1
    assert adapter.consolidated_ids == {"ok"}
    assert "sin_dedup_hash" in caplog.text


def test_acopio_center_tambien_consolida() -> None:
    aportes = [
        {
            "id": f"c{i}",
            "entity_type": "AcopioCenter",
            "dedup_hash": "hash-acopio",
            "trust_tier": "C" if i == 0 else "A",
            "source_id": f"src{i}",
            "created_at": "2026-06-24T10:00:00Z",
            "payload": {"name": "Centro Demo"},
        }
        for i in range(2)
    ]
    adapter = FakeInMemoryAdapter(aportes)

    summary = consolidate_entity_type(adapter, "AcopioCenter", batch_size=10)

    assert summary["groups"] == 1
    assert len(adapter.canonical) == 1
    canonical = adapter.canonical[("AcopioCenter", "hash-acopio")]
    assert canonical["winner_aporte_id"] == "c1"  # tier A gana


class _FailingUpsertAdapter(FakeInMemoryAdapter):
    """Fake que falla el upsert de un dedup_hash puntual (resto OK).

    Simula un fallo parcial de batch (p.ej. un 409/500 de PostgREST en un grupo)
    para verificar que el job NO aborta los grupos siguientes.
    """

    def __init__(self, aportes: list[Record], fail_hash: str) -> None:
        super().__init__(aportes)
        self._fail_hash = fail_hash

    def upsert_canonical(self, entity_type: str, record: Record) -> None:
        if record.get("dedup_hash") == self._fail_hash:
            raise httpx.HTTPError(f"boom en upsert de {self._fail_hash}")
        super().upsert_canonical(entity_type, record)


def test_fallo_de_grupo_no_aborta_los_sanos(caplog: pytest.LogCaptureFixture) -> None:
    # Bloqueante #1: si el upsert de un grupo falla, los demas grupos del batch
    # deben consolidarse igual (analogo a la regla del staging_exporter: el batch
    # avanza pese a fallos parciales). La excepcion NO se propaga.
    aportes = [
        _event_aporte("a1", dedup_hash="ha"),
        _event_aporte("b1", dedup_hash="hb"),
        _event_aporte("c1", dedup_hash="hc"),
    ]
    adapter = _FailingUpsertAdapter(aportes, fail_hash="ha")

    caplog.set_level(logging.ERROR, logger="scrapers.jobs.consolidation_job")
    summary = consolidate_entity_type(adapter, "Event", batch_size=10)

    # Los grupos sanos se consolidan; el que falla NO.
    assert adapter.consolidated_ids == {"b1", "c1"}
    assert ("Event", "ha") not in adapter.canonical
    assert ("Event", "hb") in adapter.canonical
    assert ("Event", "hc") in adapter.canonical
    assert summary["upserts"] == 2
    # El fallo se cuenta y se loguea (al menos una vez; el grupo fallido se
    # re-lee en la ronda siguiente y se reintenta, ver known follow-up del PR).
    assert summary["errors"] >= 1
    assert "ha" in caplog.text


def test_fallo_de_unico_grupo_cuenta_error_sin_propagar() -> None:
    # Un solo grupo que falla: no propaga, no consolida nada, cuenta el error.
    aportes = [_event_aporte("solo", dedup_hash="ha")]
    adapter = _FailingUpsertAdapter(aportes, fail_hash="ha")

    summary = consolidate_entity_type(adapter, "Event", batch_size=10)

    assert summary["errors"] == 1
    assert summary["upserts"] == 0
    assert summary["marked"] == 0
    assert adapter.consolidated_ids == set()
    assert adapter.canonical == {}


def test_person_no_admite_automerge() -> None:
    adapter = FakeInMemoryAdapter()
    try:
        consolidate_entity_type(adapter, "Person", batch_size=10)
    except ValueError as exc:
        assert "auto-merge" in str(exc)
    else:  # pragma: no cover - el else solo corre si NO se lanzo
        raise AssertionError("Person deberia rechazar auto-merge")


# --- Funciones puras --------------------------------------------------------

def test_group_by_dedup_hash_preserva_orden_y_descarta_sin_hash() -> None:
    recs: list[Record] = [
        {"id": "1", "dedup_hash": "a"},
        {"id": "2", "dedup_hash": "b"},
        {"id": "3", "dedup_hash": "a"},
        {"id": "4", "dedup_hash": None},
        {"id": "5"},
    ]
    groups = group_by_dedup_hash(recs)

    assert list(groups.keys()) == ["a", "b"]
    assert [r["id"] for r in groups["a"]] == ["1", "3"]
    assert [r["id"] for r in groups["b"]] == ["2"]


def test_pick_winner_desempate_determinista_por_created_at_y_source_id() -> None:
    # Mismo tier: gana el created_at mas antiguo; luego source_id lexicografico.
    group: list[Record] = [
        {"id": "z", "trust_tier": "B", "created_at": "2026-01-02", "source_id": "z"},
        {"id": "a", "trust_tier": "B", "created_at": "2026-01-01", "source_id": "b"},
        {"id": "b", "trust_tier": "B", "created_at": "2026-01-01", "source_id": "a"},
    ]
    winner = pick_winner(group)
    assert winner["id"] == "b"  # created_at mas antiguo + source_id menor

    # El orden de entrada no cambia el ganador (determinismo).
    winner_reversed = pick_winner(list(reversed(group)))
    assert winner_reversed["id"] == "b"


def test_pick_winner_tier_rank_inyectable() -> None:
    # Un mapeo inverso donde "D" es el mejor (menor rango). Como pick_winner elige
    # el MENOR rango, con este mapeo gana "d"; con el default (A=1) gana "a".
    def inverse_rank(tier: str) -> int:
        return {"D": 1, "C": 2, "B": 3, "A": 4}.get(tier.upper(), 99)

    group: list[Record] = [
        {"id": "a", "trust_tier": "A", "created_at": "x", "source_id": "x"},
        {"id": "d", "trust_tier": "D", "created_at": "x", "source_id": "x"},
    ]
    assert pick_winner(group, tier_rank=inverse_rank)["id"] == "d"
    assert pick_winner(group)["id"] == "a"  # default: A=1 gana (menor)


def test_default_tier_rank_menor_gana() -> None:
    # Decision del equipo (#82): A=1, B=2, C=3, D=4; un tier desconocido cae al
    # peor rango (mayor que D) para que nunca le gane a uno conocido.
    assert default_tier_rank("A") == 1
    assert default_tier_rank("a") == 1
    assert default_tier_rank("D") == 4
    assert default_tier_rank("Z") > default_tier_rank("D")
    assert default_tier_rank("") > default_tier_rank("D")


def test_canonical_from_winner_adjunta_metadata() -> None:
    winner: Record = {
        "id": "w1",
        "dedup_hash": "h",
        "payload": {"event_type": "flood"},
    }
    canonical = canonical_from_winner(winner)
    assert canonical["event_type"] == "flood"
    assert canonical["dedup_hash"] == "h"
    assert canonical["winner_aporte_id"] == "w1"
    assert "dedup_version" in canonical


def test_main_cli_dry_run_retorna_cero() -> None:
    # El PORT por defecto es un fake vacio: la CLI corre sin red y no falla.
    assert main(["--entity-type", "Event", "--dry-run"]) == 0


def test_main_cli_rechaza_batch_size_invalido() -> None:
    assert main(["--batch-size", "0"]) == 2


# --- Person dedup candidates (#92) -----------------------------------------

_EVENT_ID = "8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a"


def _person_aporte(
    aporte_id: str,
    person_record_id: str,
    name: str = "Juan Perez",
    cedula_hmac: str | None = "same",
    created_at: str = "2024-01-01T00:00:00Z",
    block_keys: list[str] | None = None,
    include_block_keys: bool = True,
    event_id: str = _EVENT_ID,
    phonetic_hash: str = "JN",
) -> dict[str, Any]:
    row = {
        "id": aporte_id,
        "person_record_id": person_record_id,
        "entity_type": "person",
        "full_name": name,
        "event_id": event_id,
        "cedula_hmac": cedula_hmac,
        "last_known_location": "Caracas, Distrito Capital",
        "phonetic_hash": phonetic_hash,
        "age_range": {"min": 25, "max": 35},
        "status": "missing",
        "created_at": created_at,
        "consolidated_at": None,
    }
    if include_block_keys:
        row["block_keys"] = block_keys or [f"ced:{event_id}:same"]
    return row


class _PersonTransport(httpx.BaseTransport):
    def __init__(
        self,
        batches: list[list[dict[str, Any]]],
        *,
        existing: list[dict[str, Any]] | None = None,
        post_status: int = 201,
        patch_candidate_status: int = 204,
        mark_status: int = 204,
    ) -> None:
        self.batches = batches
        self.existing = existing or []
        self.post_status = post_status
        self.patch_candidate_status = patch_candidate_status
        self.mark_status = mark_status
        self.get_urls: list[str] = []
        self.post_bodies: list[Any] = []
        self.patch_urls: list[str] = []
        self._batch_idx = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/rest/v1/aportes":
            self.get_urls.append(str(request.url))
            batch = self.batches[self._batch_idx] if self._batch_idx < len(self.batches) else []
            self._batch_idx += 1
            return httpx.Response(200, json=batch)
        if request.method == "GET" and path == "/rest/v1/dedup_candidates":
            self.get_urls.append(str(request.url))
            return httpx.Response(200, json=self.existing)
        if request.method == "POST" and path == "/rest/v1/dedup_candidates":
            self.post_bodies.append(json.loads(request.content))
            return httpx.Response(self.post_status)
        if request.method == "PATCH" and path == "/rest/v1/dedup_candidates":
            self.patch_urls.append(str(request.url))
            return httpx.Response(self.patch_candidate_status)
        if request.method == "PATCH" and path == "/rest/v1/aportes":
            self.patch_urls.append(str(request.url))
            return httpx.Response(self.mark_status)
        return httpx.Response(404, json={"error": "not found"})


def _person_client(transport: _PersonTransport) -> httpx.Client:
    return httpx.Client(base_url="https://test.supabase.co", transport=transport)


def _person_config(**overrides: Any) -> PersonConsolidationConfig:
    return PersonConsolidationConfig(
        supabase_url="https://test.supabase.co",
        publishable_key="test-publishable-key",
        consolidation_jwt="test-consolidation-jwt",
        batch_size=int(overrides.get("batch_size", 500)),
        threshold=float(overrides.get("threshold", 0.85)),
    )


def test_person_candidate_payload_matches_master_schema() -> None:
    rows = [
        _person_aporte("a1", "person-1"),
        _person_aporte("a2", "person-2"),
    ]
    transport = _PersonTransport([rows])
    result = run_person_consolidation(_person_config(), client=_person_client(transport))

    assert result.errors == []
    assert result.candidates_inserted_or_updated == 1
    assert len(transport.post_bodies) == 1
    assert isinstance(transport.post_bodies[0], list)
    body = transport.post_bodies[0][0]
    assert body["event_id"] == _EVENT_ID
    assert body["left_aporte_id"] == "a1"
    assert body["right_aporte_id"] == "a2"
    assert body["blocking_key"] == f"ced:{_EVENT_ID}:same"
    assert body["priority"] == 1
    assert body["touches_gold"] is False
    assert body["decision"] == "pending"
    assert "left_person" not in body
    assert "right_person" not in body


def test_person_cursor_includes_same_timestamp_higher_id() -> None:
    first = [_person_aporte("0001", "person-1")]
    second = [_person_aporte("0002", "person-2", name="Juan Perez Gonzalez")]
    transport = _PersonTransport([first, second, []])
    result = run_person_consolidation(
        _person_config(batch_size=1, threshold=0.99),
        client=_person_client(transport),
    )

    assert result.records_read == 2
    second_fetch = [url for url in transport.get_urls if "/rest/v1/aportes" in url][1]
    assert "or=" in second_fetch
    assert "created_at.gt.2024-01-01T00%3A00%3A00Z" in second_fetch
    assert "and%28created_at.eq.2024-01-01T00%3A00%3A00Z%2Cid.gt.0001%29" in second_fetch


def test_person_upsert_error_does_not_mark_consolidated() -> None:
    rows = [
        _person_aporte("a1", "person-1"),
        _person_aporte("a2", "person-2"),
    ]
    transport = _PersonTransport([rows], post_status=500)
    result = run_person_consolidation(_person_config(), client=_person_client(transport))

    assert result.upsert_errors == 1
    assert result.errors
    assert not any("/rest/v1/aportes" in url for url in transport.patch_urls)


def test_person_batch_lookup_does_not_get_per_candidate() -> None:
    rows = [
        _person_aporte("a1", "person-1"),
        _person_aporte("a2", "person-2"),
        _person_aporte("a3", "person-3"),
    ]
    transport = _PersonTransport([rows])
    result = run_person_consolidation(_person_config(), client=_person_client(transport))

    dedup_gets = [url for url in transport.get_urls if "/rest/v1/dedup_candidates" in url]
    assert result.candidates_inserted_or_updated == 3
    assert len(dedup_gets) == 1
    assert len(transport.post_bodies) == 1
    assert len(transport.post_bodies[0]) == 3


def test_person_existing_candidate_is_idempotent_update() -> None:
    rows = [
        _person_aporte("a1", "person-1"),
        _person_aporte("a2", "person-2"),
    ]
    transport = _PersonTransport(
        [rows],
        existing=[
            {
                "candidate_id": "cand-1",
                "left_aporte_id": "a1",
                "right_aporte_id": "a2",
                "blocking_key": f"ced:{_EVENT_ID}:same",
            }
        ],
    )
    result = run_person_consolidation(_person_config(), client=_person_client(transport))

    assert result.upsert_errors == 0
    assert result.duplicates_skipped == 1
    assert result.candidates_inserted_or_updated == 1
    assert transport.post_bodies == []
    assert any("candidate_id=eq.cand-1" in url for url in transport.patch_urls)


def test_person_mark_consolidated_error_is_reported() -> None:
    rows = [
        _person_aporte("a1", "person-1"),
        _person_aporte("a2", "person-2"),
    ]
    transport = _PersonTransport([rows], mark_status=500)
    result = run_person_consolidation(_person_config(), client=_person_client(transport))

    assert result.mark_errors == 1
    assert any(error.startswith("mark_error") for error in result.errors)


@pytest.mark.parametrize("missing_field", ["event_id", "blocking_key"])
def test_person_invalid_candidate_payload_is_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
    missing_field: str,
) -> None:
    rows = [
        _person_aporte("bad-1", "person-1"),
        _person_aporte("bad-2", "person-2"),
        _person_aporte("ok-1", "person-3"),
        _person_aporte("ok-2", "person-4"),
    ]
    invalid = {
        "event_id": _EVENT_ID,
        "left_aporte_id": "person-1",
        "right_aporte_id": "person-2",
        "blocking_key": "bad:block",
        "source_record_ids": ["bad-1", "bad-2"],
        "score": 0.95,
        "reasons": {"nombre": 0.4},
        "priority": 1,
    }
    del invalid[missing_field]
    valid = {
        "event_id": _EVENT_ID,
        "left_aporte_id": "person-3",
        "right_aporte_id": "person-4",
        "blocking_key": "ok:block",
        "source_record_ids": ["ok-1", "ok-2"],
        "score": 0.95,
        "reasons": {"nombre": 0.4},
        "priority": 1,
    }

    monkeypatch.setattr(consolidation_job, "find_candidates", lambda *_: [invalid, valid])
    transport = _PersonTransport([rows])
    result = run_person_consolidation(_person_config(), client=_person_client(transport))

    assert result.upsert_errors == 1
    assert any("candidate_payload_error" in error for error in result.errors)
    assert result.candidates_inserted_or_updated == 1
    assert len(transport.post_bodies[0]) == 1
    mark_urls = [url for url in transport.patch_urls if "/rest/v1/aportes" in url]
    assert mark_urls
    assert "ok-1" in mark_urls[0]
    assert "ok-2" in mark_urls[0]
    assert "bad-1" not in mark_urls[0]
    assert "bad-2" not in mark_urls[0]


def test_person_fatal_upsert_error_aborts_without_marking() -> None:
    rows = [
        _person_aporte("a1", "person-1"),
        _person_aporte("a2", "person-2"),
    ]
    transport = _PersonTransport([rows], post_status=401)
    result = run_person_consolidation(_person_config(), client=_person_client(transport))

    assert result.upsert_errors == 1
    assert result.errors
    assert not any("/rest/v1/aportes" in url for url in transport.patch_urls)


def test_person_fallback_without_block_keys_generates_expected_keys() -> None:
    rows = [
        _person_aporte(
            "a1",
            "person-1",
            include_block_keys=False,
            cedula_hmac="same",
            phonetic_hash="JN",
        ),
        _person_aporte(
            "a2",
            "person-2",
            include_block_keys=False,
            cedula_hmac="same",
            phonetic_hash="JN",
        ),
    ]
    transport = _PersonTransport([rows])
    result = run_person_consolidation(_person_config(), client=_person_client(transport))

    assert result.candidates_inserted_or_updated == 2
    payloads = transport.post_bodies[0]
    assert {payload["blocking_key"] for payload in payloads} == {
        f"ced:{_EVENT_ID}:same",
        f"phon:{_EVENT_ID}:JN",
    }


def test_person_without_block_keys_and_event_id_generates_no_invalid_keys() -> None:
    rows = [
        _person_aporte("a1", "person-1", include_block_keys=False, event_id=""),
        _person_aporte("a2", "person-2", include_block_keys=False, event_id=""),
    ]
    transport = _PersonTransport([rows])
    result = run_person_consolidation(_person_config(), client=_person_client(transport))

    assert result.blocks == 0
    assert result.candidates_inserted_or_updated == 0
    assert transport.post_bodies == []


# --- Person config from_env (analogo a Event/Acopio, review de mayerlim) -----

_PERSON_URL = "https://proj.supabase.co"
_PERSON_PUBLISHABLE_KEY = "publishable-key-xyz"
_PERSON_CONSOLIDATION_JWT = "consolidation-jwt-abc"


def _clear_person_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_CONSOLIDATION_JWT", raising=False)


def test_person_from_env_sin_variables_es_dry_run_info(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Ninguna env seteada (dry-run intencional en dev local): INFO, NO ERROR.
    _clear_person_env(monkeypatch)
    caplog.set_level(logging.DEBUG, logger="scrapers.jobs.consolidation_job")
    assert PersonConsolidationConfig.from_env() is None
    records = [
        r for r in caplog.records if r.name == "scrapers.jobs.consolidation_job"
    ]
    assert records
    assert all(r.levelno < logging.ERROR for r in records)
    assert any(r.levelno == logging.INFO for r in records)


def test_person_from_env_config_parcial_es_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Alguna env seteada y otra faltante (config parcial en prod): ERROR con las
    # faltantes; devuelve None (dry-run) sin abortar.
    _clear_person_env(monkeypatch)
    monkeypatch.setenv("SUPABASE_URL", _PERSON_URL)
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _PERSON_PUBLISHABLE_KEY)
    # falta SUPABASE_CONSOLIDATION_JWT => config parcial
    caplog.set_level(logging.DEBUG, logger="scrapers.jobs.consolidation_job")
    assert PersonConsolidationConfig.from_env() is None
    errors = [
        r for r in caplog.records
        if r.name == "scrapers.jobs.consolidation_job" and r.levelno == logging.ERROR
    ]
    assert errors
    assert any("SUPABASE_CONSOLIDATION_JWT" in r.getMessage() for r in errors)


def test_person_from_env_ok_con_todas_las_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_person_env(monkeypatch)
    monkeypatch.setenv("SUPABASE_URL", _PERSON_URL + "/")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _PERSON_PUBLISHABLE_KEY)
    monkeypatch.setenv("SUPABASE_CONSOLIDATION_JWT", _PERSON_CONSOLIDATION_JWT)
    config = PersonConsolidationConfig.from_env()
    assert config is not None
    assert config.supabase_url == _PERSON_URL  # trailing slash removido
    assert config.publishable_key == _PERSON_PUBLISHABLE_KEY
    assert config.consolidation_jwt == _PERSON_CONSOLIDATION_JWT


def test_person_from_env_rechaza_http_plano(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Media #2: con todas las vars pero URL http:// (no https), la config Person
    # debe entrar en dry-run (None) para NO mandar apikey/JWT/PII en claro,
    # igual que SupabaseConsolidationConfig.from_env del adapter Event/Acopio.
    _clear_person_env(monkeypatch)
    monkeypatch.setenv("SUPABASE_URL", "http://proj.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", _PERSON_PUBLISHABLE_KEY)
    monkeypatch.setenv("SUPABASE_CONSOLIDATION_JWT", _PERSON_CONSOLIDATION_JWT)
    caplog.set_level(logging.DEBUG, logger="scrapers.jobs.consolidation_job")
    assert PersonConsolidationConfig.from_env() is None
    errors = [
        r for r in caplog.records
        if r.name == "scrapers.jobs.consolidation_job" and r.levelno == logging.ERROR
    ]
    assert errors
    assert any("https" in r.getMessage().lower() for r in errors)


def test_person_from_config_headers_apikey_publishable_bearer_jwt() -> None:
    """El adapter Person manda apikey=publishable y Bearer=JWT (NO la misma key).

    Verifica el patron #200 en el path Person (analogo al test de Event/Acopio):
    from_config arma headers distintos por credencial (apikey != Bearer), sin
    service_role. Se lee el default de headers del httpx.Client, sin abrir red.
    """
    config = PersonConsolidationConfig(
        supabase_url=_PERSON_URL,
        publishable_key=_PERSON_PUBLISHABLE_KEY,
        consolidation_jwt=_PERSON_CONSOLIDATION_JWT,
    )
    adapter = SupabasePersonDedupAdapter.from_config(config)
    try:
        headers = adapter._client.headers
        assert headers["apikey"] == _PERSON_PUBLISHABLE_KEY
        assert headers["Authorization"] == f"Bearer {_PERSON_CONSOLIDATION_JWT}"
        # blindaje anti falso-verde: apikey y Bearer NO son el mismo valor.
        assert headers["apikey"] != _PERSON_CONSOLIDATION_JWT
    finally:
        adapter.close()


def test_person_run_cierra_client_propio(monkeypatch: pytest.MonkeyPatch) -> None:
    # Cuando run_person_consolidation arma el adapter (client=None), es dueno del
    # httpx.Client y debe cerrarlo al terminar (fix de cierre, review de mayerlim).
    closed: list[bool] = []

    def _spy_close(self: SupabasePersonDedupAdapter) -> None:
        closed.append(True)
        self._client.close()

    # fetch_batch vacio => corta al primer batch sin red real.
    monkeypatch.setattr(
        SupabasePersonDedupAdapter,
        "fetch_batch",
        lambda self, config, cursor: [],
    )
    monkeypatch.setattr(SupabasePersonDedupAdapter, "close", _spy_close)

    run_person_consolidation(_person_config())

    assert closed == [True]


def test_person_run_no_cierra_client_inyectado() -> None:
    # Si el caller inyecta el client (tests), run_person_consolidation NO debe
    # cerrarlo: el caller es dueno de su ciclo de vida.
    transport = _PersonTransport([[]])
    client = _person_client(transport)
    run_person_consolidation(_person_config(), client=client)
    # El client sigue usable (no se cerro): un GET no lanza ClosedResourceError.
    assert not client.is_closed
    client.close()
