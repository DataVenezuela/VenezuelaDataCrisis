from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_for_match(text: str | None) -> str:
    text = normalize_text(text).lower()
    text = "".join(
        ch for ch in unicodedata.normalize("NFD", text)
        if unicodedata.category(ch) != "Mn"
    )
    text = re.sub(r"[^a-z0-9áéíóúñü\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# --- Adiciones (issue #13): abreviaciones VE + casing de nombres + unicode configurable.
# Estrictamente aditivo: normalize_text y normalize_for_match quedan intactas porque
# son consumidas por claim_extractor.py, fingerprint.py y geo.py.

_ABBREV_PATH = Path(__file__).resolve().parents[1] / "config" / "ve_abbreviations.json"

_FALLBACK_ABBREVIATIONS: dict[str, str] = {
    "edo": "Estado", "edo.": "Estado",
    "mun": "Municipio", "mun.": "Municipio",
    "av": "Avenida", "av.": "Avenida",
    "urb": "Urbanización", "urb.": "Urbanización",
    "pq": "Parque", "pq.": "Parque",
    "c/c": "Centro Comercial", "cc": "Centro Comercial",
    "dtto": "Distrito", "dtto.": "Distrito",
}

_CONNECTORS = {"de", "del", "la", "las", "los", "y", "e"}

# Captura tokens (incluye '.' y '/' internos como en 'Edo.' o 'C/C'), espacios y
# cualquier otro caracter de puntuacion como elementos separados.
_TOKEN_RE = re.compile(r"[\w/]+\.?|\s+|[^\w\s]")


@lru_cache(maxsize=1)
def _load_abbreviations() -> dict[str, str]:
    """Carga el diccionario de abreviaciones desde el JSON de config.

    Degradacion con gracia: si el fichero falta o esta corrupto, usa un
    fallback embebido minimo en lugar de lanzar una excepcion, de modo que el
    pipeline nunca se rompe por un problema de config.
    """
    try:
        data = json.loads(_ABBREV_PATH.read_text(encoding="utf-8"))
        return {str(k).lower(): str(v) for k, v in data.items()}
    except (OSError, ValueError):
        return dict(_FALLBACK_ABBREVIATIONS)


def expand_abbreviations(text: str | None, mapping: dict[str, str] | None = None) -> str:
    """Expande abreviaciones venezolanas usando un diccionario configurable.

    El mapa por defecto vive en ``scrapers/config/ve_abbreviations.json`` (no
    hardcodeado en la funcion). Se puede inyectar otro ``mapping`` sin tocar el
    codigo. Conserva los tokens no reconocidos y los signos de puntuacion, pero
    colapsa cualquier secuencia de espacios en blanco a un unico espacio.

    Ejemplo: ``'Edo. Zulia, Mun. Maracaibo'`` -> ``'Estado Zulia, Municipio Maracaibo'``.
    """
    if not text:
        return ""
    table = mapping if mapping is not None else _load_abbreviations()
    table = {k.lower(): v for k, v in table.items()}
    out: list[str] = []
    for tok in _TOKEN_RE.findall(text):
        key = tok.lower()
        repl = table.get(key)
        if repl is None and key.endswith("."):
            repl = table.get(key[:-1])
        out.append(repl if repl is not None else tok)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def normalize_proper_name(text: str | None) -> str:
    """Title Case consistente para nombres propios venezolanos.

    Capitaliza cada palabra salvo conectores (de, del, la, las, los, y, e),
    que van en minuscula excepto si abren el nombre. Respeta acentos.

    Ejemplo: ``'JOSÉ  pérez de la cruz'`` -> ``'José Pérez de la Cruz'``.
    """
    base = normalize_text(text)
    if not base:
        return ""
    words = base.split(" ")
    result: list[str] = []
    for i, w in enumerate(words):
        low = w.lower()
        if i > 0 and low in _CONNECTORS:
            result.append(low)
        else:
            result.append(low[:1].upper() + low[1:])
    return " ".join(result)


def normalize_unicode(text: str | None, form: str = "NFKC") -> str:
    """Normalizacion Unicode configurable (NFC/NFKC/NFD/NFKD) + colapso de espacios.

    Permite elegir la forma sin alterar ``normalize_text`` (que sigue fija en
    NFKC por retrocompatibilidad con fingerprint y los PRs en vuelo).
    """
    if not text:
        return ""
    text = unicodedata.normalize(form, text)
    return re.sub(r"\s+", " ", text).strip()
