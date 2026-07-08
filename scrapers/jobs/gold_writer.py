"""GoldWriter: persiste clusters resueltos en las tablas gold.

Resuelve el criterio de Stage 2 (#266): dado un cluster de aportes y su
winner (elegido por `pick_winner`), escribe:

  - gold_entities  — UPSERT idempotente por canonical_aporte_id (winner).
  - gold_members   — UPSERT idempotente por (gold_id, aporte_id).
  - gold_history   — INSERT append-only con el evento (merge/passthrough).

Huerfanos (cluster de 1 miembro): se proyectan como gold_entity con 1
miembro y accion "passthrough", sin necesitar un candidato previo.

Acceso a datos: todo pasa por `GoldDataPort` (Protocol, definido en ports.py).
El adapter de produccion (Supabase PostgREST) queda pendiente; los tests usan
`FakeGoldAdapter` (sin red ni DB), tambien definido en ports.py.
"""

from __future__ import annotations

import logging

from scrapers.adapters._shared import now_utc
from scrapers.jobs.ports import GoldDataPort, Record

_LOGGER = logging.getLogger(__name__)

_ACTION_MERGE = "merge"
_ACTION_PASSTHROUGH = "passthrough"
_ACTOR_KIND_SYSTEM = "system"

# Mapeo de nombre interno -> slug del enum de DB (gold_entities.entity_type).
# El enum del backend acepta: "event" | "acopio" | "person" (minusculas).
_ENTITY_TYPE_SLUG: dict[str, str] = {
    "Event": "event",
    "AcopioCenter": "acopio",
    "Person": "person",
}


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

        internal_type = str(winner.get("entity_type") or "")
        entity_type_slug = _ENTITY_TYPE_SLUG.get(internal_type, internal_type.lower())
        raw_score = winner.get("confidence_score")
        confidence_score = (
            float(raw_score)
            if not isinstance(raw_score, bool) and isinstance(raw_score, (int, float))
            else 0.0
        )
        dedup_hash = winner.get("dedup_hash")
        now = now_utc()

        entity: Record = {
            "canonical_aporte_id": winner_id,
            "entity_type": entity_type_slug,
            "confidence_score": confidence_score,
            "verification_status": "unverified",
            "last_deduplicated_at": now,
            "updated_at": now,
        }
        gold_id = self._port.upsert_gold_entity(entity)

        valid_aporte_ids: list[str] = []
        for rec in cluster:
            aporte_id = str(rec.get("id") or "")
            if not aporte_id:
                _LOGGER.warning("write_cluster: aporte sin id en cluster, omitiendo")
                continue
            valid_aporte_ids.append(aporte_id)
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
                "aporte_ids": valid_aporte_ids,
                "winner_aporte_id": winner_id,
                "dedup_hash": dedup_hash,
            },
        }
        self._port.insert_gold_history(history_event)

        _LOGGER.info(
            "gold_writer cluster_written entity_type=%s gold_id=%s "
            "cluster_size=%d action=%s winner_id=%s via_candidate=%s",
            entity_type_slug,
            gold_id,
            len(cluster),
            action,
            winner_id,
            via_candidate,
        )
        return gold_id
