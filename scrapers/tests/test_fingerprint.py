from scrapers.dedup.fingerprint import build_fingerprint


def test_fingerprint_is_stable():
    a = build_fingerprint("event", "need.water", "Venezuela", "Se necesita agua")
    b = build_fingerprint("event", "need.water", "Venezuela", "Se necesita agua")

    assert a == b
