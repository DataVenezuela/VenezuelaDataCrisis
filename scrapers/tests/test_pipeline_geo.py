from __future__ import annotations

import json
from pathlib import Path

from scrapers.pipelines.run_pipeline import run_pipeline


def test_pipeline_enriches_geo(tmp_path: Path) -> None:
    """El pipeline debe escribir la zona canónica en el claim (offline, sin DB)."""
    run_pipeline(
        config_path=Path("scrapers/config/sources.demo.multi.yaml"),
        output_dir=tmp_path,
        persist_db=False,
    )
    claims_path = tmp_path / "sanitized" / "claims.jsonl"
    claims = [json.loads(line) for line in claims_path.read_text(encoding="utf-8").splitlines() if line]

    geo_codes = {claim.get("geo_code") for claim in claims}
    assert "VE-DC-LIBERTADOR-CARACAS" in geo_codes

    # El claim con geo debe traer lat/lon de enriquecimiento.
    caracas = next(c for c in claims if c.get("geo_code") == "VE-DC-LIBERTADOR-CARACAS")
    assert caracas["lat"] is not None and caracas["lon"] is not None
