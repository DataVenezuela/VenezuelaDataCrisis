from __future__ import annotations

import argparse
from pathlib import Path

from scrapers.pipelines.run_pipeline import run_pipeline
from scrapers.validators.source_validator import validate_sources_config


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m scrapers.cli",
        description="VZLA_DEDUP scrapers pipeline",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="Run scraper pipeline")
    run_cmd.add_argument("--config", required=True, help="YAML config path")
    run_cmd.add_argument("--output-dir", default="scrapers/runtime_output", help="Output directory")
    run_cmd.add_argument("--limit", type=int, default=None, help="Max documents per source")
    run_cmd.add_argument(
        "--keep-raw",
        action="store_true",
        help="Keep redacted source snapshots locally for debugging; raw PII is never written",
    )
    persist_group = run_cmd.add_mutually_exclusive_group()
    persist_group.add_argument(
        "--persist",
        dest="persist_db",
        action="store_true",
        default=None,
        help="Persist to Postgres (requires DATABASE_URL). Default: auto if DATABASE_URL is set",
    )
    persist_group.add_argument(
        "--no-persist",
        dest="persist_db",
        action="store_false",
        help="Skip Postgres persistence even if DATABASE_URL is set",
    )

    validate_cmd = sub.add_parser("validate", help="Validate source config")
    validate_cmd.add_argument("--config", required=True, help="YAML config path")

    args = parser.parse_args()

    if args.command == "validate":
        config_path = Path(args.config)
        validate_sources_config(config_path)
        print(f"OK: config válida: {config_path}")
        return

    if args.command == "run":
        summary = run_pipeline(
            config_path=Path(args.config),
            output_dir=Path(args.output_dir),
            limit=args.limit,
            keep_raw=args.keep_raw,
            persist_db=args.persist_db,
        )
        print("Pipeline finalizado")
        print(f"Fuentes procesadas: {summary['sources_processed']}")
        print(f"Documentos exportados: {summary['documents_exported']}")
        print(f"Claims exportados: {summary['claims_exported']}")
        print(f"Claims deduplicados: {summary['claims_deduplicated']}")
        if summary.get("persistence"):
            print(f"Persistencia (Postgres): {summary['persistence']}")
        print(f"Errores: {len(summary['errors'])}")
        print(f"Salida: {Path(args.output_dir).resolve()}")


if __name__ == "__main__":
    main()
