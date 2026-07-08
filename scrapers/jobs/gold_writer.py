"""GoldWriter: persiste clusters resueltos en las tablas gold.

Resuelve el criterio de Stage 2 (#266): dado un cluster de aportes y su
winner (elegido por `pick_winner`), escribe:

  - gold_entities  — UPSERT idempotente por canonical_aporte_id (winner).
  - gold_members   — UPSERT idempotente por (gold_id, aporte_id).
  - gold_history   — INSERT append-only con el evento (merge/passthrough).

Huerfanos (cluster de 1 miembro): se proyectan como gold_entity con 1
miembro y accion "passthrough", sin necesitar un candidato previo.

Acceso a datos: todo pasa por `GoldDataPort` (Protocol). El adapter de
produccion (Supabase PostgREST) queda pendiente; los tests usan
`FakeGoldAdapter` (sin red ni DB).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from scrapers.jobs.ports import Record

_LOGGER = logging.getLogger(__name__)

_ACTION_MERGE = "merge"
_ACTION_PASSTHROUGH = "passthrough"
_ACTOR_KIND_SYSTEM = "system"


@runtime_checkable
class GoldDataPort(Protocol):
    """Contrato de I/O para el writer de tablas gold."""

    def upsert_gold_entity(self, entity: Record) -> str:
        """UPSERT en gold_entities por canonical_aporte_id.

        Si ya existe una fila con ese canonical_aporte_id, la actualiza;
        si no, la crea. Devuelve el gold_id (UUID str) de la fila resultante.
        """
        ...

    def upsert_gold_member(
        self,
        gold_id: str,
        aporte_id: str,
        via_candidate: str | None = None,
    ) -> None:
        """UPSERT en gold_members por (gold_id, aporte_id).

        Re-upsertar la misma pareja es no-op (idempotente).
        """
        ...

    def insert_gold_history(self, event: Record) -> None:
        """INSERT en gold_history (append-only, log inmutable)."""
        ...

    def close(self) -> None:
        """Libera recursos (no-op en el fake)."""
        ...


class FakeGoldAdapter:
    """Implementacion en memoria de GoldDataPort para tests offline.

    - entities: canonical_aporte_id -> fila completa (incluye gold_id generado).
    - members:  set de (gold_id, aporte_id) ya insertadas.
    - history:  lista ordenada de eventos (append-only).
    """

    def __init__(self) -> None:
        self.entities: dict[str, Record] = {}
        self.members: set[tuple[str, str]] = set()
        self.history: list[Record] = []

        self.upsert_entity_calls: int = 0
        self.upsert_member_calls: int = 0
        self.insert_history_calls: int = 0

    def upsert_gold_entity(self, entity: Record) -> str:
        canonical_aporte_id = str(entity.get("canonical_aporte_id") or "")
        if not canonical_aporte_id:
            raise ValueError("upsert_gold_entity: canonical_aporte_id requerido")

        self.upsert_entity_calls += 1
        existing = self.entities.get(canonical_aporte_id)
        if existing is not None:
            existing.update(entity)
            return str(existing["gold_id"])

        gold_id = str(uuid.uuid4())
        row: Record = dict(entity)
        row["gold_id"] = gold_id
        self.entities[canonical_aporte_id] = row
        return gold_id

    def upsert_gold_member(
        self,
        gold_id: str,
        aporte_id: str,
        via_candidate: str | None = None,
    ) -> None:
        self.upsert_member_calls += 1
        key = (gold_id, aporte_id)
        if key in self.members:
            return
        self.members.add(key)

    def insert_gold_history(self, event: Record) -> None:
        self.insert_history_calls += 1
        self.history.append(dict(event))

    def close(self) -> None:
        return None


class GoldWriter:
    """Escribe un cluster resuelto en las tres tablas gold.

    Uso:
        writer = GoldWriter(port)
        gold_id = writer.write_cluster(cluster, winner)

    El caller (consolidation_job u otro orquestador) es responsable de:
      - Haber elegido el winner (via `pick_winner`).
      - Cerrar el port al terminar (`port.close()`).
    """

    def __init__(self, port: GoldDataPort) -> None:
        self._port = port

    def write_cluster(
        self,
        cluster: list[Record],
        winner: Record,
        via_candidate: str | None = None,
    ) -> str:
        """Persiste el cluster en gold_entities, gold_members y gold_history.

        Devuelve el gold_id de la entidad gold resultante.

        - Si cluster tiene 1 miembro (huerfano), accion = "passthrough".
        - Si tiene 2+ miembros (merge real), accion = "merge".
        - El writer es idempotente: re-llamar con el mismo cluster y winner
          no duplica filas en gold_entities ni en gold_members. gold_history
          es append-only y registra cada llamada (comportamiento intencional
          para trazabilidad de auditorias).
        """
        if not cluster:
            raise ValueError("write_cluster: cluster no puede estar vacio")

        winner_id = str(winner.get("id") or "")
        if not winner_id:
            raise ValueError("write_cluster: winner debe tener un campo 'id'")

        entity_type = str(winner.get("entity_type") or "")
        confidence_score = winner.get("confidence_score")
        dedup_hash = winner.get("dedup_hash")
        now = datetime.now(timezone.utc).isoformat()

        entity: Record = {
            "canonical_aporte_id": winner_id,
            "entity_type": entity_type,
            "confidence_score": confidence_score if isinstance(confidence_score, (int, float)) else 0.0,
            "verification_status": "unverified",
            "last_deduplicated_at": now,
            "updated_at": now,
        }
        gold_id = self._port.upsert_gold_entity(entity)

        aporte_ids = [str(rec.get("id") or "") for rec in cluster]
        for aporte_id in aporte_ids:
            if not aporte_id:
                _LOGGER.warning("write_cluster: aporte sin id en cluster, omitiendo")
                continue
            self._port.upsert_gold_member(gold_id, aporte_id, via_candidate)

        action = _ACTION_PASSTHROUGH if len(cluster) == 1 else _ACTION_MERGE
        history_event: Record = {
            "gold_id": gold_id,
            "action": action,
            "actor_kind": _ACTOR_KIND_SYSTEM,
            "actor_id": None,
            "via_candidate": via_candidate,
            "at": now,
            "detail": {
                "cluster_size": len(cluster),
                "aporte_ids": aporte_ids,
                "winner_aporte_id": winner_id,
                "dedup_hash": dedup_hash,
            },
        }
        self._port.insert_gold_history(history_event)

        _LOGGER.info(
            "gold_writer cluster_written entity_type=%s gold_id=%s "
            "cluster_size=%d action=%s winner_id=%s via_candidate=%s",
            entity_type,
            gold_id,
            len(cluster),
            action,
            winner_id,
            via_candidate,
        )
        return gold_id
