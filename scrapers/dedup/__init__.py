from scrapers.dedup.blocking import build_blocks
from scrapers.dedup.clustering import find_candidates
from scrapers.dedup.deduplicator import (
    deduplicate_by_fingerprint,
    deduplicate_persons,
    deduplicate_typed_entities,
)
from scrapers.dedup.fingerprint import (
    build_acopio_fingerprint,
    build_entity_fingerprint,
    build_event_fingerprint,
    build_fingerprint,
)
from scrapers.dedup.similarity import jaro_winkler, similarity_score

__all__ = [
    "build_fingerprint",
    "build_event_fingerprint",
    "build_acopio_fingerprint",
    "build_entity_fingerprint",
    "build_blocks",
    "deduplicate_by_fingerprint",
    "deduplicate_persons",
    "deduplicate_typed_entities",
    "find_candidates",
    "jaro_winkler",
    "similarity_score",
]
