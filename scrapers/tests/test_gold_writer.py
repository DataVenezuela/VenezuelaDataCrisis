"""Tests offline del GoldWriter (#266).

Cubre los criterios de aceptacion del issue:
  - FakeGoldAdapter satisface el protocolo GoldDataPort.
  - Cluster de 1 miembro (huerfano): passthrough.
  - Cluster de 2 miembros: merge.
  - Cluster de N miembros (5): merge con todos los miembros registrados.
  - Idempotencia: re-correr sobre el mismo cluster no duplica gold_entities
    ni gold_members (gold_history es append-only, cada llamada agrega 1 fila).
  - Cluster vacio lanza ValueError.
  - Winner sin id lanza ValueError.

Datos ficticios; ningun dato real de personas.
"""

from __future__ import annotations

import pytest

from scrapers.jobs.gold_writer import (
    FakeGoldAdapter,
    GoldDataPort,
    GoldWriter,
    _ACTION_MERGE,
    _ACTION_PASSTHROUGH,
    _ACTOR_KIND_SYSTEM,
)
from scrapers.jobs.ports import Record


def _aporte(
    aporte_id: str,
    entity_type: str = "Event",
    dedup_hash: str = "hash-demo",
    trust_tier: str = "D",
    confidence_score: float = 0.75,
) -> Record:
    return {
        "id": aporte_id,
        "entity_type": entity_type,
        "dedup_hash": dedup_hash,
        "trust_tier": trust_tier,
        "confidence_score": confidence_score,
        "source_id": "fuente-ficticia-1",
        "created_at": "2026-07-01T10:00:00Z",
        "payload": {"description": "Evento ficticio de prueba"},
    }


# ---------------------------------------------------------------------------
# Contrato del protocolo
# ---------------------------------------------------------------------------


def test_fake_adapter_satisface_el_protocolo() -> None:
    adapter = FakeGoldAdapter()
    assert isinstance(adapter, GoldDataPort)


# ---------------------------------------------------------------------------
# Cluster de 1 miembro (huerfano — passthrough)
# ---------------------------------------------------------------------------


def test_cluster_un_miembro_passthrough() -> None:
    adapter = FakeGoldAdapter()
    writer = GoldWriter(adapter)
    aporte = _aporte("aporte-1")

    gold_id = writer.write_cluster([aporte], winner=aporte)

    assert isinstance(gold_id, str)
    assert gold_id != ""

    # Una sola entidad gold creada.
    assert len(adapter.entities) == 1
    entity = adapter.entities["aporte-1"]
    assert entity["gold_id"] == gold_id
    assert entity["canonical_aporte_id"] == "aporte-1"
    assert entity["entity_type"] == "Event"
    assert entity["verification_status"] == "unverified"

    # Un miembro registrado.
    assert adapter.members == {(gold_id, "aporte-1")}

    # Historia registrada con accion passthrough.
    assert len(adapter.history) == 1
    hist = adapter.history[0]
    assert hist["gold_id"] == gold_id
    assert hist["action"] == _ACTION_PASSTHROUGH
    assert hist["actor_kind"] == _ACTOR_KIND_SYSTEM
    assert hist["detail"]["cluster_size"] == 1


# ---------------------------------------------------------------------------
# Cluster de 2 miembros (merge)
# ---------------------------------------------------------------------------


def test_cluster_dos_miembros_merge() -> None:
    adapter = FakeGoldAdapter()
    writer = GoldWriter(adapter)
    a1 = _aporte("aporte-1", trust_tier="A", confidence_score=0.9)
    a2 = _aporte("aporte-2", trust_tier="D", confidence_score=0.5)
    cluster = [a1, a2]
    winner = a1

    gold_id = writer.write_cluster(cluster, winner=winner)

    assert len(adapter.entities) == 1
    assert adapter.entities["aporte-1"]["canonical_aporte_id"] == "aporte-1"

    assert adapter.members == {(gold_id, "aporte-1"), (gold_id, "aporte-2")}

    assert len(adapter.history) == 1
    hist = adapter.history[0]
    assert hist["action"] == _ACTION_MERGE
    assert hist["detail"]["cluster_size"] == 2
    assert set(hist["detail"]["aporte_ids"]) == {"aporte-1", "aporte-2"}
    assert hist["detail"]["winner_aporte_id"] == "aporte-1"


