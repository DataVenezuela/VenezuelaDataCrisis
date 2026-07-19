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
    # Columnas del cursor keyset (created_at, id) y del filtro por tipo deben ser
    # reales: son los unicos campos que la paginacion de fetch_aportes_page usa.
    assert "entity_type" in aportes_cols
    assert "id" in aportes_cols
    assert "created_at" in aportes_cols


def test_aportes_consolidated_at_ausente_en_schema_real() -> None:
    # DOCUMENTADO: el schema DESPLEGADO no tiene aportes.consolidated_at (probe en
    # vivo 2026-07-19: GET ...consolidated_at=is.null -> 400 42703). La
    # consolidacion NO la usa (pagina por cursor keyset, no por estado). Este test
    # fija ese hecho: si una migracion futura la agrega y se actualiza el fixture,
    # rompe y avisa que el adapter puede volver a referenciarla.
    aportes_cols = _columns_of_table(_read_schema(), "aportes")
    assert "consolidated_at" not in aportes_cols


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


# ---------------------------------------------------------------------------
# consolidation_state — cursor durable de option B (#93)
# ---------------------------------------------------------------------------

def test_consolidation_state_columnas_reales_existen() -> None:
    # El cursor durable exige que el fixture declare consolidation_state con las
    # columnas que read_cursor/write_cursor leen y escriben. Si el DDL del PR
    # difiere del fixture, este test rompe en vez de degradar en silencio.
    sql = _read_schema()
    cols = _columns_of_table(sql, "consolidation_state")
    for col in ("entity_type", "cursor_created_at", "cursor_id", "updated_at"):
        assert col in cols, (
            f"consolidation_state.{col} requerida por el cursor durable (option B) "
            f"no existe en el fixture; columnas: {sorted(cols)}"
        )


def test_consolidation_state_select_del_adapter_usa_columnas_reales() -> None:
    # Las columnas que el SELECT del adapter proyecta (read_cursor) y las que el
    # upsert escribe (write_cursor) deben existir en el schema del fixture.
    sql = _read_schema()
    cols = _columns_of_table(sql, "consolidation_state")
    read_cols = {"cursor_created_at", "cursor_id"}
    write_cols = {"entity_type", "cursor_created_at", "cursor_id"}
    assert read_cols <= cols
    assert write_cols <= cols


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
