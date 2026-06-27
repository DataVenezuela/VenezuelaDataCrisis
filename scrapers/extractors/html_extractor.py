from __future__ import annotations

from bs4 import BeautifulSoup


def extract_html_text(html: str) -> tuple[str | None, str]:
    soup = BeautifulSoup(html or "", "html.parser")

    title = None
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside"]):
        tag.decompose()

    text = " ".join(soup.get_text(" ").split())
    return title, text