# ---------------------------------------------------------------------------
# Cluster de N miembros (5)
# ---------------------------------------------------------------------------


def test_cluster_n_miembros_merge() -> None:
    adapter = FakeGoldAdapter()
    writer = GoldWriter(adapter)
    aportes = [_aporte(f"aporte-{i}") for i in range(5)]
    winner = aportes[0]

    gold_id = writer.write_cluster(aportes, winner=winner)

    assert len(adapter.entities) == 1
    assert len(adapter.members) == 5
    for i in range(5):
        assert (gold_id, f"aporte-{i}") in adapter.members

    hist = adapter.history[0]
    assert hist["action"] == _ACTION_MERGE
    assert hist["detail"]["cluster_size"] == 5


# ---------------------------------------------------------------------------
# Idempotencia
# ---------------------------------------------------------------------------


def test_idempotencia_no_duplica_entities_ni_members() -> None:
    adapter = FakeGoldAdapter()
    writer = GoldWriter(adapter)
    a1 = _aporte("aporte-1", trust_tier="A")
    a2 = _aporte("aporte-2", trust_tier="B")
    cluster = [a1, a2]
    winner = a1

    gold_id_first = writer.write_cluster(cluster, winner=winner)
    gold_id_second = writer.write_cluster(cluster, winner=winner)

    # Mismo gold_id en ambas llamadas.
    assert gold_id_first == gold_id_second

    # gold_entities: solo 1 fila (sin duplicados).
    assert len(adapter.entities) == 1
    assert adapter.upsert_entity_calls == 2  # se llamó dos veces, pero upsert

    # gold_members: solo 2 pares únicos (sin duplicados).
    assert len(adapter.members) == 2
    assert adapter.upsert_member_calls == 4  # 2 aportes × 2 corridas (no-op segunda vez)

    # gold_history: 2 entradas (append-only, una por corrida — comportamiento intencional).
    assert len(adapter.history) == 2


def test_idempotencia_via_candidate_registrado() -> None:
    adapter = FakeGoldAdapter()
    writer = GoldWriter(adapter)
    a1 = _aporte("aporte-1")
    a2 = _aporte("aporte-2")

    candidate_id = "cand-uuid-123"
    writer.write_cluster([a1, a2], winner=a1, via_candidate=candidate_id)

    hist = adapter.history[0]
    assert hist["via_candidate"] == candidate_id


# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------


def test_cluster_vacio_lanza_error() -> None:
    adapter = FakeGoldAdapter()
    writer = GoldWriter(adapter)
    winner = _aporte("aporte-1")

    with pytest.raises(ValueError, match="cluster no puede estar vacio"):
        writer.write_cluster([], winner=winner)


def test_winner_sin_id_lanza_error() -> None:
    adapter = FakeGoldAdapter()
    writer = GoldWriter(adapter)
    a1 = _aporte("aporte-1")
    bad_winner: Record = {"entity_type": "Event"}

    with pytest.raises(ValueError, match="winner debe tener un campo 'id'"):
        writer.write_cluster([a1], winner=bad_winner)


# ---------------------------------------------------------------------------
# Multiples clusters independientes
# ---------------------------------------------------------------------------


def test_multiples_clusters_crean_entidades_separadas() -> None:
    adapter = FakeGoldAdapter()
    writer = GoldWriter(adapter)
    a1 = _aporte("aporte-1", dedup_hash="hash-A")
    a2 = _aporte("aporte-2", dedup_hash="hash-B")

    gold_id_a = writer.write_cluster([a1], winner=a1)
    gold_id_b = writer.write_cluster([a2], winner=a2)

    assert gold_id_a != gold_id_b
    assert len(adapter.entities) == 2
    assert len(adapter.members) == 2
    assert len(adapter.history) == 2
