"""Puerto de acceso a datos para la consolidacion (Stage 2) y un fake en memoria.

La decision de arquitectura del backend (PostgREST directo vs proxy Vercel)
sigue SIN tomarse por el equipo. Para no acoplar el job a ninguna de las dos
opciones, todo el acceso a datos pasa por `ConsolidationDataPort` (un Protocol).
El adapter concreto de produccion NO se implementa aqui: queda pendiente de esa
decision. Los tests usan `FakeInMemoryAdapter`, sin red ni base de datos real.

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

    def fetch_unconsolidated(self, entity_type: str, batch_size: int) -> list[Record]:
        """Devuelve hasta ``batch_size`` aportes NO consolidados de ``entity_type``.

        El orden debe ser estable entre llamadas para que la seleccion de
        ganador sea determinista. Una lista vacia indica que no queda pendiente.
        """
        ...

    def upsert_canonical(self, entity_type: str, record: Record) -> None:
        """Materializa (crea o actualiza) la fila canonica de ``record``.

        Idempotente por ``dedup_hash``: re-upsertar el mismo hash con el mismo
        ganador no debe duplicar filas.
        """
        ...

    def mark_consolidated(self, aporte_ids: list[str]) -> None:
        """Marca los aportes ``aporte_ids`` como consolidados (consolidated_at).

        Idempotente: re-marcar un aporte ya consolidado no es un error.
        """
        ...

    # --- Person: parte del contrato, fuera de alcance de este job (#91) ------

    def fetch_person_candidates(self, batch_size: int) -> list[Record]:
        """Candidatos de Person para revision humana. NO lo usa el job de #91."""
        ...


class FakeInMemoryAdapter:
    """Implementacion en memoria de `ConsolidationDataPort` para tests offline.

    Guarda los aportes, las filas canonicas (indexadas por (entity_type,
    dedup_hash)) y el conjunto de aportes consolidados. Sin red ni DB real.

    Semantica clave para los tests de #91:
      - ``fetch_unconsolidated`` solo devuelve aportes de ese tipo cuyo id NO
        esta en ``consolidated_ids`` (incremental / idempotente al re-correr).
      - ``upsert_canonical`` reemplaza por (entity_type, dedup_hash): una sola
        fila canonica por hash sin importar cuantas veces se upserte.
      - ``mark_consolidated`` acumula ids en un set (re-marcar no duplica).
    """

    def __init__(self, aportes: list[Record] | None = None) -> None:
        self.aportes: list[Record] = list(aportes or [])
        # Fila canonica por (entity_type, dedup_hash).
        self.canonical: dict[tuple[str, str], Record] = {}
        # Ids de aportes ya consolidados.
        self.consolidated_ids: set[str] = set()
        # Contadores de auditoria para asserts en tests.
        self.upsert_calls: int = 0
        self.mark_calls: int = 0

    def fetch_unconsolidated(self, entity_type: str, batch_size: int) -> list[Record]:
        pending = [
            rec
            for rec in self.aportes
            if rec.get("entity_type") == entity_type
            and str(rec.get("id")) not in self.consolidated_ids
        ]
        return pending[:batch_size]

    def upsert_canonical(self, entity_type: str, record: Record) -> None:
        dedup_hash = record.get("dedup_hash")
        if not isinstance(dedup_hash, str) or not dedup_hash:
            raise ValueError("upsert_canonical requiere un dedup_hash no vacio")
        self.canonical[(entity_type, dedup_hash)] = record
        self.upsert_calls += 1

    def mark_consolidated(self, aporte_ids: list[str]) -> None:
        self.consolidated_ids.update(aporte_ids)
        self.mark_calls += 1

    def fetch_person_candidates(self, batch_size: int) -> list[Record]:
        # Presente por el contrato; el job de #91 no lo usa. Person exige
        # revision humana y queda para #92.
        pending = [
            rec
            for rec in self.aportes
            if rec.get("entity_type") == "Person"
            and str(rec.get("id")) not in self.consolidated_ids
        ]
        return pending[:batch_size]
