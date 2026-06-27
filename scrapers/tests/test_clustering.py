from __future__ import annotations

import os

import psycopg
import pytest

from shared.clustering import assign_entities
from shared.storage import ClaimStore

DSN = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="DATABASE_URL no configurado; test de DB omitido")


def _claim(fingerprint: str, geo_code: str | None) -> dict:
    return {
        "claim_id": f"claim_{fingerprint[:16]}",
        "fingerprint": fingerprint,
        "event_id": "venezuela_earthquake_demo",
        "source_id": "test_source",
        "source_name": "Test",
        "source_url": "https://example.test",
        "claim_type": "need.water",
        "description": "se necesita agua",
        "location_text": "Venezuela",
        "geo_code": geo_code,
        "geo_zone": "Caracas" if geo_code else None,
        "confidence_score": 0.6,
        "verification_status": "new",
        "evidence_text": "evidencia",
        "trust_tier": "C",
        "raw_hash": "x",
        "fetched_at": "2026-06-27T00:00:00+00:00",
        "metadata": {},
    }


@pytest.fixture()
def clean_db():
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE afirmacion, observacion, entidad, membresia_afirmacion CASCADE")
        conn.commit()
    yield


def _entity_count() -> int:
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM entidad")
        return int(cur.fetchone()[0])


def test_same_key_claims_share_one_entity(clean_db):
    store = ClaimStore(DSN)
    # Dos claims con misma zona+tipo+día (distinta fuente/huella) -> una entidad.
    store.upsert_claims(
        [
            _claim("a" * 64, "VE-DC-LIBERTADOR-CARACAS"),
            _claim("b" * 64, "VE-DC-LIBERTADOR-CARACAS"),
            _claim("c" * 64, "VE-ZUL-MARACAIBO-MARACAIBO"),  # distinta zona -> otra entidad
        ]
    )
    result = assign_entities(DSN)
    assert result["claims_processed"] == 3
    assert result["memberships_created"] == 3
    # 2 entidades: Caracas (2 claims) + Maracaibo (1 claim).
    assert _entity_count() == 2


def test_assign_is_idempotent(clean_db):
    store = ClaimStore(DSN)
    store.upsert_claims([_claim("a" * 64, "VE-DC-LIBERTADOR-CARACAS")])
    first = assign_entities(DSN)
    second = assign_entities(DSN)  # re-correr no debe crear nada nuevo
    assert first["memberships_created"] == 1
    assert second["memberships_created"] == 0
    assert _entity_count() == 1


def test_no_geo_is_standalone(clean_db):
    store = ClaimStore(DSN)
    store.upsert_claims(
        [_claim("a" * 64, None), _claim("b" * 64, None)]  # sin geo -> cada una su entidad
    )
    result = assign_entities(DSN)
    assert result["standalone_entities"] == 2
    assert _entity_count() == 2
