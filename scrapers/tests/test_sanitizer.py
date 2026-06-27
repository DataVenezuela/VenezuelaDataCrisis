from scrapers.sanitizers.pii_detector import detect_pii
from scrapers.sanitizers.pii_redactor import redact_pii
from scrapers.sanitizers.pii_tokenizer import normalize_id_document, normalize_phone, pii_token


def test_redacts_phone_and_document():
    text = "Contacto +58 412 123 4567 y cédula V-12345678"
    redacted = redact_pii(text)

    assert "[TOKEN_PHONE:pbkdf2:" in redacted
    assert "[TOKEN_IDENTITY_DOCUMENT:pbkdf2:" in redacted
    assert "+58" not in redacted
    assert "12345678" not in redacted
    assert detect_pii(redacted) == []


def test_redacts_overlapping_numeric_matches_cleanly():
    text = "CI 12345678 y telefono 0412-1234567"
    redacted = redact_pii(text)

    assert "12345678" not in redacted
    assert "0412" not in redacted
    assert "[TOKEN_IDENTITY_DOCUMENT:pbkdf2:" in redacted
    assert "[TOKEN_PHONE:pbkdf2:" in redacted
    assert detect_pii(redacted) == []


def test_normalization_and_token_stability():
    # Cédulas en diferentes formatos deben normalizar e hashing al mismo token
    token1 = pii_token("V-12.345.678", "identity_document")
    token2 = pii_token("v 12345678", "identity_document")
    token3 = pii_token("CI: 12345678", "identity_document")
    
    assert token1 == token2 == token3
    assert len(token1) == 64  # Hash SHA256 hex string tiene 64 caracteres
    
    # Teléfonos en diferentes formatos
    phone_token1 = pii_token("+58 412 123 4567", "phone")
    phone_token2 = pii_token("0412-1234567", "phone")
    phone_token3 = pii_token("00584121234567", "phone")
    
    assert phone_token1 == phone_token2 == phone_token3


def test_does_not_redact_technical_timestamps_or_geojson_ids():
    text = (
        "metadata.generated: 1792342345678 "
        "features[0].properties.time: 1792342300000 "
        "features[0].properties.code: 6000t8k6 "
        "features[0].geometry.coordinates[0]: -67.5993"
    )

    assert detect_pii(text) == []
    assert redact_pii(text) == text
