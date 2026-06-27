from __future__ import annotations

import sqlite3
from pathlib import Path


DEFAULT_DEDUP_DB_PATH = Path(__file__).resolve().parents[1] / "runtime_output" / "dedup_state.db"


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
