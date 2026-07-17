from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from scrapers.models._validators import validate_uuid_str
from scrapers.sources.loader import load_sources
from scrapers.validators.source_validator import validate_sources_config


def _cmd_validate(args: argparse.Namespace) -> None:
    config_path = Path(args.config)
    try:
        validate_sources_config(config_path)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"OK: config valida: {config_path}")


def _cmd_run(args: argparse.Namespace) -> None:
    from scrapers.pipelines.run_pipeline import run_pipeline

    summary = run_pipeline(
        config_path=Path(args.config),
        output_dir=Path(args.output_dir),
        limit=args.limit,
        max_workers=args.max_workers,
    )
    print("Pipeline finalizado")
    print(f"Fuentes procesadas: {summary['sources_processed']}")
    print(f"Aportes enviados: {summary['staging_sent']}")
    print(f"Aportes duplicados: {summary['staging_duplicates']}")
    print(f"Errores de staging: {summary['staging_errors']}")
    print(f"Registros en cuarentena: {summary['quarantined']}")
    print(f"Errores de cuarentena: {summary['quarantine_errors']}")
    print(f"Errores: {len(summary['errors'])}")


def _cmd_list_enabled(args: argparse.Namespace) -> None:
    _project, sources = load_sources(Path(args.config))
    enabled = [s for s in sources if s.enabled]

    if args.json:
        print(json.dumps([s.id for s in enabled]))
    else:
        for s in enabled:
            print(f"{s.id}  type={s.type}  refresh={s.refresh_minutes}m")


