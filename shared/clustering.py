from __future__ import annotations

import argparse

import psycopg

from scrapers.dedup.matcher import match_key
from shared.config import get_database_url


# Afirmaciones que todavía no pertenecen a ninguna entidad.
_SELECT_UNASSIGNED = """
SELECT a.id, a.tipo, a.geo_codigo, a.capturado_en, a.metadatos
FROM afirmacion a
LEFT JOIN membresia_afirmacion m ON m.afirmacion_id = a.id
WHERE m.afirmacion_id IS NULL
"""

# Entidad por llave decisiva (idempotente). Si ya existe, no crea otra.
_UPSERT_ENTITY_BY_KEY = """
INSERT INTO entidad (dominio, clave_match)
VALUES (%(dominio)s, %(clave)s)
ON CONFLICT (clave_match) WHERE clave_match IS NOT NULL DO NOTHING
"""

_SELECT_ENTITY_BY_KEY = "SELECT uuid FROM entidad WHERE clave_match = %(clave)s"

# Entidad standalone (sin señal fuerte): cada afirmación en la suya.
_INSERT_STANDALONE_ENTITY = """
INSERT INTO entidad (dominio) VALUES (%(dominio)s) RETURNING uuid
"""

_INSERT_MEMBERSHIP = """
INSERT INTO membresia_afirmacion (entidad_uuid, afirmacion_id, puntaje, razon)
VALUES (%(entidad_uuid)s, %(afirmacion_id)s, %(puntaje)s, %(razon)s)
ON CONFLICT (entidad_uuid, afirmacion_id) DO NOTHING
"""


def assign_entities(dsn: str) -> dict:
    """Asigna cada afirmación sin entidad a una entidad por su llave decisiva.

    Determinista e idempotente: correrlo de nuevo no crea duplicados."""
    entities_created = 0
    memberships_created = 0
    standalone = 0

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(_SELECT_UNASSIGNED)
            rows = cur.fetchall()

        for claim_id, tipo, geo_codigo, capturado_en, metadatos in rows:
            claim = {
                "claim_type": tipo,
                "geo_code": geo_codigo,
                "fetched_at": capturado_en,
                "metadata": metadatos or {},
            }
            dominio, clave = match_key(claim)

            with conn.cursor() as cur:
                if clave is None:
                    cur.execute(_INSERT_STANDALONE_ENTITY, {"dominio": dominio})
                    entidad_uuid = cur.fetchone()[0]
                    entities_created += 1
                    standalone += 1
                    razon = "standalone:sin_senal_fuerte"
                else:
                    cur.execute(_UPSERT_ENTITY_BY_KEY, {"dominio": dominio, "clave": clave})
                    if cur.rowcount and cur.rowcount > 0:
                        entities_created += 1
                    cur.execute(_SELECT_ENTITY_BY_KEY, {"clave": clave})
                    entidad_uuid = cur.fetchone()[0]
                    razon = f"deterministic:{clave}"

                cur.execute(
                    _INSERT_MEMBERSHIP,
                    {
                        "entidad_uuid": entidad_uuid,
                        "afirmacion_id": claim_id,
                        "puntaje": 1.0,
                        "razon": razon,
                    },
                )
                if cur.rowcount and cur.rowcount > 0:
                    memberships_created += 1

        conn.commit()

    return {
        "claims_processed": len(rows),
        "entities_created": entities_created,
        "memberships_created": memberships_created,
        "standalone_entities": standalone,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m shared.clustering",
        description="Asigna afirmaciones a entidades por llave decisiva (determinista)",
    )
    parser.parse_args()

    dsn = get_database_url()
    if not dsn:
        raise SystemExit("DATABASE_URL no está configurado (revisa .env)")

    result = assign_entities(dsn)
    print("Clustering determinista finalizado")
    print(f"Afirmaciones procesadas: {result['claims_processed']}")
    print(f"Entidades creadas: {result['entities_created']}")
    print(f"Membresías creadas: {result['memberships_created']}")
    print(f"Entidades standalone (sin señal fuerte): {result['standalone_entities']}")


if __name__ == "__main__":
    main()
