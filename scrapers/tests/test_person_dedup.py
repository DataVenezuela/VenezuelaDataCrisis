"""Tests for Person deduplication pipeline."""

from __future__ import annotations

from scrapers.dedup.blocking import build_blocks
from scrapers.dedup.clustering import find_candidates
from scrapers.dedup.deduplicator import deduplicate_persons
from scrapers.dedup.similarity import jaro_winkler, similarity_score


# --- Jaro-Winkler tests ---

class TestJaroWinkler:
    def test_identical_strings(self) -> None:
        assert jaro_winkler("jose", "jose") == 1.0

    def test_completely_different(self) -> None:
        assert jaro_winkler("abcd", "xyzw") == 0.0

    def test_similar_names(self) -> None:
        score = jaro_winkler("jose", "joses")
        assert score > 0.9

    def test_empty_string(self) -> None:
        assert jaro_winkler("", "jose") == 0.0
        assert jaro_winkler("jose", "") == 0.0
        assert jaro_winkler("", "") == 1.0

    def test_case_sensitive(self) -> None:
        # Jaro-Winkler is case-sensitive; we normalize before calling
        score = jaro_winkler("Jose", "jose")
        assert score < 1.0


# --- Similarity scoring tests ---

def _person(name: str, location: str = "Maracaibo, Zulia", cedula: str | None = None, 
            age: dict | None = None, status: str = "missing") -> dict:
    return {
        "full_name": name,
        "last_known_location": location,
        "cedula_hmac": cedula,
        "age_range": age,
        "status": status,
        "deterministic_id": None,
    }


class TestSimilarityScore:
    def test_same_person_high_score(self) -> None:
        left = _person("Jose Luis Perez", cedula="abc123hash")
        right = _person("Jose Luis Perez", cedula="abc123hash")
        score, reasons = similarity_score(left, right)
        assert score > 0.9
        assert any("name=" in r for r in reasons)

    def test_different_names_low_score(self) -> None:
        left = _person("Jose Luis Perez")
        right = _person("Maria Garcia")
        score, _ = similarity_score(left, right)
        assert score < 0.45

    def test_cedula_match_boosts_score(self) -> None:
        left = _person("Jose Perez", cedula="abc123hash")
        right = _person("Jose Perez", cedula="abc123hash")
        score, reasons = similarity_score(left, right)
        assert score >= 0.95
        assert "cedula=match" in reasons

    def test_cedula_mismatch_penalizes(self) -> None:
        left = _person("Jose Perez", cedula="hash1")
        right = _person("Jose Perez", cedula="hash2")
        score_with, _ = similarity_score(left, right)
        left_with_ced = _person("Jose Perez", cedula="same_hash")
        right_with_ced = _person("Jose Perez", cedula="same_hash")
        score_without, _ = similarity_score(left_with_ced, right_with_ced)
        assert score_with < score_without

    def test_location_match(self) -> None:
        left = _person("Jose Perez", location="Caracas, Miranda")
        right = _person("Jose Perez", location="Caracas, Miranda")
        score, reasons = similarity_score(left, right)
        assert "location=match" in reasons

    def test_age_compatible(self) -> None:
        left = _person("Jose Perez", age={"min": 25, "max": 35})
        right = _person("Jose Perez", age={"min": 30, "max": 40})
        score, reasons = similarity_score(left, right)
        assert any("age=" in r for r in reasons)

    def test_empty_fields_handled(self) -> None:
        left = {"full_name": "Jose", "status": "missing"}
        right = {"full_name": "Jose", "status": "missing"}
        score, _ = similarity_score(left, right)
        assert score >= 0.5


# --- Blocking tests ---

class TestBlocking:
    def test_same_deterministic_id_same_block(self) -> None:
        p1 = _person("Jose Perez")
        p1["deterministic_id"] = "abc123"
        p2 = _person("Jose Perez")
        p2["deterministic_id"] = "abc123"
        blocks = build_blocks([p1, p2])
        assert len(blocks) == 1
        assert len(list(blocks.values())[0]) == 2

    def test_different_deterministic_id_different_blocks(self) -> None:
        p1 = _person("Jose Perez")
        p1["deterministic_id"] = "abc123"
        p2 = _person("Maria Garcia")
        p2["deterministic_id"] = "def456"
        blocks = build_blocks([p1, p2])
        assert len(blocks) == 2

    def test_fallback_to_name_key(self) -> None:
        p1 = _person("Jose Perez", location="Caracas, Miranda")
        p2 = _person("Jose Perez", location="Caracas, Miranda")
        blocks = build_blocks([p1, p2])
        # Same name + same location → same block
        assert len(blocks) == 1

    def test_single_person_no_candidates(self) -> None:
        blocks = build_blocks([_person("Jose Perez")])
        candidates = find_candidates(blocks, threshold=0.75)
        assert len(candidates) == 0


