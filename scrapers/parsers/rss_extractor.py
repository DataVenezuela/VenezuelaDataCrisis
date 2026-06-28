from __future__ import annotations

import defusedxml.ElementTree as ET

_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def extract_rss_items(raw: str) -> list[tuple[str | None, str]]:
    root = ET.fromstring(raw)
    items: list[tuple[str | None, str]] = []

    # RSS 2.0: <item> elements
    for item in root.findall(".//item"):
        title = item.findtext("title")
        description = item.findtext("description") or ""
        link = item.findtext("link") or ""
        text = " ".join([title or "", description, link]).strip()
        items.append((title, text))

    # Atom: <entry> elements (with or without namespace)
    if not items:
        entries = root.findall(f".//{_ATOM_NS}entry") or root.findall(".//entry")
        for entry in entries:
            title = (
                entry.findtext(f"{_ATOM_NS}title")
                or entry.findtext("title")
            )
            summary = (
                entry.findtext(f"{_ATOM_NS}summary")
                or entry.findtext(f"{_ATOM_NS}content")
                or entry.findtext("summary")
                or entry.findtext("content")
                or ""
            )
            link_el = entry.find(f"{_ATOM_NS}link")
            if link_el is None:
                link_el = entry.find("link")
            link = (link_el.get("href") if link_el is not None else "") or ""
            text = " ".join([title or "", summary, link]).strip()
            items.append((title, text))

    # Fallback: return all text as a single item
    if not items:
        text = " ".join(root.itertext())
        items.append((None, " ".join(text.split())))

    return items
