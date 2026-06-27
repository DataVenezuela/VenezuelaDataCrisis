from __future__ import annotations

import json
from scrapers.extractors.custom_json_extractor import (
    extract_geojson_earthquakes,
    extract_reliefweb_reports,
)


def test_extract_geojson_earthquakes():
    # Payload mock de USGS GeoJSON con dos eventos sísmicos
    mock_geojson = {
        "type": "FeatureCollection",
        "metadata": {
            "title": "USGS Earthquakes"
        },
        "features": [
            {
                "type": "Feature",
                "id": "us6000t8k6",
                "properties": {
                    "mag": 5.2,
                    "place": "24 km W of Güiria, Venezuela",
                    "time": 1719472300000,
                    "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us6000t8k6"
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [-62.521, 10.598, 85.3]
                }
            },
            {
                "type": "Feature",
                "id": "us6000t8k7",
                "properties": {
                    "mag": 4.1,
                    "place": "10 km N of Irapa, Venezuela",
                    "time": 1719478300000,
                    "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us6000t8k7"
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [-62.581, 10.681, 10.0]
                }
            }
        ]
    }

    raw_str = json.dumps(mock_geojson)
    results = extract_geojson_earthquakes(raw_str)

    assert len(results) == 2
    
    # Validar primer evento
    title1, text1, meta1 = results[0]
    assert title1 == "Sismo M 5.2 - 24 km W of Güiria, Venezuela"
    assert "Ubicación: 24 km W of Güiria, Venezuela" in text1
    assert "Magnitud: 5.2" in text1
    assert meta1["geojson_id"] == "us6000t8k6"
    assert meta1["magnitude"] == 5.2
    assert meta1["coordinates"] == [-62.521, 10.598, 85.3]

    # Validar segundo evento
    title2, text2, meta2 = results[1]
    assert title2 == "Sismo M 4.1 - 10 km N of Irapa, Venezuela"
    assert meta2["geojson_id"] == "us6000t8k7"
    assert meta2["magnitude"] == 4.1


def test_extract_reliefweb_reports():
    # Payload mock de ReliefWeb Reports con dos reportes
    mock_reliefweb = {
        "data": [
            {
                "id": "412345",
                "fields": {
                    "title": "Venezuela: Earthquake - Jun 2026",
                    "body": "Un sismo de magnitud 5.2 sacudió el oriente de Venezuela afectando viviendas.",
                    "url": "https://reliefweb.int/report/venezuela/venezuela-earthquake-jun-2026"
                }
            },
            {
                "id": "412346",
                "fields": {
                    "title": "Situación humanitaria en Güiria",
                    "description": "Se reportan necesidades de agua potable y evaluación estructural de hospitales.",
                    "url": "https://reliefweb.int/report/venezuela/situacion-humanitaria-guiria"
                }
            }
        ]
    }

    raw_str = json.dumps(mock_reliefweb)
    results = extract_reliefweb_reports(raw_str)

    assert len(results) == 2

    # Validar primer reporte
    title1, text1, meta1 = results[0]
    assert title1 == "Venezuela: Earthquake - Jun 2026"
    assert "afectando viviendas" in text1
    assert meta1["reliefweb_id"] == "412345"
    assert meta1["url"] == "https://reliefweb.int/report/venezuela/venezuela-earthquake-jun-2026"

    # Validar segundo reporte
    title2, text2, meta2 = results[1]
    assert title2 == "Situación humanitaria en Güiria"
    assert "agua potable" in text2
    assert meta2["reliefweb_id"] == "412346"
