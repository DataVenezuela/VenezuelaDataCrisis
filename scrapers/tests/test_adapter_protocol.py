from scrapers.adapters import AdapterProtocol, RawContent
from scrapers.models.source import SourceConfig


class DummyAdapter:
    def fetch(self, source_config: SourceConfig) -> RawContent:
        return {"source_id": source_config.id}


def test_adapter_protocol_accepts_matching_fetch_signature():
    source = SourceConfig(
        id="synthetic_source",
        name="Synthetic Source",
        type="api_json",
        enabled=True,
        trust_tier="C",
        url="https://example.invalid/data.json",
        refresh_minutes=60,
    )
    adapter: AdapterProtocol = DummyAdapter()

    assert adapter.fetch(source) == {"source_id": "synthetic_source"}
