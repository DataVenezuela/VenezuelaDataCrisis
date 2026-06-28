from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


USER_AGENT = "VZLA_DEDUP_Scraper/0.3 (+public-interest emergency-data-cleanup)"
_DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
}


def _build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        connect=3,
        read=3,
        status=5,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD", "OPTIONS"}),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


_SESSION = _build_session()


def fetch_url(url: str, timeout: int = 25) -> tuple[str, str]:
    response = _SESSION.get(url, headers=_DEFAULT_HEADERS, timeout=timeout)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    return response.text, content_type
