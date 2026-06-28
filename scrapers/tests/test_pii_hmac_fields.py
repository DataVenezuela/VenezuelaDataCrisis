from __future__ import annotations

import re

import pytest

from scrapers.sanitizers.pii_tokenizer import mask_cedula, mask_telefono, tokenize_pii_fields


SECRET = "synthetic-pii-salt"
HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")


def test_mask_cedula_is_deterministic_with_same_salt() -> None:
    first_hmac, first_masked = mask_cedula("V-12.345.678", SECRET)
    second_hmac, second_masked = mask_cedula("V12345678", SECRET)

    assert first_hmac == second_hmac
    assert first_masked == second_masked == "****5678"
    assert HEX64.match(first_hmac)


def test_mask_cedula_differs_for_different_values() -> None:
    first_hmac, _ = mask_cedula("V-12.345.678", SECRET)
    second_hmac, _ = mask_cedula("V-87.654.321", SECRET)

    assert first_hmac != second_hmac


def test_mask_cedula_distinguishes_nationality_prefix() -> None:
    """Issue #51: "V12345678" y "12345678" ya NO deben colapsar al mismo
    token — la letra de nacionalidad es parte del identificador canónico
    (misma normalización que `shared.hashing.identity_token`).

    Política documentada en docs/pipeline.md ("Política de normalización
    de `cedula_hmac`"): los rangos V/E se asignan de forma independiente,
    así que ignorar el prefijo arriesga fusionar a dos personas reales
    distintas. Cambiar esta política implica también actualizar ese doc.
    """
    venezolano_hmac, _ = mask_cedula("V12345678", SECRET)
    sin_prefijo_hmac, _ = mask_cedula("12345678", SECRET)

    assert venezolano_hmac != sin_prefijo_hmac


def test_mask_telefono_is_deterministic_and_masks_last4() -> None:
    first_hmac, first_masked = mask_telefono("+58 412 123 4567", SECRET)
    second_hmac, second_masked = mask_telefono("584121234567", SECRET)

    assert first_hmac == second_hmac
    assert first_masked == second_masked == "****4567"
    assert HEX64.match(first_hmac)


def test_mask_telefono_differs_for_different_values() -> None:
    first_hmac, _ = mask_telefono("+58 412 123 4567", SECRET)
    second_hmac, _ = mask_telefono("+58 414 765 4321", SECRET)

    assert first_hmac != second_hmac


def test_tokenize_pii_fields_drops_original_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PII_SALT", SECRET)

    result = tokenize_pii_fields(
        {
            "full_name": "Ana Perez",
            "cedula": "V-12.345.678",
            "telefono": "+58 412 123 4567",
            "fuente": "synthetic_source",
        }
    )

    assert result["full_name"] == "Ana Perez"
    assert result["fuente"] == "synthetic_source"
    assert result["cedula_masked"] == "****5678"
    assert result["telefono_masked"] == "****4567"
    assert HEX64.match(result["cedula_hmac"])
    assert HEX64.match(result["telefono_hmac"])
    assert "cedula" not in result
    assert "telefono" not in result
    assert "V-12.345.678" not in str(result)
    assert "+58 412 123 4567" not in str(result)


def test_tokenize_pii_fields_requires_pii_salt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PII_SALT", raising=False)

    with pytest.raises(RuntimeError, match="Falta variable PII_SALT"):
        tokenize_pii_fields({"cedula": "V-12.345.678"})


def test_tokenize_pii_fields_does_not_leak_when_only_phone_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PII_SALT", SECRET)

    result = tokenize_pii_fields({"phone": "+58 412 123 4567", "nota": "contacto verificado"})

    assert result["telefono_masked"] == "****4567"
    assert "telefono_hmac" in result
    assert "phone" not in result
    assert "+58" not in str(result)
