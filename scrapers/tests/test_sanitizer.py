from scrapers.sanitizers.pii_detector import detect_pii
from scrapers.sanitizers.pii_redactor import redact_pii


def test_redacts_phone_and_document():
    text = "Contacto +58 412 123 4567 y cédula V-12345678"
    redacted = redact_pii(text)

    assert "[REDACTED_" in redacted
    assert "+58" not in redacted
    assert "12345678" not in redacted
    assert detect_pii(redacted) == []


def test_redacts_overlapping_numeric_matches_cleanly():
    text = "CI 12345678 y telefono 0412-1234567"
    redacted = redact_pii(text)

    assert "12345678" not in redacted
    assert "0412" not in redacted
    assert "[REDACTED_IDENTITY_DOCUMENT]" in redacted
    assert "[REDACTED_PHONE]" in redacted
    assert detect_pii(redacted) == []


def test_does_not_redact_technical_timestamps_or_geojson_ids():
    text = (
        "metadata.generated: 1792342345678 "
        "features[0].properties.time: 1792342300000 "
        "features[0].properties.code: 6000t8k6 "
        "features[0].geometry.coordinates[0]: -67.5993"
    )

    assert detect_pii(text) == []
    assert redact_pii(text) == text
