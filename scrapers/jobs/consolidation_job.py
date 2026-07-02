"""Job de consolidacion Stage 2: auto-merge de Event/AcopioCenter por dedup_hash.

Faceta #91 del EPIC #82. Solo cubre Event y AcopioCenter, cuyo dedup exacto
(fingerprint v1) se puede auto-fundir sin revision humana (SPECS.allow_automerge
== True). Person (#92) NO se toca aqui: exige revision humana.

Flujo (por entity_type, en batches, incremental e idempotente):
  1. Leer aportes NO consolidados via el PORT (`fetch_unconsolidated`).
  2. Agrupar por dedup_hash (`group_by_dedup_hash`, funcion pura).
  3. Elegir un ganador determinista por grupo (`pick_winner`, funcion pura).
  4. Upsert de la fila canonica del ganador via el PORT (`upsert_canonical`).
  5. Marcar TODOS los aportes del grupo como consolidados (`mark_consolidated`).

En --dry-run no se escribe nada (ni upsert ni mark): solo se loguea el plan.

Ejecucion:
  python -m scrapers.jobs.consolidation_job \
      --entity-type Event --batch-size 500 [--dry-run]

El acceso a datos real (adapter concreto) queda pendiente de la decision de
arquitectura del backend; ver scrapers/jobs/ports.py. Esta CLI, tal cual, opera
sobre un adapter vacio y esta pensada para cablearse a ese adapter cuando exista.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable, Iterable

from scrapers.dedup import specs
from scrapers.dedup.fingerprint import FINGERPRINT_VERSION
from scrapers.jobs.ports import ConsolidationDataPort, FakeInMemoryAdapter, Record

_LOGGER = logging.getLogger(__name__)

# Entity types que este job puede auto-fundir. Se derivan de SPECS para no
# duplicar la decision de allow_automerge (Person queda fuera por construccion).
AUTOMERGE_ENTITY_TYPES: tuple[str, ...] = tuple(
    name for name, spec in specs.SPECS.items() if spec.allow_automerge
)

# Mapeo de tier -> rango numerico (mayor gana). Reusa la escala del dedup legacy
# (scrapers/dedup/deduplicator._TIER_RANK): los modelos Python usan letras
# A/B/C/D. El backend usa enteros 1/2/3 y su read-path aun no expone trust_tier;
# el ORIGEN y MAPEO REAL de tier queda PENDIENTE de definicion del equipo. Por
# eso el rango es inyectable (ver pick_winner) y este dict es solo el default.
DEFAULT_TIER_RANK: dict[str, int] = {"A": 4, "B": 3, "C": 2, "D": 1}

# Rango por defecto para un tier desconocido/ausente: el mas bajo.
_UNKNOWN_TIER_RANK = 0


TierRankFn = Callable[[str], int]


def default_tier_rank(tier: str) -> int:
    """Rango numerico por defecto de un tier (mayor gana).

    Normaliza a mayusculas y cae a `_UNKNOWN_TIER_RANK` si el tier no esta en
    `DEFAULT_TIER_RANK`. El mapeo real (letras vs enteros 1/2/3 del backend)
    queda pendiente de la definicion del equipo; ver docstring de `pick_winner`.
    """
    return DEFAULT_TIER_RANK.get((tier or "").strip().upper(), _UNKNOWN_TIER_RANK)


# --- Logica pura, sin I/O ni PORT -------------------------------------------

def group_by_dedup_hash(records: Iterable[Record]) -> dict[str, list[Record]]:
    """Agrupa records por su ``dedup_hash``, preservando el orden de entrada.

    Funcion pura. Los records sin ``dedup_hash`` (None o vacio) se descartan:
    sin hash no hay identidad de contenido, asi que no se pueden auto-fundir.
    El orden de insercion de las claves y de los records dentro de cada grupo
    se preserva (dict de Python 3.7+), condicion necesaria para que
    `pick_winner` sea determinista dado un input estable del PORT.
    """
    groups: dict[str, list[Record]] = {}
    for rec in records:
        dedup_hash = rec.get("dedup_hash")
        if not isinstance(dedup_hash, str) or not dedup_hash:
            continue
        groups.setdefault(dedup_hash, []).append(rec)
    return groups


def pick_winner(group: list[Record], tier_rank: TierRankFn = default_tier_rank) -> Record:
    """Elige el aporte ganador de un grupo de duplicados exactos. Funcion pura.

    Criterio (determinista):
      1. Mayor rango de tier segun ``tier_rank`` (inyectable).
      2. Desempate ESTABLE cuando dos duplicados empatan en tier: gana el de
         ``created_at`` mas antiguo (el que se vio primero) y, si tambien empata,
         el de ``source_id`` menor lexicografico. Ambos claves siempre presentes
         => resultado independiente del orden de entrada.

    Nota sobre tier: los modelos Python usan letras A/B/C/D; el backend usa
    enteros 1/2/3 y hoy su read-path NO expone trust_tier. Por eso ``tier_rank``
    se INYECTA (default `default_tier_rank`) y el origen/mapeo real de tier queda
    PENDIENTE de la definicion del equipo. El desempate no depende del tier, asi
    que sigue siendo determinista aun con un mapeo provisional.
    """
    if not group:
        raise ValueError("pick_winner requiere un grupo no vacio")

    def sort_key(rec: Record) -> tuple[int, str, str]:
        rank = tier_rank(str(rec.get("trust_tier") or ""))
        created_at = str(rec.get("created_at") or "")
        source_id = str(rec.get("source_id") or "")
        # -rank: mayor tier primero. created_at/source_id ascendentes para un
        # desempate estable e independiente del orden de entrada.
        return (-rank, created_at, source_id)

    return min(group, key=sort_key)


def canonical_from_winner(winner: Record) -> Record:
    """Construye la fila canonica a materializar a partir del aporte ganador.

    Copia el payload del ganador y adjunta el dedup_hash y la version de
    fingerprint. Mantener esto separado deja el punto de extension para cuando
    el equipo defina el schema canonico real del backend.
    """
    payload = winner.get("payload")
    canonical: Record = dict(payload) if isinstance(payload, dict) else {}
    canonical["dedup_hash"] = winner.get("dedup_hash")
    canonical["dedup_version"] = FINGERPRINT_VERSION
    canonical["winner_aporte_id"] = winner.get("id")
    return canonical


# --- Orquestacion (usa el PORT) ---------------------------------------------

def consolidate_entity_type(
    port: ConsolidationDataPort,
    entity_type: str,
    batch_size: int,
    dry_run: bool = False,
    tier_rank: TierRankFn = default_tier_rank,
    logger: logging.Logger | None = None,
) -> dict[str, int]:
    """Consolida un entity_type en batches hasta agotar lo pendiente.

    Devuelve un resumen con contadores. Incremental: cada batch se relee del
    PORT, asi que si un batch se marca consolidado, el siguiente ya no lo trae.
    Idempotente: re-correr sobre datos ya consolidados no upserta ni marca nada.
    En dry_run NO escribe (ni upsert ni mark); solo cuenta y loguea el plan.
    """
    active_logger = logger or _LOGGER
    if entity_type not in AUTOMERGE_ENTITY_TYPES:
        raise ValueError(
            f"entity_type {entity_type!r} no admite auto-merge; "
            f"permitidos: {list(AUTOMERGE_ENTITY_TYPES)}"
        )

    summary = {"groups": 0, "aportes": 0, "upserts": 0, "marked": 0, "batches": 0}

    while True:
        batch = port.fetch_unconsolidated(entity_type, batch_size)
        if not batch:
            break
        summary["batches"] += 1

        groups = group_by_dedup_hash(batch)
        # Ids sin dedup_hash quedan fuera de los grupos; no se consolidan y se
        # reintentaran (no hay identidad para fundir). Se loguean para visibilidad.
        grouped_ids = {str(r.get("id")) for g in groups.values() for r in g}
        skipped = [str(r.get("id")) for r in batch if str(r.get("id")) not in grouped_ids]
        if skipped:
            active_logger.warning(
                "consolidation skip sin_dedup_hash entity_type=%s count=%d ids=%s",
                entity_type,
                len(skipped),
                skipped,
            )

        # Nada agrupable en este batch: cortar para no ciclar infinito sobre
        # aportes sin hash que nunca se marcan.
        if not groups:
            break

        batch_progress = False
        for dedup_hash, group in groups.items():
            winner = pick_winner(group, tier_rank)
            aporte_ids = [str(rec.get("id")) for rec in group]
            summary["groups"] += 1
            summary["aportes"] += len(group)

            active_logger.info(
                "consolidation group entity_type=%s dedup_hash=%s size=%d "
                "winner_id=%s winner_tier=%s aporte_ids=%s dry_run=%s",
                entity_type,
                dedup_hash,
                len(group),
                winner.get("id"),
                winner.get("trust_tier"),
                aporte_ids,
                dry_run,
            )

            if dry_run:
                continue

            canonical = canonical_from_winner(winner)
            port.upsert_canonical(entity_type, canonical)
            summary["upserts"] += 1
            port.mark_consolidated(aporte_ids)
            summary["marked"] += len(aporte_ids)
            batch_progress = True

        # En dry_run no se marca nada, asi que el siguiente fetch devolveria el
        # mismo batch: cortar tras el primer pase para no ciclar.
        if dry_run or not batch_progress:
            break

    active_logger.info(
        "consolidation done entity_type=%s groups=%d aportes=%d upserts=%d "
        "marked=%d batches=%d dry_run=%s",
        entity_type,
        summary["groups"],
        summary["aportes"],
        summary["upserts"],
        summary["marked"],
        summary["batches"],
        dry_run,
    )
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scrapers.jobs.consolidation_job",
        description=(
            "Consolida (auto-merge) Event/AcopioCenter por dedup_hash. "
            "El adapter de datos real esta pendiente de la decision del equipo; "
            "por defecto corre contra un adapter vacio en memoria."
        ),
    )
    parser.add_argument(
        "--entity-type",
        choices=list(AUTOMERGE_ENTITY_TYPES),
        default="Event",
        help="Tipo de entidad a consolidar (solo tipos con auto-merge).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Cantidad de aportes por batch (default: 500).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=1.0,
        help=(
            "Umbral de similitud [0..1]. Para dedup EXACTO (fingerprint v1) el "
            "unico valor con sentido es 1.0; se acepta como parametro para "
            "compatibilidad futura con matching difuso (fuera de alcance de #91)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No escribe nada; solo loguea el plan de consolidacion.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Nivel de logging (default: INFO).",
    )
    return parser


def _build_port() -> ConsolidationDataPort:
    """Construye el PORT de datos a usar por la CLI.

    Hoy devuelve un `FakeInMemoryAdapter` vacio porque el adapter concreto de
    produccion (PostgREST directo vs Vercel) sigue PENDIENTE de la decision del
    equipo. Este es el unico punto a cambiar cuando esa decision se tome.
    """
    return FakeInMemoryAdapter()


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.batch_size <= 0:
        _LOGGER.error("--batch-size debe ser > 0 (recibido: %d)", args.batch_size)
        return 2
    if not 0.0 <= args.threshold <= 1.0:
        _LOGGER.error("--threshold debe estar en [0..1] (recibido: %s)", args.threshold)
        return 2
    if args.threshold != 1.0:
        _LOGGER.warning(
            "--threshold=%s ignorado: #91 solo hace dedup EXACTO (threshold=1.0)",
            args.threshold,
        )

    port = _build_port()
    summary = consolidate_entity_type(
        port=port,
        entity_type=args.entity_type,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )
    _LOGGER.info("resumen: %s", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
