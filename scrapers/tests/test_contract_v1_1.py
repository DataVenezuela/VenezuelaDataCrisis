"""Fixtures de conformidad para CONTRACT_VERSION 1.1 (issue #242).

Carga los JSONs en fixtures/contract-v1.1/valid/ y fixtures/contract-v1.1/invalid/
y verifica que Person acepta / rechaza cada uno según lo esperado.
Los fixtures documentan el contrato en formato legible y ejecutable.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scrapers.models import Person

_FIXTURES = Path(__file__).parent / "fixtures" / "contract-v1.1"
_VALID = sorted((_FIXTURES / "valid").glob("*.json"))
_INVALID = sorted((_FIXTURES / "invalid").glob("*.json"))

_RESERVED_KEYS = {"_comment", "_expected_error"}


def _load(path: Path) -> dict:
    data = json.loads(path.read_text())
    return {k: v for k, v in data.items() if k not in _RESERVED_KEYS}


@pytest.mark.parametrize("fixture_path", _VALID, ids=[p.stem for p in _VALID])
def test_valid_fixture_accepted(fixture_path: Path):
    data = _load(fixture_path)
    person = Person.model_validate(data)
    assert person.full_name


@pytest.mark.parametrize("fixture_path", _INVALID, ids=[p.stem for p in _INVALID])
def test_invalid_fixture_rejected(fixture_path: Path):
    data = _load(fixture_path)
    with pytest.raises(ValidationError):
        Person.model_validate(data)
