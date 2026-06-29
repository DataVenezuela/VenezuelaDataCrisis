from __future__ import annotations

from scrapers.parsers.rss_extractor import extract_rss_items

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Feed RSS</title>
    <item>
      <title>RSS Uno</title>
      <description>Persona en Lara</description>
      <link>https://example.test/rss/1</link>
    </item>
  </channel>
</rss>
"""

ATOM_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Feed Atom</title>
  <entry>
    <title>Atom Uno</title>
    <summary>Persona en Zulia</summary>
    <link href="https://example.test/atom/1"/>
  </entry>
  <entry>
    <title>Atom Dos</title>
    <summary>Centro en Carabobo</summary>
    <link href="https://example.test/atom/2"/>
  </entry>
</feed>
"""


def test_rss_items_still_parsed_per_item() -> None:
    items = extract_rss_items(RSS_SAMPLE)

    assert items == [("RSS Uno", "RSS Uno Persona en Lara https://example.test/rss/1")]


def test_atom_entries_parsed_per_entry() -> None:
    items = extract_rss_items(ATOM_SAMPLE)

    assert items == [
        ("Atom Uno", "Atom Uno Persona en Zulia https://example.test/atom/1"),
        ("Atom Dos", "Atom Dos Centro en Carabobo https://example.test/atom/2"),
    ]


def test_atom_uses_content_when_no_summary() -> None:
    atom = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<entry>"
        "<title>Con content</title>"
        "<content>Cuerpo en content</content>"
        '<link href="https://example.test/atom/c"/>'
        "</entry></feed>"
    )

    items = extract_rss_items(atom)

    assert items == [("Con content", "Con content Cuerpo en content https://example.test/atom/c")]


def test_atom_link_prefers_rel_alternate_href() -> None:
    atom = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<entry>"
        "<title>Multi link</title>"
        "<summary>resumen</summary>"
        '<link rel="self" href="https://example.test/atom/self"/>'
        '<link rel="alternate" href="https://example.test/atom/alt"/>'
        "</entry></feed>"
    )

    items = extract_rss_items(atom)

    # Toma el href del rel="alternate" (el canónico), no el de rel="self".
    assert items[0][1].endswith("https://example.test/atom/alt")
    assert "atom/self" not in items[0][1]


def test_atom_link_is_href_attribute_not_text() -> None:
    # En Atom el link es un atributo href; un <link> sin href no aporta texto.
    atom = (
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<entry><title>Sin href</title><summary>x</summary><link/></entry></feed>"
    )

    items = extract_rss_items(atom)

    assert items == [("Sin href", "Sin href x")]


def test_atom_content_xhtml_keeps_nested_body() -> None:
    # <content type="xhtml"> guarda el texto en hijos; findtext lo perdía.
    atom = (
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<entry>"
        "<title>Herida</title>"
        '<content type="xhtml"><div><p>Persona herida en Lara</p></div></content>'
        '<link href="https://example.test/atom/x"/>'
        "</entry></feed>"
    )

    items = extract_rss_items(atom)

    assert items == [("Herida", "Herida Persona herida en Lara https://example.test/atom/x")]


def test_atom_link_skips_href_less_alternate() -> None:
    # El alternate no tiene href → cae al siguiente link que sí lo tiene.
    atom = (
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<entry>"
        "<title>Sin href en alternate</title>"
        "<summary>x</summary>"
        '<link rel="alternate"/>'
        '<link rel="self" href="https://example.test/atom/self"/>'
        "</entry></feed>"
    )

    items = extract_rss_items(atom)

    assert items[0][1].endswith("https://example.test/atom/self")


def test_atom_without_namespace_is_parsed_as_fallback() -> None:
    # Feed Atom no-estándar (sin xmlns); se acepta como red de seguridad (#120).
    atom = (
        "<feed>"
        "<entry>"
        "<title>Sin ns</title>"
        "<summary>Persona en Táchira</summary>"
        '<link href="https://example.test/nons/1"/>'
        "</entry></feed>"
    )

    items = extract_rss_items(atom)

    assert items == [("Sin ns", "Sin ns Persona en Táchira https://example.test/nons/1")]


def test_unknown_format_falls_back_to_full_text() -> None:
    other = '<feed xmlns="http://example.com/otro"><node>hola mundo</node></feed>'

    items = extract_rss_items(other)

    assert len(items) == 1
    assert items[0][0] is None
    assert "hola mundo" in items[0][1]
