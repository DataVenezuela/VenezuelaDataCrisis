from __future__ import annotations

import xml.etree.ElementTree as ET


def extract_rss_items(raw: str) -> list[tuple[str | None, str]]:
    root = ET.fromstring(raw)
    items: list[tuple[str | None, str]] = []

    for item in root.findall(".//item"):
        title = item.findtext("title")
        description = item.findtext("description") or ""
        link = item.findtext("link") or ""
        text = " ".join([title or "", description, link]).strip()
        items.append((title, text))

    if not items:
        text = " ".join(root.itertext())
        items.append((None, " ".join(text.split())))

    return items
