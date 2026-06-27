from __future__ import annotations

import json
from typing import Any


def extract_geojson_earthquakes(raw: str) -> list[tuple[str | None, str, dict[str, Any]]]:
    """
    Parsea respuestas GeoJSON de USGS Earthquakes.
    Retorna una lista de tuplas (title, text, metadata) para cada feature.
    """
    try:
        data = json.loads(raw)
    except Exception:
        return []

    results: list[tuple[str | None, str, dict[str, Any]]] = []
    features = data.get("features", [])
    
    for feature in features:
        if not isinstance(feature, dict):
            continue
            
        props = feature.get("properties", {}) or {}
        geom = feature.get("geometry", {}) or {}
        coords = geom.get("coordinates", []) or []
        
        place = props.get("place") or "Ubicación desconocida"
        mag = props.get("mag")
        time_epoch = props.get("time") or 0
        url = props.get("url") or ""
        
        # Formatear el tiempo a algo legible si es posible
        import datetime
        try:
            time_str = datetime.datetime.fromtimestamp(time_epoch / 1000, datetime.UTC).isoformat()
        except Exception:
            time_str = str(time_epoch)
            
        title = f"Sismo M {mag} - {place}" if mag is not None else f"Sismo - {place}"
        text = (
            f"Evento sísmico reportado por USGS.\n"
            f"Ubicación: {place}\n"
            f"Magnitud: {mag if mag is not None else 'N/A'}\n"
            f"Fecha/Hora (UTC): {time_str}\n"
            f"Coordenadas: Lat {coords[1] if len(coords) > 1 else 'N/A'}, Lon {coords[0] if len(coords) > 0 else 'N/A'}\n"
            f"Detalles: {url}"
        )
        
        metadata = {
            "geojson_id": feature.get("id"),
            "magnitude": mag,
            "place": place,
            "coordinates": coords,
            "time": time_str,
            "source_type": "usgs_geojson",
        }
        results.append((title, text, metadata))
        
    return results


def extract_reliefweb_reports(raw: str) -> list[tuple[str | None, str, dict[str, Any]]]:
    """
    Parsea respuestas de la API de ReliefWeb Reports.
    Retorna una lista de tuplas (title, text, metadata) para cada reporte.
    """
    try:
        data = json.loads(raw)
    except Exception:
        return []

    results: list[tuple[str | None, str, dict[str, Any]]] = []
    items = data.get("data", [])
    
    for item in items:
        if not isinstance(item, dict):
            continue
            
        fields = item.get("fields", {}) or {}
        title = fields.get("title")
        body = fields.get("body") or fields.get("description") or ""
        url = fields.get("url") or ""
        
        text = f"{title}\n\n{body}\n\nFuente/Link: {url}".strip()
        metadata = {
            "reliefweb_id": item.get("id"),
            "url": url,
            "source_type": "reliefweb_report",
        }
        results.append((title, text, metadata))
        
    return results