def _cmd_ingest(args: argparse.Namespace) -> None:
    from scrapers.pipelines.run_pipeline import run_pipeline

    config_path = Path(args.config)
    project, sources = load_sources(config_path)
    source = next((s for s in sources if s.id == args.source), None)

    if source is None:
        print(f"ERROR: fuente '{args.source}' no encontrada en {config_path}", file=sys.stderr)
        raise SystemExit(1)

    if not source.enabled:
        print(f"WARN: fuente '{args.source}' está deshabilitada", file=sys.stderr)

    # Write a temporary single-source config to reuse run_pipeline.
    # Use dataclasses.asdict() to preserve ALL optional fields (probe_limit,
    # max_concurrent_pages, max_concurrent_posts, etc.) instead of a manual
    # dict that silently drops them.
    import dataclasses
    import tempfile

    import yaml

    source_dict = dataclasses.asdict(source)
    source_dict["enabled"] = True  # force-enable for ingest
    single_config = {
        "project": project,
        "sources": [source_dict],
    }

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as tmp:
        yaml.safe_dump(single_config, tmp)
        tmp_path = Path(tmp.name)

    try:
        summary = run_pipeline(
            config_path=tmp_path,
            output_dir=Path(args.output_dir),
            limit=args.limit,
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    result = {
        "source_id": source.id,
        "status": "ok" if not summary["errors"] else "error",
        "records_exported": summary["staging_sent"],
        "records_deduped": summary["staging_duplicates"],
        "errors": summary["errors"],
    }
    print(json.dumps(result, indent=2))

    if summary["errors"]:
        raise SystemExit(1)


def _cmd_materialize(args: argparse.Namespace) -> None:
    """Primera etapa del consolidate: proyecta aportes -> persons/acopio_centers.

    Corre antes de la generacion de aristas (es independiente de ella, solo
    comparte la cadencia del cron). Sin SUPABASE_* entra en dry-run silencioso
    (no-op), asi que en CI no toca la red.
    """
    from scrapers.exporters.staging_exporter import StagingConfig
    from scrapers.jobs.materializer import SilverMaterializer

    try:
        # El materializer solo necesita project.event_id (una constante del YAML),
        # SELECT sobre aportes e INSERT sobre persons. Nunca toca `sources`, asi
        # que leemos el event_id directo del config validado en vez de load_sources
        # (que resuelve las fuentes thin contra la DB y puede 403ear por un grant
        # que esta proyeccion no usa).
        payload = validate_sources_config(Path(args.config))
        project = payload.get("project", {})
        event_id = validate_uuid_str(str(project.get("event_id")))
    except (ValueError, FileNotFoundError, KeyError) as exc:
        print(f"WARN: no se pudo leer project.event_id de {args.config}: {exc}", file=sys.stderr)
        return

    with SilverMaterializer(StagingConfig.from_env()) as materializer:
        result = materializer.materialize(event_id=event_id)
    print(
        "Materializer: "
        f"{result.persons_projected} persons, "
        f"{result.acopio_projected} acopio_centers proyectados; "
        f"{result.events_seeded} eventos sembrados, "
        f"{result.events_skipped} aportes 'event' omitidos"
    )
    if result.cursor_table_missing:
        print(
            "WARN materializer: silver_materialize_state ausente; corriendo scan "
            "completo cada vez (aplicar el DDL pendiente en Supabase)",
            file=sys.stderr,
        )
    if result.cursor_permission_denied:
        print(
            "WARN materializer: sin permiso sobre silver_materialize_state; corriendo "
            "scan completo cada vez (verificar GRANT/POLICY del rol del JWT activo: "
            "SUPABASE_CONSOLIDATION_JWT => consolidation_job, tiene prioridad; "
            "SUPABASE_INGEST_JWT => scraper_ingest)",
            file=sys.stderr,
        )
    for err in result.errors:
        print(f"WARN materializer: {err}", file=sys.stderr)


def _cmd_consolidate(args: argparse.Namespace) -> None:
    """Consolidacion completa: materializer + auto-merge Event/Acopio + candidatos Person.

    Etapa 1: materializer (aportes -> persons/acopio_centers silver).
    Etapa 2: auto-merge exacto de Event/AcopioCenter por `dedup_hash` via
    `consolidation_job.consolidate_entity_type` (#91). En --dry-run solo
    loguea el plan, no upserta ni marca.
    Etapa 3: candidatos de dedup para Person hacia `dedup_candidates` via
    `consolidation_job.run_person_consolidation` (#92). Person nunca
    auto-funde: solo emite candidatos `pending` para revision humana.
    `run_person_consolidation` no tiene modo dry-run propio (escribiria
    candidatos reales pese al flag), asi que en --dry-run esta etapa se omite
    por completo en vez de arriesgar un write no deseado.
    """
    from scrapers.jobs.consolidation_job import (
        AUTOMERGE_ENTITY_TYPES,
        PersonConsolidationConfig,
        build_port,
        consolidate_entity_type,
        run_person_consolidation,
    )

    dry_run: bool = getattr(args, "dry_run", False)
    batch_size: int = getattr(args, "batch_size", 500)

    # Etapa 1: materializer (aportes -> silver tipado). Independiente de la
    # generacion de aristas; solo comparte la cadencia del cron.
    _cmd_materialize(args)

    # Etapa 2: auto-merge exacto Event/AcopioCenter por dedup_hash.
    port = build_port()
    try:
        for entity_type in AUTOMERGE_ENTITY_TYPES:
            summary = consolidate_entity_type(
                port=port,
                entity_type=entity_type,
                batch_size=batch_size,
                dry_run=dry_run,
            )
            print(f"Consolidation[{entity_type}]: {summary}")
    finally:
        port.close()

    # Etapa 3: candidatos Person -> dedup_candidates (nunca auto-funde).
    if dry_run:
        print("Consolidation[Person]: omitido en --dry-run (run_person_consolidation no soporta dry-run)")
        return

    person_config = PersonConsolidationConfig.from_env(batch_size=batch_size)
    if person_config is None:
        print("Consolidation[Person]: sin credenciales Supabase, omitido")
        return

    result = run_person_consolidation(person_config)
    print(
        f"Consolidation[Person]: {result.records_read} aportes leidos, "
        f"{result.candidates_inserted_or_updated} candidatos, "
        f"{len(result.errors)} errores"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m scrapers.cli",
        description="VZLA_DEDUP scrapers pipeline",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- run ---
    run_cmd = sub.add_parser("run", help="Run full scraper pipeline")
    run_cmd.add_argument("--config", required=True, help="YAML config path")
    run_cmd.add_argument(
        "--output-dir", default="scrapers/runtime_output", help="Output directory"
    )
    run_cmd.add_argument("--limit", type=int, default=None, help="Max documents per source")
    run_cmd.add_argument(
        "--max-workers", type=int, default=1,
        help="Fuentes procesadas en paralelo (default 1 = secuencial)",
    )

    # --- validate ---
    validate_cmd = sub.add_parser("validate", help="Validate source config")
    validate_cmd.add_argument("--config", required=True, help="YAML config path")

    # --- list-enabled ---
    list_cmd = sub.add_parser("list-enabled", help="List enabled sources")
    list_cmd.add_argument("--config", required=True, help="YAML config path")
    list_cmd.add_argument(
        "--json", action="store_true", help="Output as JSON array of source IDs"
    )

    # --- ingest ---
    ingest_cmd = sub.add_parser("ingest", help="Ingest a single source")
    ingest_cmd.add_argument("--config", required=True, help="YAML config path")
    ingest_cmd.add_argument("--source", required=True, help="Source ID to ingest")
    ingest_cmd.add_argument(
        "--output-dir", default="scrapers/runtime_output", help="Output directory"
    )
    ingest_cmd.add_argument("--limit", type=int, default=None, help="Max documents")

    # --- consolidate ---
    consolidate_cmd = sub.add_parser("consolidate", help="Cross-source deduplication")
    consolidate_cmd.add_argument(
        "--output-dir", default="scrapers/runtime_output", help="Output directory"
    )
    consolidate_cmd.add_argument(
        "--config",
        default="scrapers/config/sources.demo.yaml",
        help="YAML config path (para project.event_id del seed del catalogo)",
    )
    consolidate_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Solo loguea el plan de auto-merge Event/AcopioCenter; no upserta "
            "ni marca nada. Omite por completo la etapa de candidatos Person."
        ),
    )
    consolidate_cmd.add_argument(
        "--batch-size", type=int, default=500, help="Aportes por batch (default: 500)"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s %(message)s")

    # httpx/httpcore loguean la URL de cada request (httpx a INFO: "HTTP Request:
    # GET https://...", httpcore a DEBUG con host=...). En --verbose eso filtraria
    # la url de cada fuente, es decir su identidad, a stdout/CI logs. Se los sube a
    # WARNING siempre: los fallos de transporte se siguen viendo, pero sin la URL.
    for _noisy in ("httpx", "httpcore"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    commands = {
        "validate": _cmd_validate,
        "run": _cmd_run,
        "list-enabled": _cmd_list_enabled,
        "ingest": _cmd_ingest,
        "consolidate": _cmd_consolidate,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
