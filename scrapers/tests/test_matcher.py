from __future__ import annotations

from scrapers.dedup.matcher import match_key


def _claim(**kwargs) -> dict:
    base = {
        "claim_type": "need.water",
        "geo_code": "VE-DC-LIBERTADOR-CARACAS",
        "fetched_at": "2026-06-27T00:00:00+00:00",
        "metadata": {},
    }
    base.update(kwargs)
    return base


def test_same_type_geo_day_share_key():
    a = match_key(_claim())
    b = match_key(_claim(fetched_at="2026-06-27T18:30:00+00:00"))  # mismo día
    assert a[0] == "need"
    assert a[1] is not None
    assert a == b


def test_different_geo_different_key():
    a = match_key(_claim())
    b = match_key(_claim(geo_code="VE-ZUL-MARACAIBO-MARACAIBO"))
    assert a[1] != b[1]


def test_no_geo_no_automerge():
    domain, key = match_key(_claim(geo_code=None))
    assert domain == "need"
    assert key is None


def test_person_requires_identity_token():
    domain, key = match_key(_claim(claim_type="casualties.missing", metadata={}))
    assert domain == "person"
    assert key is None  # sin token no se fusiona

    domain, key = match_key(
        _claim(claim_type="casualties.missing", metadata={"cedula_hmac": "abc123"})
    )
    assert domain == "person"
    assert key == "person|token|abc123"
