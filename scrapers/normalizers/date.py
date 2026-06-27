from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date, datetime
from typing import TypeAlias

DateRange: TypeAlias = dict[str, str]
NormalizedDate: TypeAlias = str | DateRange | None

_DMY_RE = re.compile(
    r"^(?P<day>\d{1,2})[/-](?P<month>\d{1,2})[/-](?P<year>\d{4})"
    r"(?:\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?)?$"
)
_YMD_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})"
    r"(?:[T\s](?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?(?P<z>Z)?)?$",
    re.IGNORECASE,
)
_MONTH_YEAR_NUMERIC_RE = re.compile(r"^(?P<month>\d{1,2})[/-](?P<year>\d{4})$")
_MONTH_YEAR_TEXT_RE = re.compile(r"^(?P<month>[a-z]+)\s+(?:de\s+)?(?P<year>\d{4})$")
_DAY_MONTH_YEAR_TEXT_RE = re.compile(
    r"^(?P<day>\d{1,2})\s+(?:de\s+)?(?P<month>[a-z]+)\s+(?:de\s+)?(?P<year>\d{4})"
    r"(?:\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?)?$"
)

_MONTHS = {
    "enero": 1,
    "ene": 1,
    "febrero": 2,
    "feb": 2,
    "marzo": 3,
    "mar": 3,
    "abril": 4,
    "abr": 4,
    "mayo": 5,
    "may": 5,
    "junio": 6,
    "jun": 6,
    "julio": 7,
    "jul": 7,
    "agosto": 8,
    "ago": 8,
    "septiembre": 9,
    "setiembre": 9,
    "sep": 9,
    "set": 9,
    "octubre": 10,
    "oct": 10,
    "noviembre": 11,
    "nov": 11,
    "diciembre": 12,
    "dic": 12,
}


def normalize_date(raw: str | None) -> NormalizedDate:
    """Normalize common Venezuelan date strings to ISO 8601.

    Full dates return ``YYYY-MM-DD``. Datetimes return
    ``YYYY-MM-DDTHH:MM:SS`` and preserve a trailing ``Z`` when the source has
    one. Partial month/year dates return a range with the first and last day
    of that month, for example ``{"start": "2024-05-01", "end": "2024-05-31"}``.
    Invalid or unsupported inputs return ``None`` without raising.
    """
    text = _clean(raw)
    if not text:
        return None

    return (
        _parse_ymd(text)
        or _parse_dmy(text)
        or _parse_day_month_year_text(text)
        or _parse_numeric_month_year(text)
        or _parse_text_month_year(text)
    )


def _clean(raw: str | None) -> str:
    if raw is None:
        return ""
    text = unicodedata.normalize("NFKC", str(raw)).strip().lower()
    text = "".join(
        char for char in unicodedata.normalize("NFD", text)
        if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"\s+", " ", text)


def _parse_ymd(text: str) -> str | None:
    match = _YMD_RE.match(text)
    if not match:
        return None
    return _format_date_match(match)


def _parse_dmy(text: str) -> str | None:
    match = _DMY_RE.match(text)
    if not match:
        return None
    return _format_date_match(match)


def _parse_day_month_year_text(text: str) -> str | None:
    match = _DAY_MONTH_YEAR_TEXT_RE.match(text)
    if not match:
        return None
    month = _MONTHS.get(match.group("month"))
    if month is None:
        return None
    return _format_parts(
        year=_to_int(match.group("year")),
        month=month,
        day=_to_int(match.group("day")),
        hour=_optional_int(match.group("hour")),
        minute=_optional_int(match.group("minute")),
        second=_optional_int(match.group("second")),
    )


def _parse_numeric_month_year(text: str) -> DateRange | None:
    match = _MONTH_YEAR_NUMERIC_RE.match(text)
    if not match:
        return None
    return _month_range(_to_int(match.group("year")), _to_int(match.group("month")))


def _parse_text_month_year(text: str) -> DateRange | None:
    match = _MONTH_YEAR_TEXT_RE.match(text)
    if not match:
        return None
    month = _MONTHS.get(match.group("month"))
    if month is None:
        return None
    return _month_range(_to_int(match.group("year")), month)


def _format_date_match(match: re.Match[str]) -> str | None:
    return _format_parts(
        year=_to_int(match.group("year")),
        month=_to_int(match.group("month")),
        day=_to_int(match.group("day")),
        hour=_optional_int(match.group("hour")),
        minute=_optional_int(match.group("minute")),
        second=_optional_int(match.group("second")),
        suffix="Z" if match.groupdict().get("z") else "",
    )


def _format_parts(
    *,
    year: int,
    month: int,
    day: int,
    hour: int | None = None,
    minute: int | None = None,
    second: int | None = None,
    suffix: str = "",
) -> str | None:
    try:
        if hour is None and minute is None and second is None:
            return date(year, month, day).isoformat()
        if hour is None or minute is None:
            return None
        value = datetime(year, month, day, hour, minute, second or 0).isoformat(timespec="seconds")
    except ValueError:
        return None
    return f"{value}{suffix}"


def _month_range(year: int, month: int) -> DateRange | None:
    try:
        last_day = calendar.monthrange(year, month)[1]
        start = date(year, month, 1).isoformat()
        end = date(year, month, last_day).isoformat()
    except ValueError:
        return None
    return {"start": start, "end": end}


def _to_int(value: str) -> int:
    return int(value)


def _optional_int(value: str | None) -> int | None:
    return int(value) if value is not None else None
