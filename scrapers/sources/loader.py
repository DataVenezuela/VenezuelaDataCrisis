from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from scrapers.adapters.http_client import USER_AGENT
from scrapers.exporters.staging_exporter import StagingConfig
from scrapers.models.source import SourceConfig
from scrapers.validators.source_validator import validate_sources_config

log = logging.getLogger(__name__)

# Columnas operativas que viven en la tabla ``sources`` y completan una entrada
# thin (solo uuid + parser en el repo). ``parser_asignado`` NO esta aca: es lo
# unico que aporta el repo (el "shape"); la identidad (url/name/...) vive en la DB.
_SOURCE_DB_COLUMNS = (
    "source_id",
    "display_name",
    "source_type",
    "url",
    "required_keywords",
    "governed_tier",
    "refresh_minutes",
    "active",
    "allowed_domains",
    "page_size",
    "cursor_field",
    "full_scan",
    "rate_limit_per_minute",
    "timeout_seconds",
    "max_retries",
    "probe_limit",
    "max_concurrent_pages",
    "max_concurrent_posts",
    "bulk_size",
)

# Campos que la fila de la DB DEBE tener para poder operar la fuente.
_DB_REQUIRED_COLUMNS = ("url", "source_type", "governed_tier", "refresh_minutes")


def load_sources(config_path: Path) -> tuple[dict[str, Any], list[SourceConfig]]:
    """Carga las fuentes del YAML, resolviendo las entradas thin contra la DB.

    Una entrada "completa" (trae ``url``) se arma directo del YAML: es la ruta
    offline/demo y no toca la red. Una entrada "thin" (solo ``id`` UUID +
    ``parser_asignado`` + ``enabled``) se completa con la fila de ``sources`` en
    Supabase, de modo que el repo nunca expone la identidad de la fuente. Si el
    config trae fuentes thin y faltan las env SUPABASE_*, se falla cerrado.
    """
    payload = validate_sources_config(config_path)
    project = payload.get("project", {})
    raw_sources = payload["sources"]

    thin_ids = [source["id"] for source in raw_sources if "url" not in source]
    db_rows: dict[str, dict[str, Any]] = {}
    if thin_ids:
        config = StagingConfig.from_env()
        if config is None:
            raise ValueError(
                "el config trae fuentes thin (solo source_id) pero faltan las env "
                "SUPABASE_*: no se pueden resolver sus definiciones desde la DB. "
                "Setea SUPABASE_URL/SUPABASE_PUBLISHABLE_KEY/SUPABASE_INGEST_JWT o "
                "usa un config completo (con url) para correr offline."
            )
        db_rows = _fetch_source_rows(config, thin_ids)

    sources: list[SourceConfig] = []
    for source in raw_sources:
        if "url" in source:
            sources.append(_source_from_yaml(source))
        else:
            sources.append(_source_from_db(source, db_rows))

    return project, sources


def _source_from_yaml(source: dict[str, Any]) -> SourceConfig:
    """Arma un SourceConfig self-contained desde una entrada completa del YAML."""
    return SourceConfig(
        id=source["id"],
        name=source["name"],
        type=source["type"],
        enabled=bool(source["enabled"]),
        trust_tier=source["trust_tier"],
        url=source["url"],
        refresh_minutes=int(source["refresh_minutes"]),
        parser_asignado=source["parser_asignado"],
        required_keywords=source.get("required_keywords", []) or [],
        notes=source.get("notes"),
        timeout_seconds=source.get("timeout_seconds"),
        max_retries=source.get("max_retries"),
        page_size=source.get("page_size"),
        probe_limit=source.get("probe_limit"),
        max_concurrent_pages=source.get("max_concurrent_pages"),
        max_concurrent_posts=source.get("max_concurrent_posts"),
        allowed_domains=source.get("allowed_domains"),
        rate_limit_per_minute=source.get("rate_limit_per_minute"),
        bulk_size=source.get("bulk_size"),
        full_scan=bool(source.get("full_scan", False)),
        cursor_field=source.get("cursor_field") or None,
    )


def _source_from_db(entry: dict[str, Any], db_rows: dict[str, dict[str, Any]]) -> SourceConfig:
    """Fusiona una entrada thin del repo (uuid + parser) con su fila en ``sources``.

    ``parser_asignado`` y ``enabled`` vienen del repo (bajo code review); todo lo
    demas de la DB. ``enabled`` efectivo = repo ``enabled`` AND DB ``active``.
    Fail-closed si la fila no existe o le falta un campo operativo requerido.
    """
    source_id = entry["id"]
    row = db_rows.get(source_id)
    if row is None:
        raise ValueError(
            f"fuente thin {source_id!r} no existe en la tabla sources (o el JWT no "
            f"tiene SELECT sobre sources): no se puede resolver su definicion "
            f"(fail-closed)."
        )
    for column in _DB_REQUIRED_COLUMNS:
        if row.get(column) in (None, ""):
            raise ValueError(
                f"fuente thin {source_id!r}: la fila en sources no tiene {column!r} "
                f"(requerido para operar). Completar la definicion en la DB."
            )

    enabled = bool(entry["enabled"]) and bool(row.get("active", True))
    return SourceConfig(
        id=source_id,
        name=str(row.get("display_name") or source_id),
        type=str(row["source_type"]),
        enabled=enabled,
        trust_tier=str(row["governed_tier"]),
        url=str(row["url"]),
        refresh_minutes=int(row["refresh_minutes"]),
        parser_asignado=entry["parser_asignado"],
        required_keywords=list(row.get("required_keywords") or []),
        notes=None,
        timeout_seconds=row.get("timeout_seconds"),
        max_retries=row.get("max_retries"),
        page_size=row.get("page_size"),
        probe_limit=row.get("probe_limit"),
        max_concurrent_pages=row.get("max_concurrent_pages"),
        max_concurrent_posts=row.get("max_concurrent_posts"),
        allowed_domains=row.get("allowed_domains"),
        rate_limit_per_minute=row.get("rate_limit_per_minute"),
        bulk_size=row.get("bulk_size"),
        full_scan=bool(row.get("full_scan", False)),
        cursor_field=row.get("cursor_field") or None,
    )


def _fetch_source_rows(config: StagingConfig, ids: list[str]) -> dict[str, dict[str, Any]]:
    """Trae las filas de ``sources`` para ``ids`` via PostgREST, indexadas por uuid.

    No loguea la url/nombre de ninguna fuente ni el body de la respuesta (traen la
    identidad de las fuentes): solo el status en caso de error.
    """
    params = {
        "source_id": f"in.({','.join(ids)})",
        "select": ",".join(_SOURCE_DB_COLUMNS),
    }
    headers = {
        "apikey": config.publishable_key,
        "Authorization": f"Bearer {config.ingest_jwt}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    with httpx.Client(
        base_url=config.supabase_url,
        headers=headers,
        timeout=httpx.Timeout(30.0),
        follow_redirects=False,
    ) as client:
        resp = client.get("/rest/v1/sources", params=params)
    if resp.status_code != 200:
        raise ValueError(
            f"no se pudieron resolver las fuentes desde la DB: status={resp.status_code} "
            f"(verificar SUPABASE_INGEST_JWT y grants SELECT del rol scraper_ingest "
            f"sobre sources)."
        )
    rows = resp.json()
    if not isinstance(rows, list):
        raise ValueError(
            "respuesta inesperada de PostgREST al resolver sources (no es una lista)."
        )
    return {str(row.get("source_id")): row for row in rows if row.get("source_id")}
