from __future__ import annotations

import os

import pytest

from shared.storage import ClaimStore

# Estos tests requieren un Postgres real (DATABASE_URL). Sin él, se omiten,
# para no romper la suite offline que exige COMMIT_SAFETY.md.
DSN = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="DATABASE_URL no configurado; test de DB omitido")


def _make_claim(fingerprint: str) -> dict:
    return {
        "claim_id": f"claim_{fingerprint[:16]}",
        "fingerprint": fingerprint,
        "event_id": "venezuela_earthquake_demo",
        "source_id": "test_source",
        "source_name": "Test",
        "source_url": "https://example.test",
        "claim_type": "need.water",
        "description": "se necesita agua en zona de prueba",
        "location_text": "Venezuela",
        "confidence_score": 0.6,
        "verification_status": "new",
        "evidence_text": "evidencia de prueba",
        "trust_tier": "C",
        "raw_hash": "deadbeef",
        "fetched_at": "2026-06-27T00:00:00+00:00",
        "metadata": {"trust_tier": "C"},
    }


@pytest.fixture()
def store() -> ClaimStore:
    return ClaimStore(DSN)


def test_exact_dedup_persists_across_runs(store: ClaimStore) -> None:
    """Correr dos veces el mismo claim no crea duplicados (UNIQUE fingerprint)."""
    fingerprint = "a" * 64
    claim = _make_claim(fingerprint)

    first = store.upsert_claims([claim])
    second = store.upsert_claims([claim])  # mismo fingerprint => debe ignorarse

    assert first == 1
    assert second == 0


def test_distinct_fingerprints_insert(store: ClaimStore) -> None:
    claims = [_make_claim("b" * 64), _make_claim("c" * 64)]
    inserted = store.upsert_claims(claims)
    assert inserted == 2
