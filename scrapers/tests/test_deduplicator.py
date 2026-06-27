from scrapers.dedup.deduplicator import deduplicate_fuzzy


def test_deduplicate_fuzzy_missing_persons():
    claims = [
        {
            "claim_id": "claim_1",
            "event_id": "test_event",
            "claim_type": "casualties.missing",
            "description": "Se busca a Claudio Hernandez",
            "location_text": "Venezuela",
            "confidence_score": 0.8,
            "source_name": "Twitter",
            "source_url": "http://twitter.com/1",
            "fetched_at": "2026-06-27T10:00:00",
        },
        {
            "claim_id": "claim_2",
            "event_id": "test_event",
            "claim_type": "casualties.missing",
            "description": "Buscamos a Klaudio Hernandes",
            "location_text": "Venezuela",
            "confidence_score": 0.9,  # Más alta confianza
            "source_name": "WhatsApp Group",
            "source_url": "http://whatsapp.com/2",
            "fetched_at": "2026-06-27T10:05:00",
        },
    ]

    deduped, count = deduplicate_fuzzy(claims)

    assert count == 1
    assert len(deduped) == 1
    
    # La de más alta confianza (claim_2) debe ser la canónica
    canonical = deduped[0]
    assert canonical["claim_id"] == "claim_2"
    assert canonical["description"] == "Buscamos a Klaudio Hernandes"
    
    # El origen del otro claim debe estar en la metadata para trazabilidad
    merged = canonical["metadata"]["merged_claims"]
    assert len(merged) == 1
    assert merged[0]["claim_id"] == "claim_1"
    assert merged[0]["source_name"] == "Twitter"


def test_deduplicate_fuzzy_needs():
    claims = [
        {
            "claim_id": "claim_1",
            "event_id": "test_event",
            "claim_type": "need.water",
            "description": "Se requiere agua potable con urgencia",
            "location_text": "Sector Central",
            "confidence_score": 0.8,
        },
        {
            "claim_id": "claim_2",
            "event_id": "test_event",
            "claim_type": "need.water",
            "description": "Se necesita agua potable urgente",
            "location_text": "Sector Central",
            "confidence_score": 0.8,
        },
        {
            "claim_id": "claim_3",
            "event_id": "test_event",
            "claim_type": "need.water",
            "description": "Se necesita agua potable urgente",
            "location_text": "Zona Norte",  # Ubicación distinta
            "confidence_score": 0.8,
        },
    ]

    deduped, count = deduplicate_fuzzy(claims)

    # Solo las dos del Sector Central deben colapsar
    assert count == 1
    assert len(deduped) == 2


def test_deduplicate_no_gender_false_positives():
    claims = [
        {
            "claim_id": "claim_1",
            "event_id": "test_event",
            "claim_type": "casualties.missing",
            "description": "Juan Perez",
            "location_text": "Venezuela",
            "confidence_score": 0.8,
        },
        {
            "claim_id": "claim_2",
            "event_id": "test_event",
            "claim_type": "casualties.missing",
            "description": "Juana Perez",
            "location_text": "Venezuela",
            "confidence_score": 0.8,
        },
    ]

    deduped, count = deduplicate_fuzzy(claims)

    # No deben colapsar por la penalización de género
    assert count == 0
    assert len(deduped) == 2
