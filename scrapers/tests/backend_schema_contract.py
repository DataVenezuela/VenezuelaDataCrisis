"""Helpers for offline contract tests against the frozen backend schema.

The fixture is a small, frozen SQL excerpt derived from the real
DataVenezuela/dataVenezuela migrations. These helpers intentionally parse only
the DDL patterns present in the fixture: CREATE TABLE, ALTER TABLE ADD COLUMN,
and CREATE UNIQUE INDEX.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class UniqueTarget:
    table: str
    columns: tuple[str, ...]
    partial: bool = False


@dataclass
class TableContract:
    columns: set[str] = field(default_factory=set)
    unique_targets: set[UniqueTarget] = field(default_factory=set)


@dataclass
class BackendSchemaContract:
    tables: dict[str, TableContract]

    def columns(self, table: str) -> set[str]:
        try:
            return set(self.tables[table].columns)
        except KeyError as exc:
            raise AssertionError(f"table public.{table} is missing from schema fixture") from exc

    def require_columns(self, table: str, columns: set[str]) -> None:
        existing = self.columns(table)
        missing = sorted(columns - existing)
        if missing:
            formatted = ", ".join(f"{table}.{column}" for column in missing)
            raise AssertionError(
                f"missing backend columns: {formatted}; actual={sorted(existing)}"
            )

    def optional_columns(self, table: str, columns: set[str]) -> set[str]:
        return columns & self.columns(table)

    def require_unique_target(
        self,
        table: str,
        columns: tuple[str, ...],
        *,
        allow_partial: bool = False,
    ) -> None:
        wanted = tuple(column.lower() for column in columns)
        targets = self.tables.get(table, TableContract()).unique_targets
        if any(
            target.columns == wanted and (allow_partial or not target.partial)
            for target in targets
        ):
            return
        actual = sorted(target.columns for target in targets)
        raise AssertionError(
            f"missing UNIQUE public.{table}({', '.join(wanted)}); actual={actual}"
        )


def load_backend_schema_contract(path: Path) -> BackendSchemaContract:
    tables: dict[str, TableContract] = {}
    for statement in _sql_statements(path.read_text(encoding="utf-8")):
        lowered = statement.lower()
        if lowered.startswith("create table public."):
            _parse_create_table(statement, tables)
        elif lowered.startswith("alter table public."):
            _parse_alter_table(statement, tables)
        elif lowered.startswith("create unique index "):
            _parse_unique_index(statement, tables)
    return BackendSchemaContract(tables=tables)


def _table(tables: dict[str, TableContract], table: str) -> TableContract:
    return tables.setdefault(table, TableContract())


def _sql_statements(sql: str) -> list[str]:
    cleaned_lines = []
    for line in sql.splitlines():
        cleaned_lines.append(line.split("--", 1)[0])
    return [stmt.strip() for stmt in "\n".join(cleaned_lines).split(";") if stmt.strip()]


def _parse_create_table(statement: str, tables: dict[str, TableContract]) -> None:
    match = re.search(r"create\s+table\s+public\.([a-z_][a-z0-9_]*)", statement, re.I)
    if match is None:
        return
    table_name = match.group(1).lower()
    table = _table(tables, table_name)
    body = _parenthesized_after(statement, match.end())
    for item in _split_top_level_commas(body):
        stripped = item.strip()
        if not stripped:
            continue
        head = stripped.split(None, 1)[0].lower()
        if head == "constraint":
            unique = re.search(r"\bunique\s*\((.*)\)", stripped, re.I | re.S)
            if unique:
                table.unique_targets.add(
                    UniqueTarget(table_name, tuple(_split_top_level_commas(unique.group(1))))
                )
            continue
        if head in {"check", "primary", "unique", "foreign"}:
            continue
        if re.fullmatch(r"[a-z_][a-z0-9_]*", head):
            table.columns.add(head)
            lowered = stripped.lower()
            if "primary key" in lowered or re.search(r"\bunique\b", lowered):
                table.unique_targets.add(UniqueTarget(table_name, (head,)))


def _parse_alter_table(statement: str, tables: dict[str, TableContract]) -> None:
    match = re.search(r"alter\s+table\s+public\.([a-z_][a-z0-9_]*)", statement, re.I)
    if match is None:
        return
    table = _table(tables, match.group(1).lower())
    for add in re.finditer(
        r"add\s+column\s+(?:if\s+not\s+exists\s+)?([a-z_][a-z0-9_]*)",
        statement,
        re.I,
    ):
        table.columns.add(add.group(1).lower())


def _parse_unique_index(statement: str, tables: dict[str, TableContract]) -> None:
    match = re.search(r"on\s+public\.([a-z_][a-z0-9_]*)", statement, re.I)
    if match is None:
        return
    table_name = match.group(1).lower()
    target = _parenthesized_after(statement, match.end())
    columns = tuple(_split_top_level_commas(target))
    partial = " where " in statement.lower()
    _table(tables, table_name).unique_targets.add(
        UniqueTarget(table=table_name, columns=columns, partial=partial)
    )


def _parenthesized_after(text: str, start: int) -> str:
    open_at = text.index("(", start)
    depth = 0
    for idx in range(open_at, len(text)):
        char = text[idx]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[open_at + 1 : idx]
    raise ValueError("unbalanced SQL parentheses in fixture")


def _split_top_level_commas(value: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for idx, char in enumerate(value):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(value[start:idx].strip().lower())
            start = idx + 1
    tail = value[start:].strip().lower()
    if tail:
        parts.append(tail)
    return parts
