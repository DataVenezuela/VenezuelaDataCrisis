from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter
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


def _scrub_supabase_url(text: str) -> str:
    """Redacta SUPABASE_URL de un mensaje de error.

    El log del cron de materialize sube como artifact de un repo PUBLICO y
    GitHub solo enmascara secrets, no vars.*: una excepcion de transporte
    (httpx incluye la URL completa del request) publicaria el endpoint
    interno de Supabase (ADR 0002: no facilitar mass-scrapers).
    """
    url = os.getenv("SUPABASE_URL")
    return text.replace(url, "<SUPABASE_URL>") if url else text


def _fail_materialize(detail: str, note: str) -> None:
    """Aviso FAILED de fin de corrida del materialize + exit 1."""
    _emit_run_notice(
        [("Materializer", "FAILED", detail)],
        Counter({note: 1}),
        hard_failed=True,
        command="materialize",
        title="Materialize summary",
    )


def _cmd_materialize(args: argparse.Namespace) -> None:
    """Materializer standalone: proyecta aportes -> persons/acopio_centers (#336, ADR 0011).

    Antes era la etapa 1 del consolidate; ahora tiene su propio comando y su
    propio cron (materialize.yml) porque no actua sobre los datos, solo crea
    las filas proxy tipadas.

    Al final emite UN aviso de fin de corrida (Materialize summary) y corta
    con exit 1 si hubo errores, para que el cron nunca quede verde con fallos
    invisibles (mismo patron que #333 para consolidate). Por lo mismo, sin
    --dry-run las credenciales SUPABASE_* son OBLIGATORIAS: si faltan (secret
    borrado/rotado mal), el materializer entraria en no-op y el cron quedaria
    verde para siempre sin materializar nada; eso es FAILED, no OK. El unico
    modo sin credenciales es --dry-run explicito (no-op garantizado, cero red).
    """
    from scrapers.exporters.staging_exporter import StagingConfig
    from scrapers.jobs.materializer import SilverMaterializer

    dry_run: bool = getattr(args, "dry_run", False)

    try:
        # El materializer solo necesita project.event_id (una constante del YAML),
        # SELECT sobre aportes e INSERT sobre persons. Nunca toca `sources`, asi
        # que leemos el event_id directo del config validado en vez de load_sources
        # (que resuelve las fuentes thin contra la DB y puede 403ear por un grant
        # que esta proyeccion no usa).
        payload = validate_sources_config(Path(args.config))
        project = payload.get("project", {})
        event_id = validate_uuid_str(str(project.get("event_id")))
    except Exception as exc:  # noqa: BLE001 - cualquier config ilegible (YAML roto
        # incluido: yaml.YAMLError NO es ValueError) sale por el aviso FAILED,
        # no por un traceback. Cuando esto era una etapa del consolidate, un
        # config ilegible era WARN+verde (las demas etapas seguian); standalone,
        # sin event_id no se puede hacer NADA (#333: nada de fallos invisibles).
        detail = f"no se pudo leer project.event_id de {args.config}"
        print(f"ERROR materialize: {detail}: {exc}", file=sys.stderr)
        _fail_materialize(detail, f"{detail}: {exc}")
        return

    if dry_run:
        # Config None => SilverMaterializer deshabilitado: no crea cliente, no
        # toca la red, no escribe, AUNQUE haya SUPABASE_* en el entorno. Valida
        # config/event_id y el wiring del subcomando (step de verificacion del cron).
        config = None
    else:
        config = StagingConfig.from_env()
        if config is None:
            # from_env ya logueo que falta (todas ausentes o config parcial).
            detail = "sin credenciales Supabase (config ausente o parcial)"
            print(f"ERROR materialize: {detail}", file=sys.stderr)
            _fail_materialize(detail, detail)
            return

    try:
        with SilverMaterializer(config) as materializer:
            result = materializer.materialize(event_id=event_id)
    except Exception as exc:  # noqa: BLE001 - FAILED visible en el cron, no traceback verde
        message = _scrub_supabase_url(str(exc))
        detail = next(iter(message.splitlines()), "") or type(exc).__name__
        print(f"Materialize: FAILED {message}", file=sys.stderr)
        _fail_materialize(detail, detail)
        return
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
    # Los errores van SOLO a las notas deduplicadas del aviso final (sin linea
    # cruda por error): un 403-storm de miles de errores identicos no debe
    # inundar el log del cron (#333: un aviso accionable, no un flood).
    error_notes: "Counter[str]" = Counter()
    for err in result.errors:
        first_line = next(iter(str(err).splitlines()), str(err))
        error_notes[first_line] += 1

    detail = (
        f"{result.persons_projected} persons, {result.acopio_projected} acopio, "
        f"{result.events_seeded} eventos"
    )
    if dry_run:
        detail = f"{detail}, dry-run (sin escrituras)"
    if result.errors:
        status = "FAILED"
        detail = f"{detail}, {len(result.errors)} error(es)"
    else:
        status = "OK"
    _emit_run_notice(
        [("Materializer", status, detail)],
        error_notes,
        hard_failed=status == "FAILED",
        command="materialize",
        title="Materialize summary",
    )


