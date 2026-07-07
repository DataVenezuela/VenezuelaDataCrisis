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

El acceso a datos real es `SupabaseConsolidationAdapter` (PostgREST directo,
decision del equipo #82), que se cablea via `_build_port()` desde las env vars
SUPABASE_URL + SUPABASE_PUBLISHABLE_KEY + SUPABASE_CONSOLIDATION_JWT (patron de
auth acordado en #200: rol dedicado consolidation_job, sin service_role); si
faltan, cae a un `FakeInMemoryAdapter` vacio (dry-run seguro, sin red). Ver
scrapers/jobs/supabase_adapter.py. El camino Person (#92) usa el mismo par de
credenciales via `PersonConsolidationConfig`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from scrapers.dedup.blocking import build_blocks
from scrapers.dedup.clustering import find_candidates
from scrapers.dedup import specs
from scrapers.dedup.fingerprint import FINGERPRINT_VERSION
from scrapers.jobs.ports import ConsolidationDataPort, FakeInMemoryAdapter, Record
from scrapers.jobs.supabase_adapter import (
    SupabaseConsolidationAdapter,
    SupabaseConsolidationConfig,
)

_LOGGER = logging.getLogger(__name__)

_PERSON_ENTITY_TYPE = "person"
_PERSON_DEFAULT_BATCH_SIZE = 500
_PERSON_DEFAULT_THRESHOLD = 0.85
_INITIAL_CURSOR = ("1970-01-01T00:00:00Z", "00000000-0000-0000-0000-000000000000")

# Entity types que este job puede auto-fundir. Se derivan de SPECS para no
# duplicar la decision de allow_automerge (Person queda fuera por construccion).
AUTOMERGE_ENTITY_TYPES: tuple[str, ...] = tuple(
    name for name, spec in specs.SPECS.items() if spec.allow_automerge
)

# Mapeo de tier -> rango numerico. Decision del equipo (#82): trust_tier es una
# columna de aportes con letras A/B/C/D mapeadas a 1/2/3/4, y GANA EL MENOR numero
# (A=1 es la fuente mas confiable). Ver docstring de `pick_winner` para el criterio
# completo y para la nota de schema (aportes.trust_tier depende de una migracion
# de backend aun pendiente). El rango es inyectable para tests.
DEFAULT_TIER_RANK: dict[str, int] = {"A": 1, "B": 2, "C": 3, "D": 4}

# Rango por defecto para un tier desconocido/ausente. Como GANA EL MENOR, un tier
# desconocido debe PERDER frente a cualquier tier conocido: se le asigna el peor
# rango posible (mayor que cualquier valor de DEFAULT_TIER_RANK).
_UNKNOWN_TIER_RANK = max(DEFAULT_TIER_RANK.values()) + 1


TierRankFn = Callable[[str], int]


def default_tier_rank(tier: str) -> int:
    """Rango numerico por defecto de un tier; MENOR gana (A=1 es el mejor).

    Decision del equipo (#82): A=1, B=2, C=3, D=4. Normaliza a mayusculas y cae a
    `_UNKNOWN_TIER_RANK` (el peor) si el tier no esta en `DEFAULT_TIER_RANK`, para
    que un tier desconocido/ausente nunca le gane a uno conocido.
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


def _neg_confidence_score(rec: Record) -> float:
    """-confidence_score del record (mayor confidence primero); 0.0 si falta/invalido."""
    raw = rec.get("confidence_score")
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return 0.0
    try:
        return -float(raw)
    except (TypeError, ValueError):
        return 0.0


def _fetched_at_epoch(rec: Record) -> float:
    """Epoch (segundos) de ``fetched_at`` ISO-8601; -inf si falta/invalido.

    Se usa para ordenar por fetched_at DESCENDENTE con ``min()``: la clave de
    orden lo niega, asi que un fetched_at mas reciente (epoch mayor) produce una
    clave menor y gana. Un fetched_at ausente/invalido queda como el mas antiguo
    posible (nunca gana el desempate por recencia).
    """
    raw = rec.get("fetched_at")
    if not isinstance(raw, str) or not raw:
        return float("-inf")
    try:
        text = raw.replace("Z", "+00:00")
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return float("-inf")


def pick_winner(group: list[Record], tier_rank: TierRankFn = default_tier_rank) -> Record:
    """Elige el aporte ganador de un grupo de duplicados exactos. Funcion pura.

    Criterio (decision del equipo #82, determinista):
      1. MENOR rango de tier segun ``tier_rank`` (inyectable; default A=1..D=4,
         gana el menor => la fuente mas confiable).
      2. Desempate: ``fetched_at`` mas reciente (descendente).
      3. Desempate: mayor ``confidence_score`` (descendente).
      4. Desempate ESTABLE final: ``created_at`` mas antiguo y luego ``source_id``
         menor lexicografico. Ambas claves siempre presentes => resultado
         independiente del orden de entrada.

    Nota sobre tier: los modelos Python usan letras A/B/C/D. La decision del
    equipo las mapea a 1/2/3/4 y hace ganar al MENOR. IMPORTANTE: la columna
    ``aportes.trust_tier`` NO existe todavia en el schema real del backend
    (supabase/migrations 0001/0008); el mapeo de la decision DEPENDE de una
    migracion pendiente. El adapter degrada de forma segura si la columna falta
    (tier vacio => rango peor), y los desempates por fetched_at/confidence_score
    mantienen el determinismo aun sin tier.
    """
    if not group:
        raise ValueError("pick_winner requiere un grupo no vacio")

    def sort_key(rec: Record) -> tuple[int, float, float, str, str]:
        rank = tier_rank(str(rec.get("trust_tier") or ""))
        neg_fetched_at = -_fetched_at_epoch(rec)
        neg_conf = _neg_confidence_score(rec)
        created_at = str(rec.get("created_at") or "")
        source_id = str(rec.get("source_id") or "")
        # rank ascendente (menor tier gana); neg_fetched_at y neg_conf ya negados
        # para que min() elija el mas reciente / mayor confidence; created_at y
        # source_id ascendentes para un desempate final estable.
        return (rank, neg_fetched_at, neg_conf, created_at, source_id)

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

    summary = {
        "groups": 0,
        "aportes": 0,
        "upserts": 0,
        "marked": 0,
        "batches": 0,
        "errors": 0,
    }

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

            # Fallo parcial de batch: si upsert/mark de ESTE grupo revienta, se
            # loguea, se cuenta y se sigue con los demas grupos (regla del
            # staging_exporter: el batch avanza pese a errores parciales). No
            # marcar el grupo fallido => se re-lee en la ronda siguiente y se
            # reintenta de forma idempotente (upsert por on_conflict, mark por id).
            try:
                canonical = canonical_from_winner(winner)
                port.upsert_canonical(entity_type, canonical)
                summary["upserts"] += 1
                port.mark_consolidated(aporte_ids)
                summary["marked"] += len(aporte_ids)
                batch_progress = True
            except Exception as exc:  # noqa: BLE001 - aislar el grupo, no el job
                summary["errors"] += 1
                active_logger.error(
                    "consolidation group FAILED entity_type=%s dedup_hash=%s "
                    "winner_id=%s aporte_ids=%s: %s",
                    entity_type,
                    dedup_hash,
                    winner.get("id"),
                    aporte_ids,
                    exc,
                )
                continue

        # En dry_run no se marca nada, asi que el siguiente fetch devolveria el
        # mismo batch: cortar tras el primer pase para no ciclar.
        if dry_run or not batch_progress:
            break

    active_logger.info(
        "consolidation done entity_type=%s groups=%d aportes=%d upserts=%d "
        "marked=%d batches=%d errors=%d dry_run=%s",
        entity_type,
        summary["groups"],
        summary["aportes"],
        summary["upserts"],
        summary["marked"],
        summary["batches"],
        summary["errors"],
        dry_run,
    )
    return summary


@dataclass
class PersonConsolidationConfig:
    """Configuracion para Person dedup candidates via Supabase REST.

    Auth segun el patron acordado en #200 (mismo que el adapter Event/Acopio):
    ``publishable_key`` en el header ``apikey`` y ``consolidation_jwt`` en
    ``Authorization: Bearer`` (rol dedicado ``consolidation_job``, sin
    service_role). El JWT solo se LEE del entorno; el adapter NO lo firma.
    Depende de la migracion de backend del rol consolidation_job (grants +
    policies) y de la credencial ``SUPABASE_CONSOLIDATION_JWT`` (aun inexistentes) para
    correr contra Supabase real; no cambia trust_tier ni el schema.
    """

    supabase_url: str
    publishable_key: str
    consolidation_jwt: str
    entity_type: str = _PERSON_ENTITY_TYPE
    batch_size: int = _PERSON_DEFAULT_BATCH_SIZE
    threshold: float = _PERSON_DEFAULT_THRESHOLD

    @classmethod
    def from_env(cls, **overrides: Any) -> "PersonConsolidationConfig | None":
        """Construye la config desde el entorno; None si falta.

        Espeja ``SupabaseConsolidationConfig.from_env`` (Event/Acopio): distingue
        el dry-run intencional (NINGUNA env seteada, dev local) de una config
        parcial en prod (alguna seteada, otra no). La primera loguea a INFO, la
        segunda a ERROR listando las faltantes. En ambos casos devuelve None
        (gatilla dry-run) sin abortar. NUNCA loguea el valor de la key ni del JWT.
        """
        values = {
            "SUPABASE_URL": os.getenv("SUPABASE_URL"),
            "SUPABASE_PUBLISHABLE_KEY": os.getenv("SUPABASE_PUBLISHABLE_KEY"),
            "SUPABASE_CONSOLIDATION_JWT": os.getenv("SUPABASE_CONSOLIDATION_JWT"),
        }
        present = [k for k, v in values.items() if v]
        if not present:
            _LOGGER.info(
                "person consolidation deshabilitado: ninguna SUPABASE_* seteada "
                "(dry-run intencional)"
            )
            return None
        if len(present) < len(values):
            missing = [k for k, v in values.items() if not v]
            _LOGGER.error(
                "person consolidation mal configurado: faltan %s; entrando en dry-run",
                missing,
            )
            return None
        base_url = str(values["SUPABASE_URL"]).rstrip("/")
        # La key/JWT y (potencialmente) PII viajan en cada request. Sobre HTTP
        # plano serian interceptables (MITM); exigir HTTPS. Config errada =>
        # dry-run, nunca enviar a un endpoint inseguro (igual que el adapter
        # Event/Acopio, SupabaseConsolidationConfig.from_env).
        if not base_url.lower().startswith("https://"):
            _LOGGER.error(
                "person consolidation: SUPABASE_URL debe ser https:// (recibido %r); "
                "entrando en dry-run para no enviar credenciales/PII en claro",
                base_url,
            )
            return None
        return cls(
            supabase_url=base_url,
            publishable_key=str(values["SUPABASE_PUBLISHABLE_KEY"]),
            consolidation_jwt=str(values["SUPABASE_CONSOLIDATION_JWT"]),
            entity_type=str(overrides.get("entity_type", _PERSON_ENTITY_TYPE)),
            batch_size=int(overrides.get("batch_size", _PERSON_DEFAULT_BATCH_SIZE)),
            threshold=float(overrides.get("threshold", _PERSON_DEFAULT_THRESHOLD)),
        )


@dataclass
class PersonConsolidationResult:
    """Resultado agregado de Person dedup candidates."""

    run_id: str
    entity_type: str
    batches: int = 0
    records_read: int = 0
    blocks: int = 0
    pairs_compared: int = 0
    candidates_inserted_or_updated: int = 0
    duplicates_skipped: int = 0
    upsert_errors: int = 0
    mark_errors: int = 0
    execution_time_ms: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class PersonCandidateWriteResult:
    written: int = 0
    idempotent: int = 0
    errors: int = 0
    mark_blocked_record_ids: set[str] = field(default_factory=set)
    messages: list[str] = field(default_factory=list)
    fatal: bool = False


def _candidate_key(row: dict[str, Any]) -> tuple[str, str, str]:
    left = str(row["left_aporte_id"])
    right = str(row["right_aporte_id"])
    first, second = sorted([left, right])
    return (first, second, str(row["blocking_key"]))


def _source_record_ids(candidate: dict[str, Any]) -> set[str]:
    raw_ids = candidate.get("source_record_ids")
    if not isinstance(raw_ids, list):
        return set()
    return {str(value) for value in raw_ids if value}


def _candidate_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    required = (
        "event_id",
        "left_aporte_id",
        "right_aporte_id",
        "blocking_key",
        "score",
        "reasons",
    )
    missing = [key for key in required if not candidate.get(key)]
    if missing:
        raise ValueError(f"candidate payload missing required fields: {missing}")
    return {
        "event_id": candidate["event_id"],
        "left_aporte_id": candidate["left_aporte_id"],
        "right_aporte_id": candidate["right_aporte_id"],
        "blocking_key": candidate["blocking_key"],
        "score": candidate["score"],
        "reasons": candidate["reasons"],
        "priority": candidate.get("priority", 2),
        "touches_gold": False,
        "decision": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


class SupabasePersonDedupAdapter:
    """REST adapter for Person dedup candidate I/O."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    @classmethod
    def from_config(cls, config: PersonConsolidationConfig) -> "SupabasePersonDedupAdapter":
        client = httpx.Client(
            base_url=config.supabase_url,
            headers={
                # Patron #200: apikey = publishable key; Bearer = JWT del rol
                # dedicado consolidation_job. NO service_role.
                "apikey": config.publishable_key,
                "Authorization": f"Bearer {config.consolidation_jwt}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(60.0),
        )
        return cls(client)

    def fetch_batch(
        self,
        config: PersonConsolidationConfig,
        cursor: tuple[str, str],
    ) -> list[dict[str, Any]]:
        """Lee un batch estable con cursor (created_at, id)."""
        last_created_at, last_id = cursor
        response = self._client.get(
            "/rest/v1/aportes",
            params={
                "select": "*",
                "consolidated_at": "is.null",
                "entity_type": f"eq.{config.entity_type}",
                "or": (
                    f"(created_at.gt.{last_created_at},"
                    f"and(created_at.eq.{last_created_at},id.gt.{last_id}))"
                ),
                "order": "created_at.asc,id.asc",
                "limit": str(config.batch_size),
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise TypeError("Supabase aportes response must be a list")
        return payload

    def find_existing_candidates(
        self,
        payloads: list[dict[str, Any]],
    ) -> dict[tuple[str, str, str], dict[str, Any]]:
        if not payloads:
            return {}

        clauses = []
        for payload in payloads:
            left, right, blocking_key = _candidate_key(payload)
            for left_id, right_id in ((left, right), (right, left)):
                clauses.append(
                    "and("
                    f"left_aporte_id.eq.{left_id},"
                    f"right_aporte_id.eq.{right_id},"
                    f"blocking_key.eq.{blocking_key}"
                    ")"
                )

        response = self._client.get(
            "/rest/v1/dedup_candidates",
            params={
                "select": (
                    "candidate_id,left_aporte_id,"
                    "right_aporte_id,blocking_key"
                ),
                "or": f"({','.join(clauses)})",
            },
        )
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list):
            raise TypeError("Supabase dedup_candidates response must be a list")
        return {_candidate_key(row): row for row in rows}

    def insert_candidates(self, payloads: list[dict[str, Any]]) -> int:
        if not payloads:
            return 0
        response = self._client.post(
            "/rest/v1/dedup_candidates",
            json=payloads,
            headers={"Prefer": "return=minimal"},
        )
        response.raise_for_status()
        return len(payloads)

    def update_candidate(self, candidate_id: str, payload: dict[str, Any]) -> None:
        response = self._client.patch(
            f"/rest/v1/dedup_candidates?candidate_id=eq.{candidate_id}",
            json={
                "score": payload["score"],
                "reasons": payload["reasons"],
                "priority": payload["priority"],
                "decision": "pending",
            },
            headers={"Prefer": "return=minimal"},
        )
        response.raise_for_status()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SupabasePersonDedupAdapter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def mark_consolidated(self, record_ids: list[str]) -> tuple[int, int, list[str]]:
        if not record_ids:
            return (0, 0, [])

        marked = 0
        errors = 0
        messages: list[str] = []
        now = datetime.now(timezone.utc).isoformat()
        for i in range(0, len(record_ids), 100):
            chunk = record_ids[i : i + 100]
            response = self._client.patch(
                f"/rest/v1/aportes?id=in.({','.join(chunk)})",
                json={"consolidated_at": now},
                headers={"Prefer": "return=minimal"},
            )
            if response.status_code in (200, 204):
                marked += len(chunk)
                continue
            errors += 1
            message = f"mark_error: status={response.status_code}"
            messages.append(message)
            _LOGGER.error("Error marking aportes consolidated: %s", response.status_code)
        return (marked, errors, messages)


def _is_fatal_write_error(exc: Exception) -> bool:
    if isinstance(exc, TypeError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (400, 401, 403)
    return False


def _write_person_candidates(
    adapter: SupabasePersonDedupAdapter,
    candidates: list[dict[str, Any]],
) -> PersonCandidateWriteResult:
    result = PersonCandidateWriteResult()
    valid: list[tuple[dict[str, Any], set[str]]] = []

    for candidate in candidates:
        source_ids = _source_record_ids(candidate)
        try:
            valid.append((_candidate_payload(candidate), source_ids))
        except ValueError as exc:
            result.errors += 1
            result.mark_blocked_record_ids.update(source_ids)
            result.messages.append(f"candidate_payload_error: {exc}")
            _LOGGER.warning("Invalid dedup candidate payload: %s", exc)

    payloads = [payload for payload, _ in valid]
    try:
        existing = adapter.find_existing_candidates(payloads)
    except Exception as exc:
        result.fatal = _is_fatal_write_error(exc)
        result.errors += len(payloads) if payloads else 1
        for _, source_ids in valid:
            result.mark_blocked_record_ids.update(source_ids)
        result.messages.append(f"existing_lookup_error: {exc}")
        _LOGGER.error("Error looking up existing dedup candidates: %s", exc)
        return result

    new_payloads: list[dict[str, Any]] = []
    new_source_ids: set[str] = set()
    for payload, source_ids in valid:
        existing_row = existing.get(_candidate_key(payload))
        if existing_row is None:
            new_payloads.append(payload)
            new_source_ids.update(source_ids)
            continue

        candidate_id = existing_row.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            result.errors += 1
            result.mark_blocked_record_ids.update(source_ids)
            result.messages.append("upsert_error: existing candidate missing candidate_id")
            continue
        try:
            adapter.update_candidate(candidate_id, payload)
            result.written += 1
            result.idempotent += 1
        except Exception as exc:
            result.errors += 1
            result.mark_blocked_record_ids.update(source_ids)
            result.messages.append(f"upsert_error: {exc}")
            _LOGGER.error("Error updating dedup candidate: %s", exc)
            if _is_fatal_write_error(exc):
                result.fatal = True
                return result

    try:
        result.written += adapter.insert_candidates(new_payloads)
    except Exception as exc:
        result.errors += len(new_payloads)
        result.mark_blocked_record_ids.update(new_source_ids)
        result.messages.append(f"upsert_error: {exc}")
        _LOGGER.error("Error inserting dedup candidates: %s", exc)
        result.fatal = _is_fatal_write_error(exc)

    return result


def run_person_consolidation(
    config: PersonConsolidationConfig,
    client: httpx.Client | None = None,
) -> PersonConsolidationResult:
    """Run Person dedup candidate generation end-to-end.

    Si el caller inyecta un ``client`` (tests), el caller es dueno de cerrarlo.
    Si no, este runner arma el adapter via ``from_config`` (abre un httpx.Client
    propio) y lo cierra en un ``finally`` para no dejar el client sin cerrar.
    """
    start_time = time.monotonic()
    result = PersonConsolidationResult(run_id=str(uuid.uuid4()), entity_type=config.entity_type)
    caller_owns_client = client is not None
    adapter = (
        SupabasePersonDedupAdapter(client)
        if client is not None
        else SupabasePersonDedupAdapter.from_config(config)
    )
    cursor = _INITIAL_CURSOR

    try:
        while True:
            try:
                rows = adapter.fetch_batch(config, cursor)
            except Exception as exc:
                result.errors.append(f"fetch_error: {exc}")
                break

            if not rows:
                break

            result.batches += 1
            result.records_read += len(rows)

            blocks = build_blocks(rows)
            result.blocks += len(blocks)
            for members in blocks.values():
                n = len(members)
                if n >= 2:
                    result.pairs_compared += n * (n - 1) // 2

            candidates = find_candidates(blocks, config.threshold)
            write_result = _write_person_candidates(adapter, candidates)
            result.candidates_inserted_or_updated += write_result.written
            result.duplicates_skipped += write_result.idempotent
            result.upsert_errors += write_result.errors
            result.errors.extend(write_result.messages)
            if write_result.fatal:
                break

            ids = [
                str(row["id"])
                for row in rows
                if row.get("id") and str(row["id"]) not in write_result.mark_blocked_record_ids
            ]
            _, mark_errors, mark_messages = adapter.mark_consolidated(ids)
            result.mark_errors += mark_errors
            result.errors.extend(mark_messages)
            if mark_errors:
                break

            last_row = rows[-1]
            cursor = (
                str(last_row.get("created_at", _INITIAL_CURSOR[0])),
                str(last_row.get("id", _INITIAL_CURSOR[1])),
            )
            if len(rows) < config.batch_size:
                break
    finally:
        # Solo cerramos el client que abrimos nosotros; el inyectado es del caller.
        if not caller_owns_client:
            adapter.close()

    result.execution_time_ms = int((time.monotonic() - start_time) * 1000)
    print(json.dumps(asdict(result)))
    return result


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
        choices=[*AUTOMERGE_ENTITY_TYPES, _PERSON_ENTITY_TYPE],
        default="Event",
        help="Tipo de entidad a consolidar.",
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
        default=None,
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
    """Construye el PORT de datos a usar por la CLI para Event/AcopioCenter.

    Decision del equipo (#82): acceso DIRECTO a Supabase PostgREST desde GitHub
    Actions. Si `SUPABASE_URL` + `SUPABASE_PUBLISHABLE_KEY` +
    `SUPABASE_CONSOLIDATION_JWT` estan seteadas y la URL es valida (https),
    construye el adapter real (auth por rol dedicado, patron #200); si faltan,
    cae a un `FakeInMemoryAdapter` vacio (dry-run seguro, sin red), igual que el
    patron de dry-run del staging_exporter. Asi `--dry-run` sin env corre sin
    tocar la red.
    """
    config = SupabaseConsolidationConfig.from_env()
    if config is None:
        return FakeInMemoryAdapter()
    return SupabaseConsolidationAdapter.from_config(config)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.batch_size <= 0:
        _LOGGER.error("--batch-size debe ser > 0 (recibido: %d)", args.batch_size)
        return 2
    threshold = (
        _PERSON_DEFAULT_THRESHOLD
        if args.entity_type == _PERSON_ENTITY_TYPE and args.threshold is None
        else 1.0 if args.threshold is None else float(args.threshold)
    )
    if not 0.0 <= threshold <= 1.0:
        _LOGGER.error("--threshold debe estar en [0..1] (recibido: %s)", args.threshold)
        return 2
    if args.entity_type == _PERSON_ENTITY_TYPE:
        config = PersonConsolidationConfig.from_env(
            entity_type=args.entity_type,
            batch_size=args.batch_size,
            threshold=threshold,
        )
        if config is None:
            return 2
        result = run_person_consolidation(config)
        return 1 if result.errors else 0

    if threshold != 1.0:
        _LOGGER.warning(
            "--threshold=%s ignorado: #91 solo hace dedup EXACTO (threshold=1.0)",
            threshold,
        )

    port = _build_port()
    # El port real mantiene un httpx.Client abierto; cerrarlo siempre al terminar
    # (en el fake close() es no-op). Para un CLI que termina solo no es un crash,
    # pero deja el patron listo si el job pasa a correr long-running.
    try:
        summary = consolidate_entity_type(
            port=port,
            entity_type=args.entity_type,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )
    finally:
        port.close()
    _LOGGER.info("resumen: %s", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
