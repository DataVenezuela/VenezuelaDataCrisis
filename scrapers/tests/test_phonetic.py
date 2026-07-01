"""Tests for phonetic hash and deterministic ID computation."""

from __future__ import annotations

from scrapers.normalizers.phonetic import (
    _spanish_phonetic_key,
    build_deterministic_id,
    phonetic_hash,
)


class TestSpanishPhoneticKey:
    def test_basic_name(self) -> None:
        assert _spanish_phonetic_key("Jose") == "jose"

    def test_ll_to_y(self) -> None:
        assert "y" in _spanish_phonetic_key("William")

    def test_ch_to_x(self) -> None:
        assert "x" in _spanish_phonetic_key("Chavez")

    def test_rr_to_r(self) -> None:
        assert "r" in _spanish_phonetic_key("Rrrr")

    def test_h_removed(self) -> None:
        assert "h" not in _spanish_phonetic_key("Hernandez")

    def test_b_to_v(self) -> None:
        # After conversion, both b and v become v
        key = _spanish_phonetic_key("Bolivar")
        assert "b" not in key

    def test_nina(self) -> None:
        # ñ → ni BEFORE accent stripping
        key = _spanish_phonetic_key("Niña")
        assert "ni" in key

    def test_consecutive_duplicates_removed(self) -> None:
        key = _spanish_phonetic_key("Jose")
        # No consecutive duplicates
        for i in range(len(key) - 1):
            assert key[i] != key[i + 1]


class TestPhoneticHash:
    def test_deterministic(self) -> None:
        h1 = phonetic_hash("Jose Luis Perez")
        h2 = phonetic_hash("Jose Luis Perez")
        assert h1 == h2

    def test_same_sounding_names(self) -> None:
        # "Jose" and "José" should produce the same hash
        h1 = phonetic_hash("Jose")
        h2 = phonetic_hash("José")
        assert h1 == h2

    def test_different_names(self) -> None:
        h1 = phonetic_hash("Maria")
        h2 = phonetic_hash("Carlos")
        assert h1 != h2

    def test_empty_string(self) -> None:
        # Should not crash
        h = phonetic_hash("")
        assert isinstance(h, str)


class TestBuildDeterministicId:
    def test_same_inputs_same_output(self) -> None:
        id1 = build_deterministic_id("JRPS", "Maracaibo, Zulia")
        id2 = build_deterministic_id("JRPS", "Maracaibo, Zulia")
        assert id1 == id2

    def test_different_phonetic_different_output(self) -> None:
        id1 = build_deterministic_id("JRPS", "Maracaibo, Zulia")
        id2 = build_deterministic_id("MRIA", "Maracaibo, Zulia")
        assert id1 != id2

    def test_different_location_different_output(self) -> None:
        id1 = build_deterministic_id("JRPS", "Maracaibo, Zulia")
        id2 = build_deterministic_id("JRPS", "Caracas, Miranda")
        assert id1 != id2

    def test_none_phonetic_returns_none(self) -> None:
        assert build_deterministic_id(None, "Maracaibo, Zulia") is None

    def test_none_location_returns_none(self) -> None:
        assert build_deterministic_id("JRPS", None) is None

    def test_empty_strings_return_none(self) -> None:
        assert build_deterministic_id("", "Maracaibo, Zulia") is None
        assert build_deterministic_id("JRPS", "") is None

    def test_output_is_16_hex_chars(self) -> None:
        result = build_deterministic_id("JRPS", "Maracaibo, Zulia")
        assert result is not None
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)


class TestCollisionPairs:
    """Verify that same name + same location → same ID, and different → different."""

    PAIRS_SAME_ID = [
        ("Jose Luis Perez", "Maracaibo, Zulia", "JOSE LUIS PEREZ", "Maracaibo, Zulia"),
        ("Maria Garcia", "Caracas, Miranda", "MARIA GARCIA", "Caracas, Miranda"),
        ("Carlos Rodriguez", "Barquisimeto, Lara", "CARLOS RODRIGUEZ", "Barquisimeto, Lara"),
        ("Ana Martinez", "Valencia, Carabobo", "ANA MARTINEZ", "Valencia, Carabobo"),
        ("Pedro Lopez", "Barcelona, Anzoategui", "PEDRO LOPEZ", "Barcelona, Anzoategui"),
    ]

    PAIRS_DIFF_ID = [
        ("Jose Luis Perez", "Maracaibo, Zulia", "Maria Garcia", "Maracaibo, Zulia"),
        ("Jose Luis Perez", "Maracaibo, Zulia", "Jose Luis Perez", "Caracas, Miranda"),
        ("Carlos Rodriguez", "Barquisimeto, Lara", "Carlos Rodriguez", "Valencia, Carabobo"),
        ("Ana Martinez", "Valencia, Carabobo", "Pedro Lopez", "Barcelona, Anzoategui"),
        ("Maria Garcia", "Caracas, Miranda", "Maria Gonzales", "Caracas, Miranda"),
    ]

    def test_same_name_location_same_id(self) -> None:
        for name1, loc1, name2, loc2 in self.PAIRS_SAME_ID:
            from scrapers.normalizers.person import normalize_person_name
            n1 = normalize_person_name(name1)
            n2 = normalize_person_name(name2)
            ph1 = phonetic_hash(n1)
            ph2 = phonetic_hash(n2)
            id1 = build_deterministic_id(ph1, loc1)
            id2 = build_deterministic_id(ph2, loc2)
            assert id1 == id2, f"Expected same ID for ({name1}, {loc1}) and ({name2}, {loc2})"

    def test_different_name_or_location_different_id(self) -> None:
        for name1, loc1, name2, loc2 in self.PAIRS_DIFF_ID:
            from scrapers.normalizers.person import normalize_person_name
            n1 = normalize_person_name(name1)
            n2 = normalize_person_name(name2)
            ph1 = phonetic_hash(n1)
            ph2 = phonetic_hash(n2)
            id1 = build_deterministic_id(ph1, loc1)
            id2 = build_deterministic_id(ph2, loc2)
            if id1 is not None and id2 is not None:
                assert id1 != id2, f"Expected different ID for ({name1}, {loc1}) and ({name2}, {loc2})"
