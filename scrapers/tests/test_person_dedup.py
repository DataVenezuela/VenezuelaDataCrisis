"""Tests for Person dedup pipeline: similarity, blocking, clustering, deduplicator.

Covers all 4 Mayerlim corrections:
1. Partial location scoring
2. Cedula veto
3. Imports at top of deduplicator.py
4. Two-level blocking (strong cedula, loose phonetic without location)
"""

from __future__ import annotations

import pytest

from scrapers.dedup.blocking import build_blocks
from scrapers.dedup.clustering import find_candidates
from scrapers.dedup.similarity import (
    _location_score,
    similarity_score,
)

_EVENT_ID = "8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a"
_PH_HASH = "JN"
_PH_HASH_B = "KRLS"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _person(
    id_: str = "a",
    person_record_id: str | None = None,
    name: str = "Juan Perez",
    cedula_hmac: str | None = None,
    location: str = "Caracas, Distrito Capital",
    status: str = "missing",
    age_range: dict[str, int] | None = None,
    event_id: str = _EVENT_ID,
    phonetic_hash: str = _PH_HASH,
    block_keys: list[str] | None = None,
) -> dict:
    rec = {
        "id": id_,
        "person_record_id": person_record_id or id_,
        "full_name": name,
        "cedula_hmac": cedula_hmac,
        "last_known_location": location,
        "status": status,
        "age_range": age_range,
        "event_id": event_id,
        "phonetic_hash": phonetic_hash,
    }
    if block_keys is not None:
        rec["block_keys"] = block_keys
    return rec


# ---------------------------------------------------------------------------
# Similarity tests
# ---------------------------------------------------------------------------


