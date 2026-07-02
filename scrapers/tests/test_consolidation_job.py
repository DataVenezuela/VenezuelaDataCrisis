"""Tests offline del job de consolidacion (#91): Event/AcopioCenter.

Todo corre contra `FakeInMemoryAdapter`, sin red ni DB real. Cubre los
criterios de aceptacion de #91: 3 aportes con el mismo dedup_hash producen 1
fila canonica y los 3 quedan marcados; gana el de mayor tier; re-correr es
idempotente; --dry-run no muta el fake; interrupcion+reintento procesa solo lo
pendiente.
"""

from __future__ import annotations

import logging

from scrapers.jobs.consolidation_job import (
    canonical_from_winner,
    consolidate_entity_type,
    default_tier_rank,
    group_by_dedup_hash,
    main,
    pick_winner,
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


def test_gana_el_de_mayor_tier() -> None:
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
    # Un mapeo inverso: "D" vale mas que "A".
    def inverse_rank(tier: str) -> int:
        return {"D": 4, "C": 3, "B": 2, "A": 1}.get(tier.upper(), 0)

    group: list[Record] = [
        {"id": "a", "trust_tier": "A", "created_at": "x", "source_id": "x"},
        {"id": "d", "trust_tier": "D", "created_at": "x", "source_id": "x"},
    ]
    assert pick_winner(group, tier_rank=inverse_rank)["id"] == "d"
    assert pick_winner(group)["id"] == "a"  # default: A gana


def test_default_tier_rank_desconocido_es_cero() -> None:
    assert default_tier_rank("A") == 4
    assert default_tier_rank("a") == 4
    assert default_tier_rank("Z") == 0
    assert default_tier_rank("") == 0


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
