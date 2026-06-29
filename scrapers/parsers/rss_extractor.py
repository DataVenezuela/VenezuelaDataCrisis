from __future__ import annotations

from typing import Any

import defusedxml.ElementTree as ET

_ATOM_NS = "http://www.w3.org/2005/Atom"
_NS = {"atom": _ATOM_NS}


def extract_rss_items(raw: str) -> list[tuple[str | None, str]]:
    """Extrae entradas de un feed RSS (``<item>``) o Atom (``<entry>``).

    Devuelve una lista de ``(title, text)`` por entrada.  Soporta:

    - **RSS 2.0**: ``<item>`` con ``title`` / ``description`` / ``link`` (texto).
    - **Atom**: ``<entry>`` con namespace ``{http://www.w3.org/2005/Atom}``
      (estándar), usando ``title`` + ``summary``/``content`` y el ``href`` del
      ``<link>`` (en Atom el link es un atributo, no texto).  Como red de
      seguridad también se aceptan ``<entry>`` sin namespace (feeds no-estándar).

    Si el feed no tiene ni ``<item>`` ni ``<entry>`` reconocibles, degrada a un
    único item con todo el texto del feed (formato desconocido — no se descarta).
    """
    root = ET.fromstring(raw)
    items: list[tuple[str | None, str]] = []

    # RSS 2.0: <item> sin namespace.
    for item in root.findall(".//item"):
        title = item.findtext("title")
        description = item.findtext("description") or ""
        link = item.findtext("link") or ""
        text = " ".join([title or "", description, link]).strip()
        items.append((title, text))

    # Atom: <entry> con namespace estándar; sin namespace como fallback
    # no-estándar (aporte de #120 / @EChachati).
    atom_entries = root.findall(".//atom:entry", _NS) or root.findall(".//entry")
    for entry in atom_entries:
        title = _atom_text(entry, "title")
        body = _atom_text(entry, "summary") or _atom_text(entry, "content") or ""
        link = _atom_link(entry)
        text = " ".join([title or "", body, link]).strip()
        items.append((title, text))

    if not items:
        text = " ".join(root.itertext())
        items.append((None, " ".join(text.split())))

    return items


def _atom_text(entry: Any, localname: str) -> str | None:
    """Texto completo de un elemento Atom hijo (por nombre local), normalizado.

    Busca con namespace estándar y, como fallback, sin namespace.  Usa
    ``itertext`` (no ``findtext``/``.text``) para no perder el cuerpo de
    ``<content type="xhtml">`` / ``type="html">``, donde el texto vive en
    elementos anidados.  Devuelve ``None`` si el elemento no existe o queda vacío.
    """
    el = entry.find(f"atom:{localname}", _NS)
    if el is None:
        el = entry.find(localname)
    if el is None:
        return None
    text = " ".join("".join(el.itertext()).split())
    return text or None


def _atom_link(entry: Any) -> str:
    """Devuelve el ``href`` del ``<link>`` de Atom (atributo, no texto).

    Busca ``<link>`` con namespace estándar y, como fallback, sin namespace.
    Prefiere ``rel="alternate"`` (el enlace canónico; Atom trata la ausencia de
    ``rel`` como ``alternate``), salta los ``<link>`` sin ``href`` y, si ningún
    alternate tiene ``href``, cae al primer link que sí lo tenga.
    """
    links = entry.findall("atom:link", _NS) or entry.findall("link")
    ordered = sorted(links, key=lambda link: link.get("rel", "alternate") != "alternate")
    for link in ordered:
        href = link.get("href")
        if href:
            return str(href)
    return ""
