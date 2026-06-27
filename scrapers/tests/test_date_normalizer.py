from __future__ import annotations

import pytest

from scrapers.normalizers.date import normalize_date
from scrapers.normalizers import normalize_date as exported_normalize_date


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("15/05/2024", "2024-05-15"),
        ("15-05-2024", "2024-05-15"),
        ("2024-05-15", "2024-05-15"),
        ("  15/05/2024  ", "2024-05-15"),
    ],
)
def test_normalize_complete_dates(raw: str, expected: str) -> None:
    assert normalize_date(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("mayo 2024", {"start": "2024-05-01", "end": "2024-05-31"}),
        ("Mayo de 2024", {"start": "2024-05-01", "end": "2024-05-31"}),
        ("05/2024", {"start": "2024-05-01", "end": "2024-05-31"}),
        ("febrero 2024", {"start": "2024-02-01", "end": "2024-02-29"}),
        ("02/2023", {"start": "2023-02-01", "end": "2023-02-28"}),
    ],
)
def test_normalize_partial_month_year_dates(raw: str, expected: dict[str, str]) -> None:
    assert normalize_date(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("15/05/2024 14:30", "2024-05-15T14:30:00"),
        ("2024-05-15T14:30:05Z", "2024-05-15T14:30:05Z"),
        ("15 de mayo de 2024 14:30", "2024-05-15T14:30:00"),
    ],
)
def test_normalize_dates_with_time(raw: str, expected: str) -> None:
    assert normalize_date(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        None,
        "no es fecha",
        "31/02/2024",
        "13/2024",
        "mayo 20",
        "15/05",
        "2024-99-15",
    ],
)
def test_invalid_dates_return_none_without_raising(raw: str | None) -> None:
    assert normalize_date(raw) is None


def test_normalize_text_month_date_with_accents() -> None:
    assert normalize_date("15 de M\u00c1YO de 2024") == "2024-05-15"


def test_normalize_date_is_exported_from_package() -> None:
    assert exported_normalize_date("15/05/2024") == "2024-05-15"
