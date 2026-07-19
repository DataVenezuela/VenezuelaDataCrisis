"""Puertos de acceso a datos para Stage 2 y sus fakes en memoria.

La decision de arquitectura del backend (PostgREST directo vs proxy Vercel)
sigue SIN tomarse por el equipo. Para no acoplar los jobs a ninguna de las dos
opciones, todo el acceso a datos pasa por Protocols. Los adapters concretos de
produccion NO se implementan aqui: quedan pendientes de esa decision. Los tests
usan los Fakes en memoria definidos al final de este modulo.

Modelo de datos (contrato minimo, agnostico del backend)
--------------------------------------------------------
Un "aporte no consolidado" es un ``dict[str, object]`` con al menos:

  - ``id``:          identificador unico del aporte (str).
  - ``entity_type``: "Event" | "AcopioCenter" | "Person".
  - ``dedup_hash``:  fingerprint v1 de contenido (str | None). None => no agrupa.
  - ``trust_tier``:  tier de confianza de la fuente (str, p.ej. "A".."D").
  - ``source_id``:   identificador de la fuente/origen (str), para desempate.
  - ``created_at``:  timestamp ISO-8601 (str), para desempate secundario.
  - ``payload``:     dict con el contenido canonico a materializar (dict).

Los campos concretos y su origen real (columnas de ``aportes`` en el backend,
mapeo de ``trust_tier``) quedan pendientes de la definicion del equipo; ver el
docstring de ``pick_winner`` en ``consolidation_job``.
"""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

# Alias del tipo de record para legibilidad. Es intencionalmente laxo
# (dict[str, object]) para no acoplar el job a un modelo tipado del backend
# que aun no esta definido.
Record = dict[str, object]


@runtime_checkable
class ConsolidationDataPort(Protocol):
    """Contrato de acceso a datos que necesita el job de consolidacion.

    Cubre el flujo de Event/AcopioCenter (dedup exacto auto-merge por
    dedup_hash). Los metodos de Person se declaran para completitud del
    contrato, pero el job de esta faceta (#91) NO los usa: Person (#92) es de
    otro dev y su consolidacion exige revision humana (nunca auto-merge).
    """

    def fetch_aportes_page(
        self, entity_type: str, batch_size: int, cursor: tuple[str, str]
    ) -> list[Record]:
        """Devuelve hasta ``batch_size`` aportes de ``entity_type`` tras ``cursor``.

        Pagina por cursor keyset ``(created_at, id)``: devuelve las filas cuyo
        ``(created_at, id)`` es estrictamente mayor que ``cursor``, ordenadas
        ascendentemente por ese par (orden total estable => ``pick_winner``
        determinista). NO filtra por ``consolidated_at`` (columna inexistente en
        el schema real): el cursor es lo unico que pagina y cada corrida re-escanea
        el set completo desde ``cursor=("", "")`` (o el sentinela inicial). Una
        lista vacia indica que no quedan mas filas tras el cursor.
        """
        ...

    def upsert_canonical(self, entity_type: str, record: Record) -> None:
        """Materializa (crea o actualiza) la fila canonica de ``record``.

        Idempotente por ``dedup_hash``: re-upsertar el mismo hash con el mismo
        ganador no debe duplicar filas.
        """
        ...

    # --- Person: parte del contrato, fuera de alcance de este job (#91) ------

    def fetch_person_candidates(self, block_keys: list[str]) -> list[Record]:
        """Aportes person cuyo block_keys solapa con block_keys dados.

        Retorna candidatos para el similarity scorer (#92). NO lo usa el job
        de #91 (Event/AcopioCenter auto-merge).
        """
        ...

    # --- ciclo de vida -------------------------------------------------------

    def close(self) -> None:
        """Libera recursos del adapter (p.ej. el httpx.Client del real).

        El caller (CLI/main) DEBE llamarlo al terminar, idealmente en un
        ``try/finally``. En el fake es un no-op (no abre recursos).
        """
        ...