def _emit_run_notice(
    stages: list[tuple[str, str, str]],
    error_notes: "Counter[str]",
    *,
    hard_failed: bool,
    command: str = "consolidate",
    title: str = "Consolidation summary",
) -> None:
    """Imprime UN aviso de fin de corrida (no un log por intento) y corta en rojo si algo fallo en duro.

    El incidente que motivo esto fue invisible: el cron quedaba verde mientras
    cada escritura de acopio 400eaba. Aca se emite una sola linea-resumen por
    stage con OK/FAILED/DEGRADED, seguida de las notas de error DEDUPLICADAS
    (cada mensaje unico una vez, con su conteo, tope de 5) para que sea un
    aviso accionable y no un flood. `hard_failed` corta con exit 1; un stage
    DEGRADED (errores parciales) se queda en verde a proposito.

    Para consolidate los stages son Event/AcopioCenter/Person; para
    materialize hay un unico stage Materializer (#336). `command`/`title`
    ajustan los prefijos de las lineas para cada comando.
    """
    line = " | ".join(f"{name}={status}({detail})" for name, status, detail in stages)
    any_not_ok = any(status != "OK" for _, status, _ in stages)
    print(f"{title}: {line}", file=sys.stderr if any_not_ok else sys.stdout)

    top = error_notes.most_common(5)
    for note, count in top:
        suffix = f" (x{count})" if count > 1 else ""
        print(f"WARN {command}: {note}{suffix}", file=sys.stderr)
    extra = len(error_notes) - len(top)
    if extra > 0:
        print(f"WARN {command}: (+{extra} mensajes de error distintos mas)", file=sys.stderr)

    if hard_failed:
        raise SystemExit(1)


