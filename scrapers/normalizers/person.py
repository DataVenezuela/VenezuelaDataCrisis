from __future__ import annotations

from scrapers.normalizers.text import normalize_for_match


# Honoríficos/títulos que no aportan a la identidad y ensucian el match.
_HONORIFICS = {
    "sr", "sra", "srta", "don", "dona", "doña", "dr", "dra",
    "ing", "lic", "prof", "sor", "padre", "hermano", "hermana",
}


def normalize_person_name(name: str | None) -> str:
    """Normaliza un nombre para comparación: sin acentos, minúsculas, sin honoríficos.

    "Sr. José  Pérez" -> "jose perez". No reordena apellidos (eso lo decide el matcher)."""
    normalized = normalize_for_match(name)
    if not normalized:
        return ""
    tokens = [tok for tok in normalized.split() if tok not in _HONORIFICS]
    return " ".join(tokens)


def name_key(name: str | None) -> str:
    """Llave de blocking estable: tokens del nombre ordenados alfabéticamente.

    Hace que "Jose Perez" y "Perez Jose" caigan en el mismo bloque candidato."""
    normalized = normalize_person_name(name)
    if not normalized:
        return ""
    return " ".join(sorted(normalized.split()))


def derive_is_minor(age_range: dict[str, int] | None) -> bool | None:
    """True/False si la edad permite determinarlo; None si no se conoce.

    Usa ``min`` (el extremo más bajo del rango) para que un rango estimado
    (ej. {"min": 10, "max": 60}) no deje de proteger a un posible menor solo
    porque el extremo superior del rango es mayor de edad.
    """
    if age_range is None:
        return None
    return age_range["min"] < 18