class FakeInMemoryAdapter:
    """Implementacion en memoria de `ConsolidationDataPort` para tests offline.

    Guarda los aportes y las filas canonicas (indexadas por (entity_type,
    dedup_hash)). Sin red ni DB real.

    Semantica clave para los tests de #91:
      - ``fetch_aportes_page`` pagina por cursor keyset ``(created_at, id)``:
        devuelve las filas del tipo cuyo par es estrictamente mayor que el cursor,
        ordenadas ascendentemente. NO hay estado de "consolidado"; cada corrida
        re-escanea desde el cursor inicial (idempotencia via ``upsert_canonical``).
      - ``upsert_canonical`` reemplaza por (entity_type, dedup_hash): una sola
        fila canonica por hash sin importar cuantas veces se upserte.
    """

    def __init__(self, aportes: list[Record] | None = None) -> None:
        self.aportes: list[Record] = list(aportes or [])
        # Fila canonica por (entity_type, dedup_hash).
        self.canonical: dict[tuple[str, str], Record] = {}
        # Contador de auditoria para asserts en tests.
        self.upsert_calls: int = 0

    @staticmethod
    def _keyset(rec: Record) -> tuple[str, str]:
        return (str(rec.get("created_at", "")), str(rec.get("id", "")))

    def fetch_aportes_page(
        self, entity_type: str, batch_size: int, cursor: tuple[str, str]
    ) -> list[Record]:
        ordered = sorted(
            (rec for rec in self.aportes if rec.get("entity_type") == entity_type),
            key=self._keyset,
        )
        after = [rec for rec in ordered if self._keyset(rec) > cursor]
        return after[:batch_size]

    def upsert_canonical(self, entity_type: str, record: Record) -> None:
        dedup_hash = record.get("dedup_hash")
        if not isinstance(dedup_hash, str) or not dedup_hash:
            raise ValueError("upsert_canonical requiere un dedup_hash no vacio")
        self.canonical[(entity_type, dedup_hash)] = record
        self.upsert_calls += 1

    def fetch_person_candidates(self, block_keys: list[str]) -> list[Record]:
        if not block_keys:
            return []
        key_set = set(block_keys)
        return [
            rec
            for rec in self.aportes
            if rec.get("entity_type") == "Person"
            and any(k in key_set for k in (rec.get("block_keys") or []))
        ]

    def close(self) -> None:
        # No-op: el fake no abre recursos. Presente por el contrato para que el
        # caller pueda cerrar el port de forma uniforme (real o fake).
        return None


# ---------------------------------------------------------------------------
# Gold tables port + fake (#266)
# ---------------------------------------------------------------------------


@runtime_checkable
class GoldDataPort(Protocol):
    """Contrato de I/O para el writer de tablas gold.

    El adapter de produccion (Supabase PostgREST) queda pendiente; los tests
    usan `FakeGoldAdapter` (sin red ni DB).
    """

    def upsert_gold_entity(self, entity: Record) -> str:
        """UPSERT en gold_entities por canonical_aporte_id.

        Si ya existe una fila con ese canonical_aporte_id la actualiza; si no,
        la crea. Devuelve el gold_id (UUID str) de la fila resultante.
        """
        ...

    def upsert_gold_member(
        self,
        gold_id: str,
        aporte_id: str,
        via_candidate: str | None = None,
    ) -> None:
        """UPSERT en gold_members por (gold_id, aporte_id).

        Re-upsertar la misma pareja es no-op (idempotente).
        El via_candidate se almacena en la primera insercion; re-upsertar con
        un via_candidate distinto actualiza el campo existente.
        """
        ...

    def insert_gold_history(self, event: Record) -> None:
        """INSERT en gold_history (append-only, log inmutable)."""
        ...

    def close(self) -> None:
        """Libera recursos (no-op en el fake)."""
        ...


class FakeGoldAdapter:
    """Implementacion en memoria de GoldDataPort para tests offline.

    - entities:   canonical_aporte_id -> fila completa (incluye gold_id generado).
    - members:    set de (gold_id, aporte_id) ya insertadas (para asserts de membership).
    - member_via: (gold_id, aporte_id) -> via_candidate (siempre actualizado, UPSERT).
    - history:    lista ordenada de eventos (append-only).
    """

    def __init__(self) -> None:
        self.entities: dict[str, Record] = {}
        self.members: set[tuple[str, str]] = set()
        self.member_via: dict[tuple[str, str], str | None] = {}
        self.history: list[Record] = []

        self.upsert_entity_calls: int = 0
        self.upsert_member_calls: int = 0
        self.insert_history_calls: int = 0

    def upsert_gold_entity(self, entity: Record) -> str:
        canonical_aporte_id = str(entity.get("canonical_aporte_id") or "")
        if not canonical_aporte_id:
            raise ValueError("upsert_gold_entity: canonical_aporte_id requerido")

        self.upsert_entity_calls += 1
        existing = self.entities.get(canonical_aporte_id)
        if existing is not None:
            existing.update(entity)
            return str(existing["gold_id"])

        gold_id = str(uuid.uuid4())
        row: Record = dict(entity)
        row["gold_id"] = gold_id
        self.entities[canonical_aporte_id] = row
        return gold_id

    def upsert_gold_member(
        self,
        gold_id: str,
        aporte_id: str,
        via_candidate: str | None = None,
    ) -> None:
        self.upsert_member_calls += 1
        key = (gold_id, aporte_id)
        self.members.add(key)
        self.member_via[key] = via_candidate

    def insert_gold_history(self, event: Record) -> None:
        self.insert_history_calls += 1
        self.history.append(dict(event))

    def close(self) -> None:
        return None
