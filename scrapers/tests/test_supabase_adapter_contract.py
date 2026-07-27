"""Test de CONTRATO: los nombres del adapter coinciden con el schema REAL.

Este repo ya tuvo 3 bugs de falso-verde (#90/#104/#187) por mapear contra
schemas inventados. Este test parsea un fixture congelado derivado VERBATIM de
las migraciones reales del backend (fixtures/backend_schema_dedup.sql, tomado de
DataVenezuela/dataVenezuela supabase/migrations 0001/0004/0008/0009) y verifica
que cada columna y slug que usa ``supabase_adapter`` exista de verdad. Si el
schema cambia, este test rompe en vez de producir un falso-verde silencioso.

NO hace red: lee el fixture local. Si el backend cambia, hay que re-fetchear el
fixture (comando en su cabecera) y actualizar el adapter.
"""

from __future__ import annotations

import re
from pathlib import Path

from scrapers.jobs.supabase_adapter import (
    _APORTE_FIELD_MAP,
    _CANONICAL_COLUMNS,
    _ENTITY_TABLES,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "backend_schema_dedup.sql"


def _read_schema() -> str:
    return _FIXTURE.read_text(encoding="utf-8")


def _columns_of_table(sql: str, table: str) -> set[str]:
    """Extrae los nombres de columna declarados para ``public.<table>``.

    Junta las columnas del CREATE TABLE y las de los ALTER TABLE ... ADD COLUMN,
    que es como el schema real reparte las columnas de dedup entre migraciones.
    """
    columns: set[str] = set()

    # CREATE TABLE public.<table> ( ... );
    create = re.search(
        rf"create table public\.{re.escape(table)}\s*\((.*?)\n\);",
        sql,
        re.DOTALL | re.IGNORECASE,
    )
    if create:
        columns |= _parse_column_block(create.group(1))

    # ALTER TABLE public.<table> ADD COLUMN ... (uno o varios por sentencia).
    for alter in re.finditer(
        rf"alter table public\.{re.escape(table)}\s*(.*?);",
        sql,
        re.DOTALL | re.IGNORECASE,
    ):
        for add in re.finditer(
            r"add column\s+(?:if not exists\s+)?([a-z_][a-z0-9_]*)",
            alter.group(1),
            re.IGNORECASE,
        ):
            columns.add(add.group(1).lower())

    return columns


def _parse_column_block(block: str) -> set[str]:
    """Nombres de columna de un cuerpo de CREATE TABLE (ignora constraints)."""
    columns: set[str] = set()
    for raw_line in block.splitlines():
        line = raw_line.strip().rstrip(",")
        if not line:
            continue
        head = line.split()[0].lower()
        # Saltar lineas de constraint/check/comentario.
        if head in {"constraint", "check", "primary", "unique", "foreign", "--"}:
            continue
        if head.startswith("--"):
            continue
        if re.fullmatch(r"[a-z_][a-z0-9_]*", head):
            columns.add(head)
    return columns


def test_fixture_existe_y_no_vacio() -> None:
    assert _FIXTURE.exists()
    assert _read_schema().strip()


def test_aportes_tiene_columnas_que_el_adapter_lee() -> None:
    sql = _read_schema()
    aportes_cols = _columns_of_table(sql, "aportes")
    # Todas las columnas de aportes que el adapter mapea deben existir de verdad.
    for column in _APORTE_FIELD_MAP:
        assert column in aportes_cols, (
            f"columna aportes.{column} usada por el adapter NO existe en el "
            f"schema real; columnas: {sorted(aportes_cols)}"
        )
    # Filtros clave del fetch / mark tambien deben ser columnas reales.
    assert "consolidated_at" in aportes_cols
    assert "entity_type" in aportes_cols
    assert "id" in aportes_cols


def test_aportes_trust_tier_ausente_en_schema_real() -> None:
    # DOCUMENTADO: la decision del equipo (#82) trata trust_tier como columna de
    # aportes, pero NO existe en el schema real publicado. Este test fija ese hecho
    # explicitamente: si una migracion futura la agrega y se actualiza el fixture,
    # este test rompe y avisa que ya no es un supuesto pendiente.
    aportes_cols = _columns_of_table(_read_schema(), "aportes")
    assert "trust_tier" not in aportes_cols
    # fetched_at y confidence_score tampoco viven en aportes en el schema real.
    assert "fetched_at" not in aportes_cols
    assert "confidence_score" not in aportes_cols


def test_columnas_canonicas_de_events_existen() -> None:
    sql = _read_schema()
    events_cols = _columns_of_table(sql, "events")
    assert "dedup_hash" in events_cols  # el on_conflict del upsert
    for column in _CANONICAL_COLUMNS["Event"]:
        assert column in events_cols, (
            f"columna events.{column} en _CANONICAL_COLUMNS NO existe en el "
            f"schema real; columnas: {sorted(events_cols)}"
        )


def test_columnas_canonicas_de_acopio_existen() -> None:
    sql = _read_schema()
    acopio_cols = _columns_of_table(sql, "acopio_centers")
    assert "dedup_hash" in acopio_cols
    for column in _CANONICAL_COLUMNS["AcopioCenter"]:
        assert column in acopio_cols, (
            f"columna acopio_centers.{column} en _CANONICAL_COLUMNS NO existe en "
            f"el schema real; columnas: {sorted(acopio_cols)}"
        )


def test_indices_unique_dedup_hash_existen() -> None:
    # El upsert on_conflict=dedup_hash exige un indice UNIQUE en dedup_hash (0009).
    sql = _read_schema().lower()
    assert "unique index events_dedup_uniq" in sql
    assert "unique index acopio_centers_dedup_uniq" in sql


def test_slugs_entity_type_coinciden_con_enum_del_backend() -> None:
    # El comentario del schema real declara el enum: event | acopio | person.
    slugs = {slug for slug, _ in _ENTITY_TABLES.values()}
    assert slugs == {"event", "acopio"}  # #91 solo auto-merge de Event/Acopio
    schema = _read_schema().lower()
    assert "event | acopio | person" in schema
    for slug in slugs:
        assert slug in schema


def test_table_paths_apuntan_a_tablas_reales() -> None:
    sql = _read_schema().lower()
    for _, table_path in _ENTITY_TABLES.values():
        table = table_path.rsplit("/", 1)[-1]
        assert f"create table public.{table}" in sql


# ---------------------------------------------------------------------------
# dedup_candidates — contrato del consolidation_job (#281)
# ---------------------------------------------------------------------------

def test_dedup_candidates_columnas_reales_existen() -> None:
    sql = _read_schema()
    cols = _columns_of_table(sql, "dedup_candidates")
    for col in ("left_aporte_id", "right_aporte_id", "blocking_key", "priority", "touches_gold"):
        assert col in cols, (
            f"dedup_candidates.{col} requerida por el consolidation_job "
            f"no existe en el schema real; columnas: {sorted(cols)}"
        )
    # Columnas del schema viejo (migración 0009) no deben estar en el fixture actual.
    assert "left_person" not in cols, (
        "dedup_candidates.left_person es del schema antiguo; fixture debe usar left_aporte_id"
    )
    assert "right_person" not in cols, (
        "dedup_candidates.right_person es del schema antiguo; fixture debe usar right_aporte_id"
    )


def test_candidate_payload_solo_emite_columnas_reales() -> None:
    from scrapers.jobs.consolidation_job import _candidate_payload

    sql = _read_schema()
    dedup_cols = _columns_of_table(sql, "dedup_candidates")
    payload = _candidate_payload({
        "left_aporte_id": "aaaaaaaa-0000-4000-8000-000000000001",
        "right_aporte_id": "aaaaaaaa-0000-4000-8000-000000000002",
        "blocking_key": "ced:ev1:abc123",
        "score": 0.95,
        "reasons": {"nombre": 0.5},
        "priority": 1,
    })
    unknown = set(payload) - dedup_cols
    assert not unknown, (
        f"_candidate_payload() emite claves sin columna real en dedup_candidates: {unknown}. "
        "Actualizar el payload o el fixture si el schema cambió."
    )


# ---------------------------------------------------------------------------
# gold_entities / gold_members / gold_history — contrato del GoldWriter (#317)
#
# #317 despliega la capa gold + grants de consolidation_job (DDL en el cuerpo del
# PR, aplicada por el mantenedor). Estos tests fijan que las columnas que EMITE
# `GoldWriter` (lo que el adapter de produccion de #318 tendra que escribir)
# existan de verdad en el schema gold, usando el mismo patron anti-drift que
# `test_candidate_payload_solo_emite_columnas_reales`: si el schema gold cambia,
# rompe aca en vez de dar un 4xx en produccion (ver #90/#104/#187 y el incidente
# de `aportes.consolidated_at`).
# ---------------------------------------------------------------------------


class _GoldColumnSpy:
    """Port que registra las columnas exactas que `GoldWriter` emite por tabla.

    No persiste nada: solo acumula los nombres de columna de cada payload para
    contrastarlos contra el fixture congelado. Implementa `GoldDataPort`.
    """

    def __init__(self) -> None:
        self.entity_cols: set[str] = set()
        self.member_cols: set[str] = set()
        self.history_cols: set[str] = set()

    def upsert_gold_entity(self, entity: dict) -> str:
        self.entity_cols |= set(entity)
        return "00000000-0000-4000-8000-000000000000"

    def upsert_gold_member(
        self, gold_id: str, aporte_id: str, via_candidate: str | None = None
    ) -> None:
        # El writer pasa estos tres campos posicionalmente; el adapter real los
        # escribe como columnas de gold_members.
        self.member_cols |= {"gold_id", "aporte_id", "via_candidate"}

    def insert_gold_history(self, event: dict) -> None:
        self.history_cols |= set(event)

    def close(self) -> None:
        return None


def _emit_gold_columns() -> _GoldColumnSpy:
    """Corre `GoldWriter.write_cluster` con un cluster de 2 miembros y captura
    las columnas emitidas hacia cada tabla gold."""
    from scrapers.jobs.gold_writer import GoldWriter

    spy = _GoldColumnSpy()
    winner = {
        "id": "aaaaaaaa-0000-4000-8000-000000000001",
        "entity_type": "Person",
        "confidence_score": 0.91,
        "dedup_hash": "abc123",
    }
    other = {
        "id": "aaaaaaaa-0000-4000-8000-000000000002",
        "entity_type": "Person",
        "confidence_score": 0.80,
    }
    GoldWriter(spy).write_cluster(
        [winner, other],
        winner,
        via_candidate="cccccccc-0000-4000-8000-000000000009",
    )
    return spy


def test_tablas_gold_existen_en_fixture() -> None:
    sql = _read_schema()
    for table in ("gold_entities", "gold_members", "gold_history"):
        cols = _columns_of_table(sql, table)
        assert cols, (
            f"la tabla public.{table} no esta en el fixture; #317 despliega la "
            "capa gold y el fixture debe reflejar el schema aplicado (ver docs/schema.md)."
        )


def test_gold_entities_columnas_que_el_writer_emite_existen() -> None:
    cols = _columns_of_table(_read_schema(), "gold_entities")
    emitted = _emit_gold_columns().entity_cols
    unknown = emitted - cols
    assert not unknown, (
        f"GoldWriter emite claves sin columna real en gold_entities: {unknown}. "
        "Actualizar el writer o el fixture si el schema gold cambió."
    )
    # canonical_aporte_id es la clave del UPSERT idempotente: debe existir.
    assert "canonical_aporte_id" in cols


def test_gold_members_columnas_que_el_writer_emite_existen() -> None:
    cols = _columns_of_table(_read_schema(), "gold_members")
    emitted = _emit_gold_columns().member_cols
    unknown = emitted - cols
    assert not unknown, (
        f"GoldWriter escribe claves sin columna real en gold_members: {unknown}. "
        "gold_members se upsertea por (gold_id, aporte_id)."
    )
    for col in ("gold_id", "aporte_id"):
        assert col in cols


def test_gold_history_columnas_que_el_writer_emite_existen() -> None:
    cols = _columns_of_table(_read_schema(), "gold_history")
    emitted = _emit_gold_columns().history_cols
    unknown = emitted - cols
    assert not unknown, (
        f"GoldWriter emite claves sin columna real en gold_history: {unknown}. "
        "gold_history es append-only (INSERT)."
    )
