"""Tests offline para la primitiva canónica de identidad HMAC (shared/hashing.py)
y el normalizador/blocking de nombres de persona (scrapers/normalizers/person.py).

Estos módulos son la "fuente única de verdad" para el `cedula_hmac` (issue #12) y el
blocking por nombre (issue #18). No tocan DB ni red: corren en cualquier CI.
"""

from __future__ import annotations

import pytest

from scrapers.normalizers.person import derive_is_minor, name_key, normalize_person_name
from shared.hashing import identity_token, sha256_hex


SECRET = "test-secret"


def test_identity_token_is_deterministic():
    assert identity_token("V12345678", SECRET) == identity_token("V12345678", SECRET)


def test_identity_token_normalizes_before_hashing():
    # "V-12.345.678" y "V12345678" deben producir el MISMO token.
    assert identity_token("V-12.345.678", SECRET) == identity_token("V12345678", SECRET)


def test_identity_token_is_hex_sha256():
    token = identity_token("V12345678", SECRET)
    assert token is not None
    assert len(token) == 64
    int(token, 16)  # es hexadecimal válido


def test_identity_token_depends_on_secret():
    assert identity_token("V12345678", "secret-a") != identity_token("V12345678", "secret-b")


def test_identity_token_none_or_empty_returns_none():
    assert identity_token(None, SECRET) is None
    assert identity_token("", SECRET) is None
    assert identity_token("   ", SECRET) is None


def test_identity_token_requires_secret():
    with pytest.raises(ValueError):
        identity_token("V12345678", "")


def test_sha256_hex_handles_none():
    assert sha256_hex(None) == sha256_hex("")
    assert len(sha256_hex("hola")) == 64


def test_normalize_person_name_strips_honorifics_and_accents():
    assert normalize_person_name("Sr. José  Pérez") == "jose perez"
    assert normalize_person_name("Dra. María Rodríguez") == "maria rodriguez"


def test_normalize_person_name_empty():
    assert normalize_person_name(None) == ""
    assert normalize_person_name("   ") == ""


def test_name_key_is_order_invariant():
    assert name_key("Jose Perez") == name_key("Perez Jose")


def test_name_key_blocks_same_person_with_honorific():
    assert name_key("Sr. Jose Perez") == name_key("perez jose")


def test_derive_is_minor_true_under_18():
    assert derive_is_minor({"min": 15, "max": 15}) is True


def test_derive_is_minor_false_at_18_and_above():
    assert derive_is_minor({"min": 18, "max": 18}) is False
    assert derive_is_minor({"min": 35, "max": 35}) is False


def test_derive_is_minor_none_without_age():
    assert derive_is_minor(None) is None


def test_derive_is_minor_uses_lower_bound_of_estimated_range():
    """Un rango estimado debe proteger al menor aunque el extremo superior sea mayor de edad."""
    assert derive_is_minor({"min": 10, "max": 60}) is True