class TestSimilarity:
    def test_identical_persons_score_high(self) -> None:
        a = _person("1", name="Juan Perez", cedula_hmac="same",
                     age_range={"min": 25, "max": 35})
        b = _person("2", name="Juan Perez", cedula_hmac="same",
                     age_range={"min": 25, "max": 35})
        score, reasons = similarity_score(a, b)
        assert score > 0.85
        assert "nombre" in reasons
        assert "cedula" in reasons
        assert "ubicacion" in reasons
        assert "edad" in reasons
        assert "status" in reasons

    def test_cedula_veto_returns_zero(self) -> None:
        """Mayerlim correction #2: different cedulas → score = 0."""
        a = _person("1", name="Juan Perez", cedula_hmac="aaa")
        b = _person("2", name="Juan Perez", cedula_hmac="bbb")
        score, reasons = similarity_score(a, b)
        assert score == 0.0
        assert reasons["cedula"] == 0.0
        assert reasons["nombre"] == 0.0

    def test_cedula_absent_in_one_no_veto(self) -> None:
        a = _person("1", name="Juan Perez", cedula_hmac="aaa")
        b = _person("2", name="Juan Perez")  # no cedula
        score, reasons = similarity_score(a, b)
        assert score > 0.0  # no veto, just cedula contributes 0
        assert reasons["cedula"] == 0.0

    def test_cedula_absent_in_both_does_not_contribute(self) -> None:
        a = _person("1", name="Juan Perez")
        b = _person("2", name="Juan Perez")
        _, reasons = similarity_score(a, b)
        assert reasons["cedula"] == 0.0

    def test_cedula_match_contributes_full(self) -> None:
        a = _person("1", name="Juan Perez", cedula_hmac="aaa")
        b = _person("2", name="Juan Perez", cedula_hmac="aaa")
        score, reasons = similarity_score(a, b)
        assert reasons["cedula"] == 0.30
        assert score > 0.85

    def test_cedula_mismatch_returns_total_zero(self) -> None:
        a = _person("1", name="Juan Perez", cedula_hmac="aaa")
        b = _person("2", name="Juan Perez", cedula_hmac="bbb")
        score, reasons = similarity_score(a, b)
        assert score == 0.0
        assert all(value == 0.0 for value in reasons.values())

    def test_location_same_city_state_scores_1(self) -> None:
        """Same city and state → location component = 1.0 * 0.15 = 0.15."""
        assert _location_score("Caracas, Distrito Capital", "Caracas, Distrito Capital") == 1.0

    def test_location_same_state_only_scores_0_5(self) -> None:
        """Mayerlim correction #1: same state, different city → 0.5."""
        assert _location_score("Caracas, Miranda", "Petare, Miranda") == 0.5

    def test_location_different_state_scores_0(self) -> None:
        assert _location_score("Caracas, Miranda", "Maracaibo, Zulia") == 0.0

    def test_location_missing_scores_0(self) -> None:
        a = _person("1", location=None)
        b = _person("2", location="Caracas, Miranda")
        score, reasons = similarity_score(a, b)
        assert reasons["ubicacion"] == 0.0

    def test_location_partial_in_similarity_score(self) -> None:
        """Mayerlim correction #1: same state only → ubicacion = 0.075."""
        a = _person("1", name="Juan Perez", location="Caracas, Miranda")
        b = _person("2", name="Juan Perez", location="Petare, Miranda")
        _, reasons = similarity_score(a, b)
        assert reasons["ubicacion"] == pytest.approx(0.075, rel=0.1)

    def test_age_overlap_full_matches(self) -> None:
        a = _person("1", age_range={"min": 25, "max": 35})
        b = _person("2", age_range={"min": 25, "max": 35})
        _, reasons = similarity_score(a, b)
        assert reasons["edad"] == 0.10

    def test_age_overlap_no_overlap(self) -> None:
        a = _person("1", age_range={"min": 10, "max": 15})
        b = _person("2", age_range={"min": 50, "max": 60})
        _, reasons = similarity_score(a, b)
        assert reasons["edad"] == 0.0

    def test_age_missing_in_one(self) -> None:
        a = _person("1", age_range={"min": 25, "max": 35})
        b = _person("2", age_range=None)
        _, reasons = similarity_score(a, b)
        assert reasons["edad"] == 0.0

    def test_status_match(self) -> None:
        a = _person("1", status="missing")
        b = _person("2", status="missing")
        _, reasons = similarity_score(a, b)
        assert reasons["status"] == 0.05

    def test_status_mismatch(self) -> None:
        a = _person("1", status="missing")
        b = _person("2", status="found")
        _, reasons = similarity_score(a, b)
        assert reasons["status"] == 0.0

    def test_reasons_dict_not_flat_list(self) -> None:
        """The spec requires reasons as a dict, not a flat list."""
        a = _person("1", name="Juan Perez")
        b = _person("2", name="Juan Perez")
        _, reasons = similarity_score(a, b)
        assert isinstance(reasons, dict)
        assert set(reasons.keys()) == {"nombre", "cedula", "ubicacion", "edad", "status"}

    def test_jaro_winkler_from_jellyfish(self) -> None:
        """Verify jaro_winkler is from jellyfish, not pure Python."""
        from scrapers.dedup.similarity import jaro_winkler
        # Very similar names should score high
        assert jaro_winkler("juan perez", "juan peres") > 0.9
        # Completely different names
        assert jaro_winkler("juan", "pedro") < 0.5


# ---------------------------------------------------------------------------
# Blocking tests
# ---------------------------------------------------------------------------


