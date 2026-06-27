from scrapers.dedup.phonetics import (
    spanish_metaphone,
    jaro_winkler_similarity,
    phonetic_similarity,
)


def test_spanish_metaphone_exact():
    assert spanish_metaphone("Claudio") == spanish_metaphone("Klaudio")
    assert spanish_metaphone("Gimenez") == spanish_metaphone("Jimenez")
    assert spanish_metaphone("Zapato") == spanish_metaphone("Sapato")
    assert spanish_metaphone("Huevo") == spanish_metaphone("Webo")
    assert spanish_metaphone("Aboyar") == spanish_metaphone("Abollar")


def test_spanish_metaphone_empty():
    assert spanish_metaphone("") == ""
    assert spanish_metaphone(None) == ""


def test_jaro_winkler_similarity():
    # Coincidencia exacta
    assert jaro_winkler_similarity("Claudio", "Claudio") == 1.0
    
    # Diferencia de una letra
    sim1 = jaro_winkler_similarity("Hernandez", "Hernandes")
    assert sim1 > 0.9
    
    # Diferencia significativa
    sim2 = jaro_winkler_similarity("Claudio", "Maria")
    assert sim2 < 0.6


def test_phonetic_similarity_names():
    # Misma persona, errores ortográficos comunes
    assert phonetic_similarity("Claudio Hernandez", "Klaudio Hernandes") >= 0.92
    assert phonetic_similarity("Alejandro Gimenez", "Alexandro Jimenez") >= 0.90
    
    # Nombres diferentes pero con sonido o estructura similar (género opuesto)
    # Deben ser lo suficientemente bajos o no dar 1.0 para evitar falso positivo
    assert phonetic_similarity("Maria Rodriguez", "Mario Rodriguez") < 0.95
    assert phonetic_similarity("Juan Perez", "Juana Perez") < 0.95
    
    # Nombres completamente distintos
    assert phonetic_similarity("Claudio", "Rodrigo") < 0.70