def _cmd_consolidate(args: argparse.Namespace) -> None:
    """Consolidacion: auto-merge Event/Acopio + candidatos Person.

    El materializer ya NO corre aca (#336, ADR 0011): es el comando
    `materialize`, con su propio cron (materialize.yml).

    Etapa 1: auto-merge exacto de Event/AcopioCenter por `dedup_hash` via
    `consolidation_job.consolidate_entity_type` (#91). En --dry-run solo
    loguea el plan, no upserta ni marca.
    Etapa 2: candidatos de dedup para Person hacia `dedup_candidates` via
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

    # Resumen de fin de corrida: un stage por (Event/AcopioCenter/Person) con su
    # estado, mas notas de error deduplicadas. Se emite UNA vez al final (ver
    # _emit_run_notice) para que un fallo parcial deje de ser invisible.
    stages: list[tuple[str, str, str]] = []
    error_notes: "Counter[str]" = Counter()
    hard_failed = False

    # Etapa 1: auto-merge exacto Event/AcopioCenter por dedup_hash. Cada
    # entity_type se aisla en su propio try/except: un fetch que revienta
    # (error transitorio de red/Supabase, sin try/except propio dentro de
    # consolidate_entity_type) no debe abortar el comando entero antes de
    # llegar a la Etapa 2 (Person no depende de que Event/AcopioCenter salgan
    # bien).
    port = build_port()
    try:
        for entity_type in AUTOMERGE_ENTITY_TYPES:
            try:
                summary = consolidate_entity_type(
                    port=port,
                    entity_type=entity_type,
                    batch_size=batch_size,
                    dry_run=dry_run,
                )
                print(f"Consolidation[{entity_type}]: {summary}")
                detail = (
                    f"{summary['upserts']} upserts, {summary['groups']} grupos, "
                    f"{summary['batches']} batches"
                )
                if summary.get("errors"):
                    stages.append((entity_type, "DEGRADED", f"{detail}, {summary['errors']} con error"))
                    error_notes[f"{entity_type}: {summary['errors']} grupo(s) con error de upsert"] += 1
                else:
                    stages.append((entity_type, "OK", detail))
            except Exception as exc:  # noqa: BLE001 - aislar el entity_type, no el comando
                # Fallo en duro (p.ej. 400 de enum invalido): captura el detalle
                # que hoy se pierde (raise_for_status propaga el cuerpo PostgREST).
                hard_failed = True
                detail = next(iter(str(exc).splitlines()), "") or type(exc).__name__
                stages.append((entity_type, "FAILED", detail))
                error_notes[f"{entity_type}: {detail}"] += 1
                print(f"Consolidation[{entity_type}]: FAILED {exc}", file=sys.stderr)
    finally:
        port.close()

    # Etapa 2: candidatos Person -> dedup_candidates (nunca auto-funde).
    if dry_run:
        print("Consolidation[Person]: omitido en --dry-run (run_person_consolidation no soporta dry-run)")
        stages.append(("Person", "OK", "omitido (dry-run)"))
        _emit_run_notice(stages, error_notes, hard_failed=hard_failed)
        return

    person_config = PersonConsolidationConfig.from_env(batch_size=batch_size)
    if person_config is None:
        print("Consolidation[Person]: sin credenciales Supabase, omitido")
        stages.append(("Person", "OK", "omitido (sin credenciales)"))
        _emit_run_notice(stages, error_notes, hard_failed=hard_failed)
        return

    result = run_person_consolidation(person_config)
    print(
        f"Consolidation[Person]: {result.records_read} aportes leidos, "
        f"{result.candidates_inserted_or_updated} candidatos, "
        f"{len(result.errors)} errores"
    )
    if result.errors:
        # DEGRADED (no hard-fail): p.ej. el 500/timeout del fetch de companeros de
        # bloque es no-fatal (bloqueo solo-pagina). Se surfacean los mensajes reales,
        # no solo el conteo, deduplicados por _emit_run_notice.
        stages.append(("Person", "DEGRADED", f"{len(result.errors)} error(es)"))
        for msg in result.errors:
            first_line = next(iter(str(msg).splitlines()), str(msg))
            error_notes[f"Person: {first_line}"] += 1
    else:
        stages.append(
            (
                "Person",
                "OK",
                f"{result.candidates_inserted_or_updated} candidatos de {result.records_read} aportes",
            )
        )

    _emit_run_notice(stages, error_notes, hard_failed=hard_failed)


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

    # --- materialize ---
    materialize_cmd = sub.add_parser(
        "materialize",
        help="Proyecta aportes -> filas tipadas (persons/acopio_centers/events)",
    )
    materialize_cmd.add_argument(
        "--config",
        default="scrapers/config/sources.demo.yaml",
        help="YAML config path (para project.event_id del seed del catalogo)",
    )
    materialize_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "No-op garantizado: valida config y project.event_id pero no crea "
            "cliente ni escribe nada, AUNQUE haya SUPABASE_* en el entorno. "
            "Sin este flag, las credenciales SUPABASE_* son obligatorias "
            "(faltantes => FAILED + exit 1, nunca un no-op verde)."
        ),
    )

    # --- consolidate ---
    consolidate_cmd = sub.add_parser("consolidate", help="Cross-source deduplication")
    consolidate_cmd.add_argument(
        "--config",
        default="scrapers/config/sources.demo.yaml",
        help=(
            "YAML config path. Sin uso directo desde #336 (el materializer, "
            "unico consumidor de project.event_id, es ahora el comando "
            "`materialize`); se mantiene por compatibilidad con invocaciones "
            "existentes (consolidate.yml)."
        ),
    )
    consolidate_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "No upserta ni marca nada en el auto-merge Event/AcopioCenter, "
            "y omite la etapa de candidatos Person por completo."
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
        "materialize": _cmd_materialize,
        "consolidate": _cmd_consolidate,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
