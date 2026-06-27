from __future__ import annotations

import re
from scrapers.dedup.phonetics import phonetic_similarity, jaro_winkler_similarity


def deduplicate_by_fingerprint(items: list[dict]) -> tuple[list[dict], int]:
    """Deduplicación exacta basada en el fingerprint SHA-256."""
    seen: set[str] = set()
    output: list[dict] = []
    duplicates = 0

    for item in items:
        fp = item.get("fingerprint")
        if fp in seen:
            duplicates += 1
            continue
        seen.add(fp)
        output.append(item)

    return output, duplicates


def clean_missing_description(text: str) -> str:
    """Extrae nombres limpios eliminando frases comunes de busqueda en español."""
    text = (text or "").lower().strip()
    patterns = [
        r"\bse\s+busca\s+a\b",
        r"\bbuscando\s+a\b",
        r"\bbuscamos\s+a\b",
        r"\bse\s+encuentra\s+desaparecido[as]?\b",
        r"\besta\s+desaparecido[as]?\b",
        r"\bdesaparecido[as]?\b",
        r"\bno\s+se\s+sabe\s+de\b",
        r"\bayuda\s+para\s+encontrar\s+a\b",
        r"\bayuda\s+para\s+localizar\s+a\b",
        r"\breportan\s+a\b",
        r"\breporta\s+a\b",
        r"\bnombre[s]?\b",
    ]
    for p in patterns:
        text = re.sub(p, "", text, flags=re.IGNORECASE)
    text = re.sub(r"[^a-z\s]", " ", text)
    return " ".join(text.split())


def are_claims_fuzzy_duplicates(c1: dict, c2: dict, threshold: float = 0.85) -> bool:
    """Determina si dos claims son duplicados difusos."""
    if c1.get("event_id") != c2.get("event_id"):
        return False
    if c1.get("claim_type") != c2.get("claim_type"):
        return False

    claim_type = c1.get("claim_type")

    # Búsqueda de desaparecidos por homofonía de nombres.
    if claim_type == "casualties.missing":
        name1 = clean_missing_description(c1.get("description", ""))
        name2 = clean_missing_description(c2.get("description", ""))
        if not name1 or not name2:
            return False
        # Un umbral de 0.92 evita colapsos erróneos de género (ej: Juan/Juana).
        return phonetic_similarity(name1, name2) >= 0.92

    # Necesidades generales: proximidad de ubicaciones y descripciones.
    loc1 = c1.get("location_text") or ""
    loc2 = c2.get("location_text") or ""

    if loc1 and loc2:
        loc_sim = jaro_winkler_similarity(loc1, loc2)
        if loc_sim < 0.80:
            return False

    desc1 = c1.get("description", "")
    desc2 = c2.get("description", "")
    if not desc1 or not desc2:
        return False

    # Umbral relajado a 0.75 debido a la variabilidad léxica en reportes de necesidades.
    return jaro_winkler_similarity(desc1, desc2) >= 0.75


def deduplicate_fuzzy(items: list[dict], threshold: float = 0.85) -> tuple[list[dict], int]:
    """Deduplica de forma difusa (fuzzy) usando fonética y Jaro-Winkler, preservando trazabilidad."""
    canonical_list: list[dict] = []
    duplicates_count = 0

    for item in items:
        item_copy = dict(item)
        if "metadata" not in item_copy:
            item_copy["metadata"] = {}

        found_duplicate = False
        for idx, canonical in enumerate(canonical_list):
            if are_claims_fuzzy_duplicates(item_copy, canonical, threshold):
                duplicates_count += 1
                found_duplicate = True

                conf_item = item_copy.get("confidence_score", 0.0)
                conf_canonical = canonical.get("confidence_score", 0.0)

                if conf_item > conf_canonical or (
                    conf_item == conf_canonical
                    and len(item_copy.get("description", "")) > len(canonical.get("description", ""))
                ):
                    old_canonical = canonical
                    canonical_list[idx] = item_copy
                    canonical = item_copy
                    item_to_merge = old_canonical
                else:
                    item_to_merge = item_copy

                if "merged_claims" not in canonical["metadata"]:
                    canonical["metadata"]["merged_claims"] = []

                canonical["metadata"]["merged_claims"].append(
                    {
                        "claim_id": item_to_merge.get("claim_id"),
                        "source_id": item_to_merge.get("source_id"),
                        "source_name": item_to_merge.get("source_name"),
                        "source_url": item_to_merge.get("source_url"),
                        "description": item_to_merge.get("description"),
                        "fetched_at": item_to_merge.get("fetched_at"),
                    }
                )
                break

        if not found_duplicate:
            canonical_list.append(item_copy)

    return canonical_list, duplicates_count
