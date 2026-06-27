from __future__ import annotations

import math
from scrapers.normalizers.text import normalize_for_match


def spanish_metaphone(text: str | None) -> str:
    """Codificación fonética adaptada al español (Metaphone-ES) palabra por palabra."""
    if not text:
        return ""

    norm_text = normalize_for_match(text)
    if not norm_text:
        return ""

    words = norm_text.split()
    keys = []
    for word in words:
        key = _metaphone_word(word)
        if key:
            keys.append(key)
    return " ".join(keys)


def _metaphone_word(word: str) -> str:
    if not word:
        return ""

    def string_at(string: str, start: int, length: int, lista: list[str]) -> bool:
        if start < 0 or start >= len(string):
            return False
        for expr in lista:
            if string.find(expr, start, start + length) != -1:
                return True
        return False

    def is_vowel(string: str, pos: int) -> bool:
        if pos < 0 or pos >= len(string):
            return False
        return string[pos] in ("A", "E", "I", "O", "U")

    def strtr(st: str) -> str:
        st = st.replace("á", "a")
        st = st.replace("ch", "x")
        st = st.replace("ç", "s")
        st = st.replace("é", "e")
        st = st.replace("í", "i")
        st = st.replace("ó", "o")
        st = st.replace("ú", "u")
        st = st.replace("ñ", "ny")
        st = st.replace("gü", "w")
        st = st.replace("ü", "u")
        st = st.replace("b", "v")
        st = st.replace("ll", "y")
        # Seseo: homogenización de Z y S.
        st = st.replace("z", "s")
        return st

    clean_word = strtr(word.lower().strip())
    if not clean_word:
        return ""

    original_string = clean_word.upper() + "    "
    meta_key = ""
    key_length = 8
    current_pos = 0

    while len(meta_key) < key_length:
        if current_pos >= len(clean_word):
            break

        current_char = original_string[current_pos]

        if is_vowel(original_string, current_pos):
            if current_pos == 0:
                meta_key += current_char
            current_pos += 1
        else:
            # Consonantes simples
            if string_at(original_string, current_pos, 1, ["D", "F", "J", "K", "M", "N", "P", "T", "V", "L", "Y"]):
                meta_key += current_char
                if original_string[current_pos + 1] == current_char:
                    current_pos += 2
                else:
                    current_pos += 1

            elif current_char == "C":
                if original_string[current_pos + 1] == "C":
                    meta_key += "X"
                    current_pos += 2
                elif string_at(original_string, current_pos, 2, ["CE", "CI"]):
                    meta_key += "Z"
                    current_pos += 2
                else:
                    meta_key += "K"
                    current_pos += 1

            elif current_char == "G":
                if string_at(original_string, current_pos, 2, ["GE", "GI"]):
                    meta_key += "J"
                    current_pos += 2
                else:
                    meta_key += "G"
                    current_pos += 1

            elif current_char == "H":
                if is_vowel(original_string, current_pos + 1):
                    meta_key += original_string[current_pos + 1]
                    current_pos += 2
                else:
                    meta_key += "H"
                    current_pos += 1

            elif current_char == "Q":
                if original_string[current_pos + 1] == "U":
                    current_pos += 2
                else:
                    current_pos += 1
                meta_key += "K"

            elif current_char == "W":
                meta_key += "U"
                current_pos += 1

            elif current_char == "R":
                meta_key += "R"
                if original_string[current_pos + 1] == "R":
                    current_pos += 2
                else:
                    current_pos += 1

            elif current_char == "S":
                if not is_vowel(original_string, current_pos + 1) and current_pos == 0:
                    meta_key += "ES"
                    current_pos += 1
                else:
                    meta_key += "S"
                    current_pos += 1

            elif current_char == "Z":
                meta_key += "Z"
                current_pos += 1

            elif current_char == "X":
                if not is_vowel(original_string, current_pos + 1) and len(clean_word) > 1 and current_pos == 0:
                    meta_key += "EX"
                    current_pos += 1
                else:
                    meta_key += "X"
                    current_pos += 1
            else:
                current_pos += 1

    # Si la palabra original termina en vocal, la preservamos al final de la clave
    # para distinguir generos y terminaciones criticas (ej. Maria vs Mario, Juan vs Juana)
    last_char = word[-1].upper()
    if last_char in ("A", "E", "I", "O", "U"):
        if not meta_key.endswith(last_char):
            meta_key += last_char

    return meta_key.strip()


