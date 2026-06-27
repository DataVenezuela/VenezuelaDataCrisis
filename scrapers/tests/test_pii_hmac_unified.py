"""Tests de equivalencia del HMAC de PII unificado (issue #30).

Verifican que el redactor (scrapers.sanitizers.pii_tokenizer.hmac_token) y el
matcher (shared.hashing.identity_token) comparten UNA sola normalización
canónica, de modo que la MISMA cédula en formatos distintos produce el MISMO
token.

Se usa `monkeypatch.setenv` para no filtrar PII_HMAC_SECRET a otras suites
(p.ej. test_sanitizer.py, que asume el redactor SIN secret).
"""

from __future__ import annotations

import re

from scrapers.sanitizers.pii_tokenizer import HMAC_PREFIX, hmac_digest, hmac_token
from shared.hashing import hmac_hex, identity_token, normalize_identifier


SECRET = "test-secret-unified"

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")


def test_redactor_and_matcher_produce_same_token(monkeypatch):
    monkeypatch.setenv("PII_HMAC_SECRET", SECRET)

    redactor_token = hmac_token("V-12.345.678").removeprefix(HMAC_PREFIX)
    matcher_token = identity_token("V12345678", SECRET)

    assert redactor_token == matcher_token


def test_different_formats_collapse_to_same_token(monkeypatch):
    monkeypatch.setenv("PII_HMAC_SECRET", SECRET)

    formats = ["V-12.345.678", "V12345678", "v 12 345 678", "V.12.345.678"]
    tokens = {hmac_token(value).removeprefix(HMAC_PREFIX) for value in formats}

    assert len(tokens) == 1


def test_hmac_digest_matches_identity_token(monkeypatch):
    monkeypatch.setenv("PII_HMAC_SECRET", SECRET)

    assert hmac_digest("V-12.345.678") == identity_token("V12345678", SECRET)


def test_redactor_delegates_to_shared_hmac_hex(monkeypatch):
    monkeypatch.setenv("PII_HMAC_SECRET", SECRET)

    assert hmac_token("V12345678").removeprefix(HMAC_PREFIX) == hmac_hex("V-12.345.678", SECRET)


def test_normalize_identifier_strips_punctuation_and_spaces():
    assert normalize_identifier("V-12.345.678") == normalize_identifier("V12345678")
    assert normalize_identifier("V-12.345.678") == "v12345678"


# --- Blindaje del contrato de exportación de `cedula_hmac` (PR #33) ---------
#
# Estos tests fijan que la forma EXPORTABLE (la que puebla `cedula_hmac`) es
# hex puro de 64 chars SIN prefijo, y que la forma INTERNA (`hmac_token`) SÍ
# lleva el prefijo "hmac_sha256:". Objetivo: que nadie cablee por error el
# token prefijado hacia el campo exportado / la columna DB VARCHAR(64).


def test_identity_token_is_export_safe_64_hex_without_prefix(monkeypatch):
    monkeypatch.setenv("PII_HMAC_SECRET", SECRET)

    token = identity_token("V-12.345.678", SECRET)

    assert not token.startswith(HMAC_PREFIX)
    assert _HEX64.match(token), token
    assert len(token) == 64


def test_hmac_digest_is_export_safe_64_hex_without_prefix(monkeypatch):
    monkeypatch.setenv("PII_HMAC_SECRET", SECRET)

    digest = hmac_digest("V-12.345.678")

    assert not digest.startswith(HMAC_PREFIX)
    assert _HEX64.match(digest), digest
    assert len(digest) == 64


def test_hmac_hex_is_export_safe_64_hex_without_prefix(monkeypatch):
    monkeypatch.setenv("PII_HMAC_SECRET", SECRET)

    value = hmac_hex("V-12.345.678", SECRET)

    assert not value.startswith(HMAC_PREFIX)
    assert _HEX64.match(value), value
    assert len(value) == 64


def test_hmac_digest_equals_identity_token_export_contract(monkeypatch):
    """La fuente exportada (`hmac_digest`) debe coincidir exactamente con
    `identity_token`, la única fuente declarada de `cedula_hmac`."""
    monkeypatch.setenv("PII_HMAC_SECRET", SECRET)

    assert hmac_digest("V12345678") == identity_token("V12345678", SECRET)


def test_hmac_token_is_internal_form_with_prefix(monkeypatch):
    """`hmac_token` es la forma INTERNA (logs/redacción): SIEMPRE lleva prefijo
    y por tanto NUNCA debe exportarse a `cedula_hmac`."""
    monkeypatch.setenv("PII_HMAC_SECRET", SECRET)

    token = hmac_token("V-12.345.678")

    assert token.startswith(HMAC_PREFIX)
    # Tras quitar el prefijo interno, queda el hex puro exportable de 64 chars.
    assert token != token.removeprefix(HMAC_PREFIX)
    assert _HEX64.match(token.removeprefix(HMAC_PREFIX))


def test_identity_token_never_carries_redaction_prefix(monkeypatch):
    """Regresión: identity_token NUNCA debe empezar con HMAC_PREFIX, para que
    nadie confunda la forma de redacción interna con la fuente de export."""
    monkeypatch.setenv("PII_HMAC_SECRET", SECRET)

    for value in ["V-12.345.678", "V12345678", "v 12 345 678", "E-9.999.999"]:
        assert not identity_token(value, SECRET).startswith(HMAC_PREFIX)