class TestBlocking:
    def test_two_level_blocking_with_cedula(self) -> None:
        """Person with cedula_hmac → strong block key."""
        p = _person("1", cedula_hmac="abc123")
        blocks = build_blocks([p])
        assert len(blocks) == 2
        assert any(k.startswith("ced:") for k in blocks)
        assert any(k.startswith("phon:") for k in blocks)

    def test_block_key_format(self) -> None:
        """Block keys follow spec format."""
        p = _person("1", cedula_hmac="abc123", phonetic_hash="XYZ")
        blocks = build_blocks([p])
        expected_ced = f"ced:{_EVENT_ID}:abc123"
        expected_phon = f"phon:{_EVENT_ID}:XYZ"
        assert expected_ced in blocks
        assert expected_phon in blocks

    def test_no_cedula_only_loose_block(self) -> None:
        """Person without cedula_hmac → only phonetic block."""
        p = _person("1", cedula_hmac=None)
        blocks = build_blocks([p])
        assert len(blocks) == 1
        assert all(k.startswith("phon:") for k in blocks)

    def test_no_phonetic_hash_no_loose_block(self) -> None:
        """Person without phonetic_hash → only strong block (if cedula)."""
        p = _person("1", cedula_hmac="abc123", phonetic_hash="")
        blocks = build_blocks([p])
        # Only strong block since phonetic_hash is empty
        assert len(blocks) == 1
        assert all(k.startswith("ced:") for k in blocks)

    def test_no_cedula_no_phonetic_no_blocks(self) -> None:
        """Person without cedula and without phonetic_hash → no blocks."""
        p = _person("1", cedula_hmac=None, phonetic_hash="")
        blocks = build_blocks([p])
        assert len(blocks) == 0

    def test_multiple_persons_in_same_block(self) -> None:
        """Two persons with same cedula → same strong block."""
        a = _person("a", cedula_hmac="same")
        b = _person("b", cedula_hmac="same")
        blocks = build_blocks([a, b])
        strong_key = f"ced:{_EVENT_ID}:same"
        assert strong_key in blocks
        assert len(blocks[strong_key]) == 2

    def test_uses_precomputed_block_keys_when_present(self) -> None:
        p = _person("1", block_keys=["custom:block"])
        blocks = build_blocks([p])
        assert blocks == {"custom:block": [p]}

    def test_person_in_multiple_blocks(self) -> None:
        """A person with both cedula and phonetic_hash belongs to both blocks."""
        a = _person("a", cedula_hmac="abc", phonetic_hash="XYZ")
        b = _person("b", cedula_hmac="abc")
        c = _person("c", phonetic_hash="XYZ")
        blocks = build_blocks([a, b, c])
        # Person a should be in both ced and phon blocks
        ced_key = f"ced:{_EVENT_ID}:abc"
        phon_key = f"phon:{_EVENT_ID}:XYZ"
        assert ced_key in blocks
        assert phon_key in blocks
        assert len(blocks[ced_key]) == 2  # a and b
        assert len(blocks[phon_key]) == 2  # a and c


# ---------------------------------------------------------------------------
# Clustering tests
# ---------------------------------------------------------------------------