def jaro_similarity(s1: str, s2: str) -> float:
    """Calcula la similitud Jaro basica entre dos cadenas."""
    s1 = "".join(s1.split()).lower()
    s2 = "".join(s2.split()).lower()

    len_s1, len_s2 = len(s1), len(s2)
    if len_s1 == 0 and len_s2 == 0:
        return 1.0
    if len_s1 == 0 or len_s2 == 0:
        return 0.0

    # Ventana de coincidencia
    match_bound = max(0, (max(len_s1, len_s2) // 2) - 1)

    s1_matches = [False] * len_s1
    s2_matches = [False] * len_s2

    matches = 0
    transpositions = 0

    # Encontrar coincidencias
    for i in range(len_s1):
        start = max(0, i - match_bound)
        end = min(i + match_bound + 1, len_s2)
        for j in range(start, end):
            if s2_matches[j]:
                continue
            if s1[i] == s2[j]:
                s1_matches[i] = True
                s2_matches[j] = True
                matches += 1
                break

    if matches == 0:
        return 0.0

    # Contar transposiciones
    k = 0
    for i in range(len_s1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1

    return (matches / len_s1 + matches / len_s2 + (matches - transpositions / 2) / matches) / 3.0


def jaro_winkler_similarity(s1: str, s2: str, p: float = 0.1) -> float:
    """Calcula la similitud Jaro-Winkler, dando peso al prefijo comun."""
    s1 = "".join(s1.split()).lower()
    s2 = "".join(s2.split()).lower()

    jaro = jaro_similarity(s1, s2)

    # Longitud del prefijo comun (maximo 4 caracteres)
    prefix = 0
    for i in range(min(len(s1), len(s2), 4)):
        if s1[i] == s2[i]:
            prefix += 1
        else:
            break

    return jaro + (prefix * p * (1.0 - jaro))


def phonetic_similarity(s1: str, s2: str) -> float:
    """Calcula similitud ponderada entre nombres reales y sus Metaphone-ES."""
    if not s1 or not s2:
        return 0.0

    norm_s1 = normalize_for_match(s1)
    norm_s2 = normalize_for_match(s2)

    if not norm_s1 or not norm_s2:
        return 0.0

    sim_original = jaro_winkler_similarity(norm_s1, norm_s2)

    meta_s1 = spanish_metaphone(norm_s1)
    meta_s2 = spanish_metaphone(norm_s2)

    if not meta_s1 or not meta_s2:
        return sim_original

    sim_metaphone = jaro_winkler_similarity(meta_s1, meta_s2)

    # Penalización de desinencias de género (o/a y vocal/consonante) para mitigar colapsos erróneos entre hermanos (ej. Juan/Juana, Mario/Maria).
    words1 = norm_s1.split()
    words2 = norm_s2.split()
    if words1 and words2:
        first1 = words1[0]
        first2 = words2[0]
        if first1 and first2:
            last1 = first1[-1]
            last2 = first2[-1]
            if (last1 == 'a' and last2 == 'o') or (last1 == 'o' and last2 == 'a'):
                sim_original -= 0.08
                sim_metaphone -= 0.08
            elif (last1 in ('a', 'o') and last2 not in ('a', 'e', 'i', 'o', 'u')) or \
                 (last2 in ('a', 'o') and last1 not in ('a', 'e', 'i', 'o', 'u')):
                sim_original -= 0.08
                sim_metaphone -= 0.08

    if meta_s1 == meta_s2:
        if sim_original < 0.75:
            return 0.4 * sim_original + 0.6 * sim_metaphone
        return max(sim_original, 0.95)

    return 0.4 * sim_original + 0.6 * sim_metaphone
