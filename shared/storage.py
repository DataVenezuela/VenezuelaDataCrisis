from __future__ import annotations

from contextlib import contextmanager
from typing import Iterable, Iterator

import psycopg
from psycopg.types.json import Jsonb


# Mapa: llave (inglés) del dict de claim del pipeline -> columna (español) en la DB.
_CLAIM_FIELD_TO_COLUMN = {
    "claim_id": "id",
    "fingerprint": "huella",
    "event_id": "evento_id",
    "source_id": "fuente_id",
    "source_name": "fuente_nombre",
    "source_url": "fuente_url",
    "claim_type": "tipo",
    "description": "descripcion",
    "location_text": "ubicacion_texto",
    "geo_code": "geo_codigo",
    "geo_zone": "geo_zona",
    "geo_estado": "geo_estado",
    "geo_municipio": "geo_municipio",
    "lat": "lat",
    "lon": "lon",
    "confidence_score": "confianza",
    "verification_status": "estado_verificacion",
    "evidence_text": "evidencia_texto",
    "trust_tier": "nivel_confianza",
    "raw_hash": "hash_crudo",
    "fetched_at": "capturado_en",
    "metadata": "metadatos",
}

_CLAIM_COLUMNS = list(_CLAIM_FIELD_TO_COLUMN.values())

# capturado_en se castea a timestamptz; metadatos es JSONB.
_CLAIM_VALUES = ", ".join(
    f"%({col})s::timestamptz" if col == "capturado_en" else f"%({col})s"
    for col in _CLAIM_COLUMNS
)

_INSERT_AFIRMACION = f"""
INSERT INTO afirmacion ({", ".join(_CLAIM_COLUMNS)})
VALUES ({_CLAIM_VALUES})
ON CONFLICT (huella) DO NOTHING
"""

_INSERT_OBSERVACION = """
INSERT INTO observacion (fuente_id, fuente_nombre, fuente_url, nivel_confianza, crudo, hash_crudo, capturado_en)
VALUES (
    %(fuente_id)s, %(fuente_nombre)s, %(fuente_url)s, %(nivel_confianza)s,
    %(crudo)s, %(hash_crudo)s, %(capturado_en)s::timestamptz
)
ON CONFLICT (fuente_id, hash_crudo) DO NOTHING
"""


class ClaimStore:
    """Acceso a Postgres para observaciones y afirmaciones.

    Es el punto de cambio para Supabase: solo cambia el DSN.
    El dedup EXACTO persistente sale del UNIQUE sobre `afirmacion.huella`.
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection]:
        with psycopg.connect(self.dsn) as conn:
            yield conn

    def upsert_claims(self, claims: Iterable[dict]) -> int:
        """Inserta afirmaciones; los duplicados exactos (misma huella) se ignoran.

        Devuelve el número de afirmaciones realmente insertadas (nuevas)."""
        rows = [self._claim_params(claim) for claim in claims]
        if not rows:
            return 0
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(_INSERT_AFIRMACION, rows)
            inserted = cur.rowcount
        return inserted if inserted is not None and inserted >= 0 else len(rows)

    def upsert_observations(self, documents: Iterable[dict]) -> int:
        """Guarda el crudo de cada documento como observacion (JSONB)."""
        rows = [self._observation_params(doc) for doc in documents]
        if not rows:
            return 0
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(_INSERT_OBSERVACION, rows)
            inserted = cur.rowcount
        return inserted if inserted is not None and inserted >= 0 else len(rows)

    def persist_run(self, documents: Iterable[dict], claims: Iterable[dict]) -> dict:
        """Persiste observaciones y afirmaciones en UNA sola transacción (atómico).

        Evita el estado parcial (observaciones sin sus afirmaciones) ante un crash."""
        obs_rows = [self._observation_params(doc) for doc in documents]
        claim_rows = [self._claim_params(claim) for claim in claims]
        with self._connect() as conn, conn.cursor() as cur:
            obs_inserted = 0
            if obs_rows:
                cur.executemany(_INSERT_OBSERVACION, obs_rows)
                obs_inserted = cur.rowcount if cur.rowcount and cur.rowcount >= 0 else len(obs_rows)
            claims_inserted = 0
            if claim_rows:
                cur.executemany(_INSERT_AFIRMACION, claim_rows)
                claims_inserted = cur.rowcount if cur.rowcount and cur.rowcount >= 0 else len(claim_rows)
        return {"observations_inserted": obs_inserted, "claims_inserted": claims_inserted}

    def count_claims(self) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM afirmacion")
            return int(cur.fetchone()[0])

    @staticmethod
    def _claim_params(claim: dict) -> dict:
        params = {
            column: claim.get(field)
            for field, column in _CLAIM_FIELD_TO_COLUMN.items()
        }
        params["estado_verificacion"] = claim.get("verification_status") or "new"
        params["metadatos"] = Jsonb(claim.get("metadata") or {})
        return params

    @staticmethod
    def _observation_params(document: dict) -> dict:
        return {
            "fuente_id": document.get("source_id"),
            "fuente_nombre": document.get("source_name"),
            "fuente_url": document.get("source_url"),
            "nivel_confianza": document.get("trust_tier"),
            "crudo": Jsonb(document),
            "hash_crudo": document.get("raw_hash"),
            "capturado_en": document.get("fetched_at"),
        }