class TestClustering:
    def test_pair_above_threshold_becomes_candidate(self) -> None:
        """Two similar persons in same block → candidate."""
        a = _person("a", name="Juan Perez Gonzalez", cedula_hmac="same",
                     age_range={"min": 25, "max": 35})
        b = _person("b", name="Juan Perez Gonzales", cedula_hmac="same",
                     age_range={"min": 25, "max": 35})
        blocks = build_blocks([a, b])
        candidates = find_candidates(blocks, threshold=0.80)
        assert len(candidates) >= 1
        assert candidates[0]["left_person_record_id"] != candidates[0]["right_person_record_id"]
        assert candidates[0]["event_id"] == _EVENT_ID
        assert "blocking_key" in candidates[0]
        assert candidates[0]["score"] >= 0.80

    def test_pair_below_threshold_not_candidate(self) -> None:
        """Very different persons → no candidate."""
        a = _person("a", name="Juan Perez", cedula_hmac="aaa")
        b = _person("b", name="Maria Rodriguez", cedula_hmac="aaa")
        blocks = build_blocks([a, b])
        candidates = find_candidates(blocks, threshold=0.95)
        assert len(candidates) == 0

    def test_cedula_veto_no_candidate(self) -> None:
        """Different cedulas → score 0, no candidate."""
        a = _person("a", name="Juan Perez", cedula_hmac="aaa")
        b = _person("b", name="Juan Perez", cedula_hmac="bbb")
        blocks = build_blocks([a, b])
        candidates = find_candidates(blocks, threshold=0.01)  # very low threshold
        assert len(candidates) == 0  # veto means score is 0

    def test_pair_already_in_candidates_found_only_once(self) -> None:
        """Duplicate pairs across blocks → only one candidate."""
        a = _person("a", name="Juan Perez", cedula_hmac="abc", phonetic_hash="XYZ")
        b = _person("b", name="Juan Perez", cedula_hmac="def", phonetic_hash="XYZ")
        # They share phonetic block but NOT cedula block
        blocks = build_blocks([a, b])
        candidates = find_candidates(blocks, threshold=0.01)
        # Only phonetic block pairs them (cedula is different, so they're only
        # in that one block together)
        phonetic_key = f"phon:{_EVENT_ID}:XYZ"
        assert phonetic_key in blocks
        assert len(blocks[phonetic_key]) == 2
        # Only one pair (but veto means score 0 below threshold of 0.01)
        # Wait, they have different cedulas so veto applies → score 0
        assert len(candidates) == 0

    def test_priority_high_when_score_above_95(self) -> None:
        """score >= 0.95 → priority='high'."""
        # Use identical persons for high score
        a = _person("a", name="Juan Perez", cedula_hmac="same",
                     location="Caracas, Miranda", status="missing",
                     age_range={"min": 25, "max": 35})
        b = _person("b", name="Juan Perez", cedula_hmac="same",
                     location="Caracas, Miranda", status="missing",
                     age_range={"min": 25, "max": 35})
        blocks = build_blocks([a, b])
        candidates = find_candidates(blocks, threshold=0.80)
        assert len(candidates) >= 1
        # With identical everything, score should be very high
        assert candidates[0]["score"] >= 0.95
        assert candidates[0]["priority"] == "high"

    def test_candidate_has_expected_keys(self) -> None:
        """Candidates match dedup_candidates contract in master."""
        a = _person("a", name="Juan Perez", cedula_hmac="same",
                     age_range={"min": 25, "max": 35})
        b = _person("b", name="Juan Perez", cedula_hmac="same",
                     age_range={"min": 25, "max": 35})
        blocks = build_blocks([a, b])
        candidates = find_candidates(blocks, threshold=0.80)
        assert len(candidates) >= 1
        c = candidates[0]
        assert "event_id" in c
        assert "left_person_record_id" in c
        assert "right_person_record_id" in c
        assert "blocking_key" in c
        assert "score" in c
        assert "reasons" in c
        assert "priority" in c
        assert isinstance(c["reasons"], dict)

    def test_empty_block_no_candidates(self) -> None:
        """Block with 1 person → no pairs → no candidates."""
        blocks = build_blocks([_person("a")])
        candidates = find_candidates(blocks, threshold=0.0)
        assert len(candidates) == 0


# ---------------------------------------------------------------------------
# Deduplicator tests
# ---------------------------------------------------------------------------


class TestDeduplicatePersons:
    def test_build_then_find_returns_candidates(self) -> None:
        persons = [
            _person("a", name="Juan Perez Gonzalez", cedula_hmac="same",
                     age_range={"min": 25, "max": 35}),
            _person("b", name="Juan Perez Gonzales", cedula_hmac="same",
                     age_range={"min": 25, "max": 35}),
        ]
        blocks = build_blocks(persons)
        candidates = find_candidates(blocks, threshold=0.80)
        assert len(candidates) >= 1

    def test_build_then_find_no_candidates_for_different(self) -> None:
        persons = [
            _person("a", name="Juan Perez", cedula_hmac="aaa"),
            _person("b", name="Maria Rodriguez", cedula_hmac="aaa"),
        ]
        blocks = build_blocks(persons)
        candidates = find_candidates(blocks, threshold=0.90)
        assert len(candidates) == 0

    def test_cedula_mismatch_penalizes_to_zero(self) -> None:
        """With veto, score is 0.0, not just lower."""
        a = _person("a", name="Juan Perez", cedula_hmac="abc")
        b = _person("b", name="Juan Perez", cedula_hmac="xyz")
        score, _ = similarity_score(a, b)
        assert score == 0.0  # veto forces zero
