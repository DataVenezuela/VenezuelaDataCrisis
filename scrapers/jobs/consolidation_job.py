"""Consolidation job: Person dedup → candidates.

Reads person records from ``aportes`` in Supabase, groups by block keys,
compares pairs with multi-field scoring, and writes candidates to
``dedup_candidates``. Never auto-merges — decision='pending' always.

CLI:
    python -m scrapers.jobs.consolidation_job --entity-type person --batch-size 500 --threshold 0.85

Environment:
    SUPABASE_URL       — Supabase REST API base URL
    SUPABASE_SERVICE_KEY — service_role key for writes

Testing:
    Inject an httpx.Client with MockTransport via ``_create_client``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import click
import httpx

from scrapers.dedup.blocking import build_blocks
from scrapers.dedup.clustering import find_candidates

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEFAULT_BATCH_SIZE = 500
_DEFAULT_THRESHOLD = 0.85
_DEFAULT_ENTITY_TYPE = "person"
_INITIAL_CURSOR = ("1970-01-01T00:00:00Z", "00000000-0000-0000-0000-000000000000")


@dataclass
class ConsolidationConfig:
    """Configuracion leida del entorno o inyectada para testing."""

    supabase_url: str
    supabase_service_key: str
    entity_type: str = _DEFAULT_ENTITY_TYPE
    batch_size: int = _DEFAULT_BATCH_SIZE
    threshold: float = _DEFAULT_THRESHOLD

    @classmethod
    def from_env(cls, **overrides: Any) -> ConsolidationConfig | None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_KEY")
        if not url or not key:
            _LOGGER.error("SUPABASE_URL and SUPABASE_SERVICE_KEY are required")
            return None
        return cls(
            supabase_url=str(url).rstrip("/"),
            supabase_service_key=str(key),
            entity_type=overrides.get("entity_type", _DEFAULT_ENTITY_TYPE),
            batch_size=int(overrides.get("batch_size", _DEFAULT_BATCH_SIZE)),
            threshold=float(overrides.get("threshold", _DEFAULT_THRESHOLD)),
        )


@dataclass
class ConsolidationResult:
    """Resultado agregado de una corrida de consolidation."""

    run_id: str
    entity_type: str
    batches: int = 0
    records_read: int = 0
    blocks: int = 0
    pairs_compared: int = 0
    candidates: int = 0
    duplicates_skipped: int = 0
    execution_time_ms: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Supabase REST client (injectable for testing)
# ---------------------------------------------------------------------------


def _build_client(config: ConsolidationConfig) -> httpx.Client:
    """Build a Supabase REST API client."""
    return httpx.Client(
        base_url=config.supabase_url,
        headers={
            "apikey": config.supabase_service_key,
            "Authorization": f"Bearer {config.supabase_service_key}",
            "Content-Type": "application/json",
        },
        timeout=httpx.Timeout(60.0),
    )


# ---------------------------------------------------------------------------
# Cursor-based reading
# ---------------------------------------------------------------------------


def _fetch_batch(
    client: httpx.Client,
    config: ConsolidationConfig,
    cursor: tuple[str, str],
) -> list[dict[str, Any]]:
    """Fetch one batch of unconsolidated person records from aportes.

    Uses cursor (created_at, id) for stable pagination.
    """
    last_created_at, last_id = cursor
    params: dict[str, str] = {
        "select": "*",
        "consolidated_at": "is.null",
        "entity_type": f"eq.{config.entity_type}",
        "order": "created_at.asc,id.asc",
        "limit": str(config.batch_size),
    }
    # Cursor filter: (created_at, id) > (last_created_at, last_id)
    # Supabase PostgREST uses `and=` with parenthesized groups
    filter_parts = []
    filter_parts.append(f"created_at.gt.{last_created_at}")
    filter_parts.append(
        f"or=(created_at.eq.{last_created_at},id.gt.{last_id})"
    )
    params["and"] = f"({','.join(filter_parts)})"

    # Build URL with query params inline (PostgREST style)
    query_parts = []
    for k, v in params.items():
        query_parts.append(f"{k}={v}")

    path = f"/rest/v1/aportes?{'&'.join(query_parts)}"

    response = client.get(path)
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Upsert candidates
# ---------------------------------------------------------------------------


def _upsert_candidates(
    client: httpx.Client,
    candidates: list[dict[str, Any]],
) -> tuple[int, int]:
    """Upsert candidates into dedup_candidates.

    Returns (inserted_or_updated, errors_count).
    Supabase PostgREST supports ON CONFLICT resolution via Prefer header.
    """
    if not candidates:
        return (0, 0)

    inserted = 0
    errors = 0

    for cand in candidates:
        payload = {
            "left_person": cand["left_person"],
            "right_person": cand["right_person"],
            "score": cand["score"],
            "reasons": cand["reasons"],
            "priority": cand["priority"],
            "decision": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            # Use POST with Prefer: resolution=merge-duplicates for upsert
            response = client.post(
                "/rest/v1/dedup_candidates",
                json=payload,
                headers={
                    "Prefer": "resolution=merge-duplicates",
                },
            )
            # ponytail: 409 would mean Supabase ignored Prefer header, surface as error
            if response.status_code in (200, 201):
                inserted += 1
            else:
                _LOGGER.warning(
                    "Failed to upsert candidate %s <-> %s: %s",
                    cand["left_person"],
                    cand["right_person"],
                    response.status_code,
                )
                errors += 1
        except Exception as exc:
            _LOGGER.error("Exception upserting candidate: %s", exc)
            errors += 1

    return (inserted, errors)


# ---------------------------------------------------------------------------
# Mark as consolidated
# ---------------------------------------------------------------------------


def _mark_consolidated(
    client: httpx.Client,
    record_ids: list[str],
) -> int:
    """Mark aportes rows as consolidated by setting consolidated_at = NOW().

    Returns number of rows updated.
    """
    if not record_ids:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    updated = 0

    # Process in chunks to avoid too-large PATCH requests
    chunk_size = 100
    for i in range(0, len(record_ids), chunk_size):
        chunk = record_ids[i : i + chunk_size]
        # Build filter: id=in.(uuid1,uuid2,...)
        ids_csv = ",".join(chunk)
        path = f"/rest/v1/aportes?id=in.({ids_csv})"

        try:
            response = client.patch(
                path,
                json={"consolidated_at": now},
                headers={"Prefer": "return=minimal"},
            )
            if response.status_code in (200, 204):
                updated += len(chunk)
            else:
                _LOGGER.warning(
                    "Failed to mark consolidated: %s", response.status_code
                )
        except Exception as exc:
            _LOGGER.error("Exception marking consolidated: %s", exc)

    return updated


# ---------------------------------------------------------------------------
# Main job
# ---------------------------------------------------------------------------


def run_consolidation(
    config: ConsolidationConfig,
    client: httpx.Client | None = None,
) -> ConsolidationResult:
    """Run the Person dedup consolidation job end-to-end.

    Args:
        config: Consolidation configuration.
        client: Optional pre-built httpx.Client (for test injection).

    Returns:
        ConsolidationResult with summary metrics.
    """
    start_time = time.monotonic()
    run_id = str(uuid.uuid4())
    result = ConsolidationResult(run_id=run_id, entity_type=config.entity_type)

    http_client = client if client is not None else _build_client(config)

    cursor: tuple[str, str] = _INITIAL_CURSOR
    done = False

    while not done:
        try:
            rows = _fetch_batch(http_client, config, cursor)
        except Exception as exc:
            _LOGGER.error("Error fetching batch: %s", exc)
            result.errors.append(f"fetch_error: {exc}")
            break

        if not rows:
            done = True
            continue

        result.batches += 1
        result.records_read += len(rows)

        blocks = build_blocks(rows)
        if blocks:
            result.blocks += len(blocks)
            candidates = find_candidates(blocks, config.threshold)
            for members in blocks.values():
                n = len(members)
                if n >= 2:
                    result.pairs_compared += n * (n - 1) // 2
            if candidates:
                inserted, upsert_errors = _upsert_candidates(http_client, candidates)
                result.candidates += inserted
                result.duplicates_skipped += upsert_errors

        # Mark processed rows as consolidated (always)
        ids = [str(r.get("id", "")) for r in rows if r.get("id")]
        _mark_consolidated(http_client, ids)

        # Advance cursor (always)
        last_row = rows[-1]
        cursor = (
            str(last_row.get("created_at", _INITIAL_CURSOR[0])),
            str(last_row.get("id", _INITIAL_CURSOR[1])),
        )

        if len(rows) < config.batch_size:
            done = True

    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    result.execution_time_ms = elapsed_ms

    print(json.dumps(asdict(result)))

    return result


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


@click.command()
@click.option("--entity-type", default="person", help="Entity type to consolidate")
@click.option("--batch-size", default=500, help="Batch size for cursor reading")
@click.option("--threshold", default=0.85, help="Minimum similarity score")
def _run(entity_type: str, batch_size: int, threshold: float) -> None:
    """CLI entrypoint with minimal arg parsing.

    Example:
        python -m scrapers.jobs.consolidation_job --entity-type person --batch-size 500 --threshold 0.85
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = ConsolidationConfig.from_env(
        entity_type=entity_type,
        batch_size=batch_size,
        threshold=threshold,
    )
    if config is None:
        _LOGGER.error("Cannot start: missing SUPABASE_URL or SUPABASE_SERVICE_KEY")
        sys.exit(1)

    result = run_consolidation(config)
    if result.errors:
        _LOGGER.warning("Completed with %d errors", len(result.errors))
        sys.exit(1)


if __name__ == "__main__":
    _run()
