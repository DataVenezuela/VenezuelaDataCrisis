from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from scrapers.dedup.fingerprint import build_entity_fingerprint
from scrapers.models import AcopioCenter, Event


DEFAULT_DEDUP_DB_PATH = Path(__file__).resolve().parents[1] / "runtime_output" / "dedup_state.db"

# El issue #17 y los modelos tipados actuales usan A/B/C/D. La documentacion
# larga tambien menciona 1/2/3 para DB/API; no convertimos aqui para no mezclar
# contratos de ingesta con la logica interna de scrapers.
_TIER_RANK = {
    "A": 4,
    "B": 3,
    "C": 2,
    "D": 1,
}
_LOGGER = logging.getLogger(__name__)


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_fingerprints (
            fingerprint TEXT PRIMARY KEY,
            first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _resolve_db_path(db_path: str | Path | None) -> Path:
    if db_path is None:
        return DEFAULT_DEDUP_DB_PATH
    return Path(db_path)


def deduplicate_by_fingerprint(
    items: list[dict],
    db_path: str | Path | None = None,
) -> tuple[list[dict], int]:
    database_path = _resolve_db_path(db_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    output: list[dict] = []
    duplicates = 0

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=5000")
        _ensure_schema(connection)

        for item in items:
            fingerprint = item.get("fingerprint")
            if not fingerprint:
                output.append(item)
                continue

            cursor = connection.execute(
                "INSERT OR IGNORE INTO seen_fingerprints (fingerprint) VALUES (?)",
                (fingerprint,),
            )
            if cursor.rowcount == 1:
                output.append(item)
            else:
                duplicates += 1

    return output, duplicates


def deduplicate_typed_entities(
    entities: list[Event | AcopioCenter],
    logger: logging.Logger | None = None,
) -> tuple[list[Event | AcopioCenter], int]:
    """Deduplicate typed Event/AcopioCenter records by content fingerprint.

    This intentionally excludes Person records because person deduplication is
    sensitive and must go through candidate review. When two typed entities
    share a fingerprint, the entity with the higher trust_tier wins. Discards
    are logged with source_id when present, falling back to the current
    model field `fuente`.
    """
    active_logger = logger or _LOGGER
    output: list[Event | AcopioCenter] = []
    seen: dict[str, tuple[int, Event | AcopioCenter]] = {}
    duplicates = 0

    for entity in entities:
        fingerprint = build_entity_fingerprint(entity)
        existing = seen.get(fingerprint)

        if existing is None:
            seen[fingerprint] = (len(output), entity)
            output.append(entity)
            continue

        duplicates += 1
        existing_index, existing_entity = existing
        winner = _choose_winner(existing_entity, entity)
        loser = entity if winner is existing_entity else existing_entity

        if winner is not existing_entity:
            output[existing_index] = winner
            seen[fingerprint] = (existing_index, winner)

        _log_discard(active_logger, fingerprint, winner, loser)

    return output, duplicates


def _choose_winner(
    current: Event | AcopioCenter,
    candidate: Event | AcopioCenter,
) -> Event | AcopioCenter:
    current_rank = _trust_rank(current)
    candidate_rank = _trust_rank(candidate)
    if candidate_rank > current_rank:
        return candidate
    return current


def _trust_rank(entity: Event | AcopioCenter) -> int:
    tier = str(getattr(entity, "trust_tier", "D") or "D").upper()
    return _TIER_RANK.get(tier, _TIER_RANK["D"])


def _log_discard(
    logger: logging.Logger,
    fingerprint: str,
    winner: Event | AcopioCenter,
    loser: Event | AcopioCenter,
) -> None:
    logger.info(
        "dedup_discard fingerprint=%s kept_source_id=%s discarded_source_id=%s winning_tier=%s",
        fingerprint,
        _source_id(winner),
        _source_id(loser),
        getattr(winner, "trust_tier", "D"),
    )


def _source_id(entity: Event | AcopioCenter) -> str:
    # Los modelos actuales exponen `fuente`; mantenemos compatibilidad futura
    # con `source_id` sin inventar ese campo en el schema tipado.
    return str(getattr(entity, "source_id", None) or getattr(entity, "fuente", "unknown"))
