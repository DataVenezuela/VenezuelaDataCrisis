from __future__ import annotations

import argparse
import json
from pathlib import Path

from shared.config import get_database_url
from shared.storage import ClaimStore


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def seed_from_output(output_dir: Path, dsn: str) -> dict:
    """Carga documents.jsonl -> observation y claims.jsonl -> claim en Postgres."""
    sanitized = output_dir / "sanitized"
    documents = _read_jsonl(sanitized / "documents.jsonl")
    claims = _read_jsonl(sanitized / "claims.jsonl")

    store = ClaimStore(dsn)
    observations_inserted = store.upsert_observations(documents)
    claims_inserted = store.upsert_claims(claims)

    return {
        "documents_read": len(documents),
        "claims_read": len(claims),
        "observations_inserted": observations_inserted,
        "claims_inserted": claims_inserted,
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m shared.seed", description="Seed Postgres desde JSONL")
    parser.add_argument("--output-dir", default="scrapers/runtime_output", help="Directorio de salida del pipeline")
    args = parser.parse_args()

    dsn = get_database_url()
    if not dsn:
        raise SystemExit("DATABASE_URL no está configurado (revisa .env)")

    result = seed_from_output(Path(args.output_dir), dsn)
    print("Seed finalizado")
    print(f"Documentos leídos: {result['documents_read']}")
    print(f"Claims leídos: {result['claims_read']}")
    print(f"Observations insertadas (nuevas): {result['observations_inserted']}")
    print(f"Claims insertados (nuevos): {result['claims_inserted']}")


if __name__ == "__main__":
    main()