# --- Clustering tests ---

class TestClustering:
    def test_pair_above_threshold(self) -> None:
        p1 = _person("Jose Luis Perez", location="Maracaibo, Zulia", cedula="ced_hash_1")
        p1["deterministic_id"] = "same123"
        p2 = _person("Jose Luis Perez", location="Maracaibo, Zulia", cedula="ced_hash_1")
        p2["deterministic_id"] = "same123"
        blocks = build_blocks([p1, p2])
        candidates = find_candidates(blocks, threshold=0.75)
        assert len(candidates) == 1
        assert candidates[0]["decision"] == "pending"

    def test_pair_below_threshold(self) -> None:
        p1 = _person("Jose Perez")
        p1["deterministic_id"] = "block1"
        p2 = _person("Maria Garcia")
        p2["deterministic_id"] = "block1"
        blocks = build_blocks([p1, p2])
        candidates = find_candidates(blocks, threshold=0.75)
        assert len(candidates) == 0

    def test_no_auto_merge(self) -> None:
        p1 = _person("Jose Perez", cedula="ced_hash_1")
        p1["deterministic_id"] = "block1"
        p2 = _person("Jose Perez", cedula="ced_hash_1")
        p2["deterministic_id"] = "block1"
        blocks = build_blocks([p1, p2])
        candidates = find_candidates(blocks, threshold=0.75)
        assert all(c["decision"] == "pending" for c in candidates)


# --- Full pipeline tests ---

class TestDeduplicatePersons:
    def test_all_persons_preserved(self) -> None:
        persons = [
            _person("Jose Perez", location="Maracaibo, Zulia", cedula="ced_123"),
            _person("Jose Perez", location="Maracaibo, Zulia", cedula="ced_123"),
            _person("Maria Garcia", location="Caracas, Miranda", cedula="ced_456"),
        ]
        persons[0]["deterministic_id"] = "abc123"
        persons[1]["deterministic_id"] = "abc123"
        persons[2]["deterministic_id"] = "def456"
        
        result, candidates, count = deduplicate_persons(persons)
        assert len(result) == 3  # All preserved
        assert count >= 1

    def test_no_persons(self) -> None:
        result, candidates, count = deduplicate_persons([])
        assert result == []
        assert count == 0

    def test_single_person_no_candidates(self) -> None:
        persons = [_person("Jose Perez")]
        persons[0]["deterministic_id"] = "abc123"
        result, candidates, count = deduplicate_persons(persons)
        assert len(result) == 1
        assert count == 0

    def test_5_true_positive_pairs(self) -> None:
        """5 pairs of same person → 5 candidates."""
        pairs = [
            ("Jose Luis Perez", "Maracaibo, Zulia"),
            ("Maria Garcia", "Caracas, Miranda"),
            ("Carlos Rodriguez", "Barquisimeto, Lara"),
            ("Ana Martinez", "Valencia, Carabobo"),
            ("Pedro Lopez", "Barcelona, Anzoategui"),
        ]
        persons = []
        for i, (name, loc) in enumerate(pairs):
            ced = f"ced_hash_{i}"
            p1 = _person(name, location=loc, cedula=ced)
            p1["deterministic_id"] = f"block_{i}"
            p2 = _person(name, location=loc, cedula=ced)
            p2["deterministic_id"] = f"block_{i}"
            persons.extend([p1, p2])
        
        result, candidates, count = deduplicate_persons(persons)
        assert count >= 5

    def test_5_false_positive_pairs(self) -> None:
        """5 pairs of different people → 0 candidates."""
        pairs = [
            ("Jose Perez", "Maria Garcia"),
            ("Carlos Lopez", "Ana Rodriguez"),
            ("Pedro Martinez", "Luis Hernandez"),
            ("Juan Gonzalez", "Pedro Sanchez"),
            ("Maria Fernandez", "Carlos Mendoza"),
        ]
        persons = []
        for i, (name1, name2) in enumerate(pairs):
            p1 = _person(name1, location="Caracas, Miranda")
            p1["deterministic_id"] = f"block_{i}"
            p2 = _person(name2, location="Caracas, Miranda")
            p2["deterministic_id"] = f"block_{i}"
            persons.extend([p1, p2])
        
        result, candidates, count = deduplicate_persons(persons)
        assert count == 0
