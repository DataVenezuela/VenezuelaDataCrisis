"""
scrapers/pipelines/run_pipeline.py
====================================
Orquestador principal del pipeline VZLA_DEDUP.

Flujo por fuente habilitada
----------------------------
1. **Adapter**   — fetch del contenido raw segun ``source.type``
2. **Parser**    — convierte raw -> list[Person | AcopioCenter | Event]
3. **PII**       — ``tokenize_pii_fields`` sobre cada entidad (model_dump)
4. **Enriquecimiento** — normaliza last_known_location/date_iso y calcula
                   ``deterministic_id`` (el exporter lo usa como external_id
                   de Person)
5. **Score**     — ``confidence_score`` sobre cada entidad
6. **Minor protection** — ``protect_minor_fields`` reduce campos identificables
                   cuando is_minor=True (foto, cedula_masked, ubicacion exacta)
7. **Staging**   — ``StagingExporter`` hace POST a /api/aportes de dataVenezuela

La deduplicacion ya no ocurre por fuente: el dedup_hash/external_id deterministas
y las block keys (scrapers/dedup/specs.py) la trasladan al backend (upsert por
external_id) y al consolidation job de Stage 2 (#82).

Principios de resiliencia
--------------------------
- Un error en un registro individual no tumba el pipeline.
- Un error en una fuente entera se loguea y se continua con la siguiente.
- Todos los errores se acumulan en el summary para visibilidad.

Summary devuelto
-----------------
El dict de retorno tiene las keys que espera ``cli.py``:
  sources_processed   int  — fuentes completadas sin error fatal
  staging_sent        int  — aportes aceptados por staging (200/201)
  staging_duplicates  int  — aportes ya existentes en staging (409)
  staging_errors      int  — errores por registro o de watermark
  errors              list[str]  — mensajes de error no fatales
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from scrapers.adapters._shared import now_utc, sha256_hex
from scrapers.adapters.base import RawContent
from scrapers.exporters.staging_exporter import (
    ExportResult,
    StagingConfig,
    StagingExporter,
)
from scrapers.models import AcopioCenter, Event, Person
from scrapers.models._validators import validate_uuid_str
from scrapers.models.source import SourceConfig
from scrapers.normalizers import normalize_date, normalize_location
from scrapers.sanitizers.minor_protection import protect_minor_fields
from scrapers.sanitizers.pii_tokenizer import tokenize_pii_fields
from scrapers.sources.loader import load_sources
from scrapers.validators.quality import confidence_score

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tipos internos
# ---------------------------------------------------------------------------

ParsedEntity = Person | AcopioCenter | Event


def _error_summary(message: str) -> dict[str, Any]:
    """Summary de salida temprana con las keys nuevas de staging."""
    return {
        "sources_processed": 0,
        "staging_sent": 0,
        "staging_duplicates": 0,
        "staging_errors": 0,
        "errors": [message],
    }


# ---------------------------------------------------------------------------
# Registry: adapters y parsers
# ---------------------------------------------------------------------------

def _get_adapter(source: SourceConfig) -> Any:
    """
    Devuelve la instancia del adapter adecuado para el type de la fuente.

    Tipos soportados:
      api_json    -> ApiAdapter (httpx, paginacion automatica)
      html_static -> HtmlAdapter (requests + BeautifulSoup, devuelve texto limpio)
      manual_file / text -> local_file (lectura local)
      pdf         -> PdfAdapter (pdfplumber, texto por pagina)
      rss         -> RssAdapter (extrae items del feed RSS/Atom)
      webapp_js   -> PlaywrightAdapter (browser headless, paginas con JS)

    Tipos sin adapter registrado devuelven None y la fuente se omite.
    """
    stype = source.type

    if stype == "api_json":
        from scrapers.adapters.api_adapter import (
            ApiAdapter,
            _DEFAULT_TIMEOUT,
            _MAX_RETRIES,
        )
        # base_url = esquema + host; el path se pasa en fetch_all
        import httpx
        parsed = httpx.URL(source.url)
        base_url = f"{parsed.scheme}://{parsed.host}"
        if parsed.port:
            base_url += f":{parsed.port}"
        path = parsed.path or "/"
        adapter_kwargs: dict[str, Any] = {
            "base_url": base_url,
            "source_key": source.id,
            "default_path": path,
            "timeout": source.timeout_seconds if source.timeout_seconds is not None else _DEFAULT_TIMEOUT,
            "max_retries": source.max_retries if source.max_retries is not None else _MAX_RETRIES,
            "max_concurrent_pages": source.max_concurrent_pages,
        }
        # page_size es opcional: cada fuente declara el limite real que su
        # API soporta (algunas aceptan 1000+, otras capan en 50). Sin
        # override, ApiAdapter usa su propio default (_DEFAULT_PAGE_SIZE).
        if source.page_size is not None:
            adapter_kwargs["page_size"] = source.page_size
        adapter = ApiAdapter(**adapter_kwargs)
        return adapter

    if stype == "html_static":
        from scrapers.adapters.html_adapter import HtmlAdapter
        return HtmlAdapter.from_source_config(source)

    if stype == "rss":
        from scrapers.adapters.rss_adapter import RssAdapter
        return RssAdapter.from_source_config(source)

    if stype in ("manual_file", "text"):
        from scrapers.adapters.local_file import read_local_file
        return _LocalFileAdapter(source_key=source.id, read_fn=read_local_file)

    if stype == "pdf":
        from scrapers.adapters.pdf_adapter import PdfAdapter
        return PdfAdapter.from_source_config(source)

    if stype == "webapp_js":
        from scrapers.adapters.playwright_adapter import PlaywrightAdapter
        return PlaywrightAdapter.from_source_config(source)

    log.warning(
        "Adapter para type=%r no implementado (fuente=%s) — omitida",
        stype, source.id,
    )
    return None


def _get_parser(source: SourceConfig, event_id: str) -> Any:
    """
    Devuelve la instancia del parser asignado segun ``parser_asignado``.

    Parsers concretos (producen entidades tipadas):
      encuentralos  -> EncuentralosParser -> list[Person]

    Si ``parser_asignado`` no tiene implementacion registrada se loguea un
    warning y se devuelve None; ``_run_source`` trata la ausencia de parser
    como fuente omitida (no se pierde el pipeline, solo esa fuente).

    ``event_id`` viene validado (UUID) desde ``run_pipeline`` y se inyecta
    en el parser para que lo propague a cada ``Person`` — los parsers no
    lo derivan ni lo conocen mas alla de propagarlo, igual que ``fuente``
    o ``trust_tier``.
    """
    pa = (source.parser_asignado or "").lower().strip()

    if pa == "encuentralos":
        from scrapers.parsers.encuentralos_parser import EncuentralosParser
        secret = os.getenv("PII_HMAC_SECRET")
        return EncuentralosParser(event_id=event_id, secret=secret)

    log.warning(
        "Parser %r no implementado (fuente=%s) — fuente omitida",
        source.parser_asignado, source.id,
    )
    return None


# ---------------------------------------------------------------------------
# Adapters ligeros para fuentes no-API
# ---------------------------------------------------------------------------

class _LocalFileAdapter:
    """Wrapper fino sobre read_local_file para fuentes manual_file."""

    def __init__(self, source_key: str, read_fn: Any) -> None:
        self.source_key = source_key
        self._read = read_fn

    def fetch(self, url: str, **_: Any) -> RawContent:
        text = self._read(url)
        return RawContent(
            source_key=self.source_key,
            source_url=url,
            fetched_at=now_utc(),
            http_status=200,
            content_type="text/plain",
            content_hash=sha256_hex(text.encode("utf-8")),
            raw_content=text,
            page=None,
            total_pages=None,
            offset=None,
            limit=None,
            records_in_page=None,
        )

    def fetch_all(self, url: str, **kwargs: Any) -> Iterator[RawContent]:
        yield self.fetch(url)


# ---------------------------------------------------------------------------
# Etapas del pipeline
# ---------------------------------------------------------------------------

def _fetch_pages(adapter: Any, source: SourceConfig, updated_after: str) -> list[RawContent]:
    """Llama a fetch_all del adapter y recopila todas las paginas.

    ``updated_after`` es el watermark actual de la fuente; se pasa como query
    param a todos los adapters (fetch_all acepta **kwargs en todos). Los que
    soportan filtrado server-side (ApiAdapter) lo incluyen en la request; el
    resto lo ignora silenciosamente.
    """
    url = source.url
    pages: list[RawContent] = []
    params = {"updated_after": updated_after}

    # ApiAdapter expone default_path separado de base_url
    if hasattr(adapter, "default_path") and adapter.default_path:
        path = adapter.default_path
        for page in adapter.fetch_all(path, params=params):
            pages.append(page)
    else:
        for page in adapter.fetch_all(url, params=params):
            pages.append(page)

    return pages


def _parse_pages(
    parser: Any,
    pages: list[RawContent],
    limit: int | None,
) -> tuple[list[ParsedEntity], list[str]]:
    """Parsea todas las paginas y devuelve (entidades, errores_por_registro)."""
    entities: list[ParsedEntity] = []
    errors: list[str] = []

    for raw in pages:
        try:
            batch = parser.parse(raw)
        except Exception as exc:
            msg = f"Error parseando pagina {raw.get('page')}: {exc}"
            log.warning(msg)
            errors.append(msg)
            continue

        entities.extend(batch)
        if limit is not None and len(entities) >= limit:
            entities = entities[:limit]
            break

    return entities, errors


# Campos PII crudos que nunca deben llegar a export aunque tokenize falle
_PII_FIELD_NAMES = {"cedula", "cédula", "identity_document", "documento_identidad",
                    "telefono", "teléfono", "phone", "mobile", "celular"}


def _strip_raw_pii(d: dict[str, Any]) -> dict[str, Any]:
    """Elimina campos PII crudos sin hashear (defensa en profundidad)."""
    return {k: v for k, v in d.items() if k.lower() not in _PII_FIELD_NAMES}


def _apply_pii(
    entities: list[ParsedEntity],
    errors: list[str],
) -> list[dict[str, Any]]:
    """
    Convierte entidades tipadas a dicts y aplica tokenize_pii_fields.

    Los campos cedula_hmac/cedula_masked ya vienen del parser concreto
    (p. ej. EncuentralosParser).  tokenize_pii_fields actua como segunda
    capa de seguridad: detecta y hashea cualquier campo PII que haya
    escapado del parser.

    Si PII_SALT no esta configurado (entorno CI/dev), el registro pasa
    igualmente — sin los campos PII crudos — en lugar de perderse.
    Esto permite que el pipeline funcione en tests offline.
    Devuelve lista de dicts listos para enriquecimiento + staging.
    """
    pii_salt_available = bool(os.getenv("PII_SALT"))

    result: list[dict[str, Any]] = []
    for entity in entities:
        try:
            d = entity.model_dump()
            if pii_salt_available:
                d = tokenize_pii_fields(d)
            else:
                # Sin salt: eliminar campos PII crudos sin tokenizar
                d = _strip_raw_pii(d)
            # Preservar el tipo para el router de export
            d["_entity_type"] = type(entity).__name__
            result.append(d)
        except Exception as exc:
            msg = f"Error en etapa PII para {type(entity).__name__}: {exc}"
            log.warning(msg)
            errors.append(msg)
            # Intentar rescatar el registro sin PII antes que perderlo
            try:
                d = _strip_raw_pii(entity.model_dump())
                d["_entity_type"] = type(entity).__name__
                result.append(d)
            except Exception:
                pass
    return result


def _enrich_records(
    records: list[dict[str, Any]],
    errors: list[str],
) -> list[dict[str, Any]]:
    """
    Normalizacion post-dump y computo de ``deterministic_id``.

    El exporter usa ``deterministic_id`` como external_id de Person, asi que
    este enriquecimiento debe correr antes de la etapa de staging. Normaliza
    ``last_known_location`` (string crudo) y ``date_iso`` cuando aplica; los
    parsers concretos ya normalizan al parsear, esta etapa cubre los huecos.
    """
    from scrapers.normalizers.person import normalize_person_name as _norm_name
    from scrapers.normalizers.phonetic import (
        build_deterministic_id as _build_det_id,
        phonetic_hash as _compute_phonetic,
    )

    enriched: list[dict[str, Any]] = []
    for rec in records:
        try:
            # Normalizar ubicacion si viene como string crudo sin objeto
            loc = rec.get("last_known_location")
            if isinstance(loc, str) and loc:
                loc_obj = normalize_location(loc)
                estado = loc_obj.get("estado")
                municipio = loc_obj.get("municipio")
                if municipio and estado:
                    rec["last_known_location"] = f"{municipio}, {estado}"
                elif estado:
                    rec["last_known_location"] = estado

            # Computar deterministic_id (external_id de Person)
            name_norm = _norm_name(rec.get("full_name") or "")
            ph = _compute_phonetic(name_norm) if name_norm else None
            rec["deterministic_id"] = _build_det_id(ph, rec.get("last_known_location"))

            # Normalizar date_iso para Event
            date_raw = rec.get("date_iso")
            if isinstance(date_raw, str) and date_raw:
                normalized_date = normalize_date(date_raw)
                if normalized_date and isinstance(normalized_date, str):
                    rec["date_iso"] = normalized_date

            enriched.append(rec)
        except Exception as exc:
            msg = f"Error en enriquecimiento: {exc}"
            log.warning(msg)
            errors.append(msg)
            enriched.append(rec)  # anadir sin enriquecer antes que perder
    return enriched


def _apply_confidence(
    records: list[dict[str, Any]],
    errors: list[str],
) -> list[dict[str, Any]]:
    """Calcula y escribe confidence_score en cada registro."""
    _MODEL_MAP = {"Person": Person, "AcopioCenter": AcopioCenter, "Event": Event}
    result: list[dict[str, Any]] = []
    for rec in records:
        try:
            entity_type = rec.get("_entity_type", "Person")
            model_cls = _MODEL_MAP.get(entity_type, Person)
            d2 = {k: v for k, v in rec.items() if not k.startswith("_")}
            entity = model_cls(**d2)
            score = confidence_score(entity)
            rec["confidence_score"] = score
        except Exception as exc:
            log.warning("Error calculando confidence_score: %s", exc)
            errors.append(f"Error en confidence_score: {exc}")
        result.append(rec)
    return result


def _apply_minor_protection(
    records: list[dict[str, Any]],
    errors: list[str],
) -> list[dict[str, Any]]:
    """Reduce campos identificables en registros con is_minor=True antes de exportar.

    No afecta a Event/AcopioCenter (no tienen is_minor) ni a Person con
    is_minor en None/False — ver scrapers/sanitizers/minor_protection.py.
    """
    result: list[dict[str, Any]] = []
    for rec in records:
        try:
            result.append(protect_minor_fields(rec))
        except Exception as exc:
            log.error("Error en proteccion de menores, registro omitido: %s", exc)
            errors.append(f"Error en proteccion de menores (registro omitido): {exc}")
            # Fail-closed: no se agrega el registro sin redactar.
    return result


# ---------------------------------------------------------------------------
# Orquestador por fuente
# ---------------------------------------------------------------------------

def _run_source(
    source: SourceConfig,
    limit: int | None,
    all_errors: list[str],
    event_id: str,
    exporter: StagingExporter,
) -> ExportResult:
    """
    Ejecuta el pipeline completo para una fuente.

    Devuelve un ``ExportResult`` agregado (sent, duplicates, errors). Los
    errores previos de la fuente (parse/PII/enriquecimiento) se arrastran en
    ``ExportResult.errors``. Cualquier excepcion no capturada sube al
    orquestador principal.
    """
    log.info("Iniciando fuente: %s (type=%s, parser=%s)", source.id, source.type, source.parser_asignado)
    source_errors: list[str] = []

    # 1. Adapter
    adapter = _get_adapter(source)
    if adapter is None:
        return ExportResult()

    # 2. Parser
    parser = _get_parser(source, event_id)
    if parser is None:
        # El adapter ya fue creado (puede tener browser/conexion abierta), asi
        # que se cierra antes de omitir la fuente para no filtrar recursos en
        # cada corrida (fuentes con parser_asignado no registrado).
        if hasattr(adapter, "close"):
            adapter.close()
        # La omision queda VISIBLE en el summary del run (no solo en un
        # log.warning silencioso): se contabiliza como error de fuente y
        # fluye a summary["errors"] via all_errors, igual que el resto de
        # errores no fatales de la fuente.
        msg = (
            f"parser no implementado: {source.parser_asignado} "
            f"(fuente {source.id} omitida)"
        )
        all_errors.append(f"[{source.id}] {msg}")
        return ExportResult(errors=[msg])

    # 3. Fetch
    # El watermark se lee ANTES del fetch para acotar la ventana
    # (updated_after); en la primera corrida de la fuente (sin watermark
    # previo) vale "1970-01-01T00:00:00Z" y provoca backfill completo.
    # get_watermark() va DENTRO del try/finally: aunque hace fail-open en
    # httpx.HTTPError, un fallo no contemplado (ej. JSON malformado) no debe
    # dejar el adapter sin cerrar (browser, conexiones) ni saltarse el close().
    try:
        watermark_at = exporter.get_watermark(source.id)
        pages = _fetch_pages(adapter, source, watermark_at)
    finally:
        if hasattr(adapter, "close"):
            try:
                adapter.close()
            except Exception:
                pass

    log.info("%s: %d pagina(s) descargadas", source.id, len(pages))

    fetched_ats = [str(p.get("fetched_at")) for p in pages if p.get("fetched_at")]

    # 4. Parse
    entities, parse_errors = _parse_pages(parser, pages, limit)
    source_errors.extend(parse_errors)
    log.info("%s: %d entidades parseadas", source.id, len(entities))

    if not entities:
        all_errors.extend([f"[{source.id}] {e}" for e in source_errors])
        return ExportResult(errors=list(source_errors))

    # 5. PII
    records = _apply_pii(entities, source_errors)

    # 6. Enriquecimiento (deterministic_id + normalizacion)
    records = _enrich_records(records, source_errors)

    # 7. Confidence score
    records = _apply_confidence(records, source_errors)

    # 8. Proteccion de menores (is_minor=True reduce campos identificables)
    records = _apply_minor_protection(records, source_errors)

    # 9. Staging export
    # source_errors se pasa para que el watermark NO avance si hubo errores
    # previos de la fuente (parse/PII/enriquecimiento/proteccion de menores).
    result = exporter.export_source(
        records,
        source_slug=source.id,
        source_fetched_ats=fetched_ats,
        source_errors=source_errors,
        max_concurrent_posts=source.max_concurrent_posts,
    )
    # Arrastrar los errores previos de la fuente al frente del resultado.
    result.errors[0:0] = source_errors

    all_errors.extend([f"[{source.id}] {e}" for e in result.errors])
    log.info(
        "%s: %d enviados, %d duplicados, %d errores",
        source.id, result.sent, result.duplicates, len(result.errors),
    )
    return result


def _process_source_safe(
    source: SourceConfig,
    limit: int | None,
    all_errors: list[str],
    event_id: str,
    exporter: StagingExporter,
) -> tuple[ExportResult, bool]:
    """Ejecuta ``_run_source`` capturando cualquier excepcion fatal de la fuente.

    Aisla el manejo de errores fatales (try/except con log + acumulado en
    ``all_errors``) para que tanto el modo secuencial como el paralelo
    (``ThreadPoolExecutor``) lo reusen igual. Devuelve ``(resultado, ok)``
    donde ``ok=False`` si la fuente entera fallo (no cuenta como
    ``sources_processed``).
    """
    try:
        result = _run_source(source, limit, all_errors, event_id, exporter)
        return result, True
    except Exception as exc:
        msg = f"[{source.id}] Error fatal en fuente: {exc}"
        log.error(msg, exc_info=True)
        all_errors.append(msg)
        return ExportResult(), False


# ---------------------------------------------------------------------------
# Punto de entrada publico
# ---------------------------------------------------------------------------

def run_pipeline(
    config_path: Path,
    output_dir: Path,
    limit: int | None = None,
    max_workers: int = 1,
) -> dict[str, Any]:
    """
    Orquestador principal del pipeline.

    Parameters
    ----------
    config_path:
        Ruta al YAML de configuracion de fuentes.
    output_dir:
        Reservado para artefactos/logs. El export a JSONL desaparecio; el
        destino ahora es la tabla aportes via /api/aportes. Se conserva en la
        firma por compatibilidad con la CLI.
    limit:
        Numero maximo de entidades por fuente (None = sin limite).
    max_workers:
        Fuentes procesadas en paralelo (default 1 = secuencial, igual que
        antes). Cada fuente es I/O-bound (fetch + POST a staging), asi que
        threads alcanzan sin GIL real: no hace falta multiprocessing. El
        ``StagingExporter`` (un solo httpx.Client) es seguro de compartir
        entre threads. Watermarks y errores se acumulan por fuente
        (``source.id`` unico), asi que no hay estado compartido mutable entre
        fuentes mas alla de los acumuladores de ``run_pipeline``, que se
        suman despues de que cada future termina.

    Returns
    -------
    dict con keys: sources_processed, staging_sent, staging_duplicates,
                   staging_errors, errors.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info("Pipeline iniciado — config=%s output=%s limit=%s", config_path, output_dir, limit)

    # Cargar fuentes
    try:
        project, sources = load_sources(config_path)
    except Exception as exc:
        log.error("Error cargando config: %s", exc)
        return _error_summary(f"Error cargando config: {exc}")

    # event_id es obligatorio en cada Person/AcopioCenter exportado (FK NOT NULL
    # en la DB) — se valida una sola vez aqui en vez de dejar que cada registro
    # falle su propia validacion y generar ruido masivo en los logs.
    raw_event_id = project.get("event_id")
    try:
        event_id = validate_uuid_str(str(raw_event_id))
    except ValueError:
        msg = f"project.event_id invalido o ausente en config: {raw_event_id!r}"
        log.error(msg)
        return _error_summary(msg)

    enabled = [s for s in sources if s.enabled]
    log.info("%d fuentes habilitadas de %d totales", len(enabled), len(sources))

    staging_sent = 0
    staging_duplicates = 0
    staging_errors = 0
    sources_processed = 0
    all_errors: list[str] = []

    # Un solo exporter (un httpx.Client) y un solo run_id por corrida.
    exporter = StagingExporter(StagingConfig.from_env(), run_id=str(uuid.uuid4()))
    try:
        if max_workers <= 1 or len(enabled) <= 1:
            for source in enabled:
                result, ok = _process_source_safe(source, limit, all_errors, event_id, exporter)
                staging_sent += result.sent
                staging_duplicates += result.duplicates
                staging_errors += len(result.errors)
                sources_processed += int(ok)
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = [
                    pool.submit(_process_source_safe, source, limit, all_errors, event_id, exporter)
                    for source in enabled
                ]
                for future in as_completed(futures):
                    result, ok = future.result()
                    staging_sent += result.sent
                    staging_duplicates += result.duplicates
                    staging_errors += len(result.errors)
                    sources_processed += int(ok)
    finally:
        exporter.close()

    summary = {
        "sources_processed": sources_processed,
        "staging_sent": staging_sent,
        "staging_duplicates": staging_duplicates,
        "staging_errors": staging_errors,
        "errors": all_errors,
    }

    log.info(
        "Pipeline finalizado — fuentes=%d enviados=%d duplicados=%d errores=%d",
        sources_processed, staging_sent, staging_duplicates, len(all_errors),
    )
    return summary
