from __future__ import annotations

import requests


DEFAULT_HEADERS = {
    "User-Agent": "VZLA_DEDUP_Scraper/0.2 (+public-interest emergency-data-cleanup)"
}


def fetch_url(url: str, timeout: int = 25) -> tuple[str, str]:
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    return response.text, content_type
