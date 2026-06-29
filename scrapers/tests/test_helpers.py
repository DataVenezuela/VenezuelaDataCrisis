import pytest

from shared.helpers import digits_only, mask_last4


class TestDigitsOnly:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("V-12.345.678", "12345678"),
            ("v 12 345 678", "12345678"),
            ("abc123def456", "123456"),
            ("0412-1234567", "04121234567"),
            ("(0212) 555-1234", "02125551234"),
            ("\t12\n34\r", "1234"),
            ("0001", "0001"),
        ],
    )
    def test_strips_everything_but_digits(self, raw, expected):
        assert digits_only(raw) == expected
    
    
    @pytest.mark.parametrize("raw", ["sin numeros", "", "----", "V-..."])
    def test_empty_when_no_digits(self, raw):
        assert digits_only(raw) == ""

    def test_is_idempotent(self):
        once = digits_only("V-12.345.678")
        assert digits_only(once) == once


class TestMaskLast4:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("12345678", "****5678"),
            ("V-12.345.678", "****5678"),
            ("0412-1234567", "****4567"),
            
        ],
    )
    
    def test_masks_keeping_last_four_digits(self, raw, expected):
        assert mask_last4(raw) == expected

    def test_always_prefixed_with_four_asterisks(self):
        masked = mask_last4("V-12.345.678")
        assert masked.startswith("****")
        # Nunca expone más de 4 dígitos en claro.
        assert len(masked) - len("****") <= 4

    def test_invariant_matches_digits_only_tail(self):
        raw = "V-12.345.678"
        assert mask_last4(raw) == "****" + digits_only(raw)[-4:]

    @pytest.mark.parametrize("raw", ["sin numeros", "", "----", "V-..."])
    def test_raises_when_no_digits(self, raw):
        with pytest.raises(ValueError, match="No hay dígitos"):
            mask_last4(raw) 

    @pytest.mark.parametrize("raw", ["1234", "V-1234", "0000"])
    def test_raises_when_less_than_5_characters(self, raw):
        with pytest.raises(ValueError, match="Debe tener al menos 5 caracteres para generar máscara PII"):
            mask_last4(raw)


class TestSharedWithPiiPipeline:
    """El refactor (#71) no debe alterar el masking real del tokenizador PII."""

    def test_mask_cedula_reuses_shared_helper(self):
        from scrapers.sanitizers.pii_tokenizer import mask_cedula

        _, masked = mask_cedula("V-12.345.678", salt="test-salt")
        assert masked == mask_last4("V-12.345.678") == "****5678"

    def test_mask_telefono_reuses_shared_helper(self):
        from scrapers.sanitizers.pii_tokenizer import mask_telefono

        _, masked = mask_telefono("0412-1234567", salt="test-salt")
        assert masked == mask_last4("0412-1234567") == "****4567"
