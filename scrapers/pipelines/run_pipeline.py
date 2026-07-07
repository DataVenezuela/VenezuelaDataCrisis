"""
scrapers/pipelines/run_pipeline.py
====================================
Orquestador principal del pipeline VZLA_DEDUP.

Flujo por fuente habilitada
----------------------------
0. **Bronze**    — ``ProvenanceExporter`` abre una ``scrape_run`` por fuente y
                   registra cada pagina fetcheada en ``raw_artifacts`` (append-only);
                   cada aporte lleva su ``artifact_id`` (issue #256)
1. **Adapter**   — fetch del contenido raw segun ``source.type``
2. **Parser**    — convierte raw -> list[Person | AcopioCenter | Event]
3. **PII**       — ``tokenize_pii_fields`` sobre cada entidad (model_dump)
4. **Enriquecimiento** — normaliza last_known_location/date_iso y calcula
                   ``deterministic_id`` (el exporter lo usa como external_id
                   de Person)
5. **Score**     — ``confidence_score`` sobre cada entidad
6. **Minor protection** — ``protect_minor_fields`` reduce campos identificables
                   cuando is_minor=True (foto, cedula_masked, ubicacion exacta)
7. **Staging**   — ``StagingExporter`` hace upsert directo a Supabase (PostgREST)

Cuarentena (Issue #88)
----------------------
NINGUN registro se descarta en silencio. Cada punto donde el pipeline antes
perdia datos (parser ausente, parseo fallido, PII no tratable, fail-closed de
proteccion de menores) ahora enruta el registro a la Quarantine DB via
``QuarantineExporter`` (POST /api/quarantine) para revision humana. El preview
va redactado; el run continua con las demas fuentes.

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
  quarantined         int  — registros enviados a la Quarantine DB
  quarantine_errors   int  — errores al enviar a cuarentena
  errors              list[str]  — mensajes de error no fatales
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from scrapers import __version__ as _PIPELINE_VERSION
from scrapers.adapters._shared import now_utc, sha256_hex
from scrapers.adapters.base import RawContent
from scrapers.exporters.quarantine_exporter import (
    QuarantineConfig,
    QuarantineExporter,
    QuarantineRecord,
    quarantine_payload_hash,
)
from scrapers.exporters.provenance_exporter import ProvenanceExporter
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
from scrapers.sanitizers.pii_detector import detect_pii
from scrapers.sanitizers.pii_redactor import redact_pii
from scrapers.sanitizers.pii_tokenizer import tokenize_pii_fields
from scrapers.sources.loader import load_sources
from scrapers.validators.quality import confidence_score

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tipos internos
# ---------------------------------------------------------------------------

ParsedEntity = Person | AcopioCenter | Event


def _error_summary(message: str) -> dict[str, Any]:
    """Summary de salida temprana con las keys nuevas de staging y cuarentena."""
    return {
        "sources_processed": 0,
        "sources_failed_closed": 0,
        "staging_sent": 0,
        "staging_duplicates": 0,
        "staging_errors": 0,
        "quarantined": 0,
        "quarantine_errors": 0,
        "errors": [message],
    }


# ---------------------------------------------------------------------------
# Cuarentena: construccion de registros no procesables
# ---------------------------------------------------------------------------
# Principio inamovible (Issue #88): NINGUN registro se descarta en silencio. En
# una crisis donde cada registro puede ser una vida, todo lo que hoy se perderia
# (parser ausente, error de parseo, PII no redactable, fail-closed de menores)
# va a la Quarantine DB para que pase por ojo humano. El preview se manda SIEMPRE
# redactado; pii_findings_summary lleva conteos por tipo, nunca valores en claro.

def _to_text(value: object) -> str:
    """Coacciona el contenido crudo (str | dict | list) a texto para preview/hash."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _pii_summary(text: str) -> dict[str, object] | None:
    """Resumen de hallazgos PII como conteos por tipo. Sin valores en claro.

    Devuelve None si no hay hallazgos (la columna jsonb queda null).
    """
    summary: dict[str, int] = {}
    for finding in detect_pii(text):
        kind = str(finding.get("kind", "unknown"))
        summary[kind] = summary.get(kind, 0) + 1
    return dict(summary) if summary else None


def _quarantine_from_text(
    *,
    source: SourceConfig,
    text: str,
    reason_code: str,
    risk_level: str,
    detail: str,
    source_url: str | None = None,
) -> QuarantineRecord:
    """Arma un QuarantineRecord desde contenido crudo o serializado.

    ``payload_hash`` se calcula sobre el texto ORIGINAL (permite verificar el
    payload exacto aun tras destruirlo). ``payload_preview_redacted`` se redacta
    con ``redact_pii`` para no filtrar PII en claro al revisor.
    """
    return QuarantineRecord(
        source_slug=source.id,
        reason_code=reason_code,
        risk_level=risk_level,
        source_url=source_url or source.url,
        reason_detail=detail,
        payload_preview_redacted=redact_pii(text),
        payload_hash=quarantine_payload_hash(text),
        pii_findings_summary=_pii_summary(text),
    )



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
      x_recent_search -> XSearchAdapter (API oficial de X Recent Search)

    Tipos sin adapter registrado devuelven None y la fuente se omite.
    """
    stype = source.type

    if stype == "api_json":
        from scrapers.adapters._shared import RateLimiter
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
        if source.probe_limit is not None:
            adapter_kwargs["probe_limit"] = source.probe_limit
        # ApiAdapter es el unico que hace multiples requests por corrida
        # (paginacion), asi que es el unico que aplica rate_limit_per_minute.
        if source.rate_limit_per_minute is not None:
            adapter_kwargs["rate_limiter"] = RateLimiter(source.rate_limit_per_minute)
        adapter = ApiAdapter(**adapter_kwargs)
        return adapter

    if stype == "x_recent_search":
        from scrapers.adapters.x_search_adapter import XSearchAdapter

        query = _build_x_query(source.required_keywords)
        return XSearchAdapter(
            query=query,
            source_key=source.id,
        )

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
      demo_text     -> DemoTextParser -> list[Person] (fixture sintetico local)
      x_posts       -> XPostParser -> list[Person]

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

    if pa == "demo_text":
        from scrapers.parsers.demo_text_parser import DemoTextParser
        return DemoTextParser(event_id=event_id)

    if pa == "x_posts":
        from scrapers.parsers.x_post_parser import XPostParser

        return XPostParser(
            event_id=event_id,
            source_key=source.id,
            trust_tier=source.trust_tier,
        )

    log.warning(
        "Parser %r no implementado (fuente=%s) — fuente omitida",
        source.parser_asignado, source.id,
    )
    return None


def _build_x_query(required_keywords: list[str]) -> str:
    keywords = [keyword.strip() for keyword in required_keywords if keyword.strip()]
    if not keywords:
        return "Venezuela terremoto"
    parts = [f'"{keyword}"' if " " in keyword else keyword for keyword in keywords]
    return " OR ".join(parts)


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

    def fetch_all(self, url: str, **kwargs: Any):  # type: ignore[return]
        yield self.fetch(url)


# ---------------------------------------------------------------------------
# Etapas del pipeline
# ---------------------------------------------------------------------------

def _strip_ms(ts: str) -> str:
    """'2026-07-03T12:45:07.153Z' → '2026-07-03T12:45:07Z' (formato watermark sin ms)."""
    dot = ts.find(".")
    return ts[:dot] + "Z" if dot != -1 else ts



def _fetch_pages(adapter: Any, source: SourceConfig, updated_after: str):
    """Genera las páginas del adapter una a una (streaming).

    ``updated_after`` es el watermark actual de la fuente; se pasa como query
    param a todos los adapters (fetch_all acepta **kwargs en todos). Los que
    soportan filtrado server-side (ApiAdapter) lo incluyen en la request; el
    resto lo ignora silenciosamente.

    Si ``source.full_scan`` es True, se omite ``updated_after`` — la API
    upstream no lo soporta y devolvería el dataset completo de todas formas.
    El dedup queda delegado al upsert por external_id en staging.

    Si ``source.cursor_field`` está definido, hace early-stop en cuanto
    ``min(cursor_field de la página)`` ≤ ``updated_after``: todos los registros
    siguientes son anteriores al watermark y ya están en staging.
    """
    url = source.url
    if source.full_scan:
        log.info("%s: full_scan=True — omitiendo updated_after en el fetch", source.id)
        params: dict[str, str] = {}
    else:
        params = {"updated_after": updated_after}

    path = adapter.default_path if (hasattr(adapter, "default_path") and adapter.default_path) else url
    for page in adapter.fetch_all(path, params=params):
        yield page

        if source.cursor_field:
            items = (page.get("raw_content") or {}).get("items") or []
            if items:
                cursors = [
                    _strip_ms(str(item[source.cursor_field]))
                    for item in items
                    if item.get(source.cursor_field)
                ]
                if cursors and min(cursors) <= updated_after:
                    log.info(
                        "%s: early-stop cursor — min(%s)=%s ≤ watermark=%s (página %s)",
                        source.id, source.cursor_field, min(cursors), updated_after, page.get("page"),
                    )
                    break


def _parse_pages(
    parser: Any,
    pages: list[RawContent],
    limit: int | None,
    source: SourceConfig,
    quarantine_batch: list[QuarantineRecord],
) -> tuple[list[ParsedEntity], list[str]]:
    """Parsea todas las paginas y devuelve (entidades, errores_por_registro).

    Una pagina que falla al parsear NO se descarta: su contenido crudo va a
    cuarentena (``invalid_schema``) para revision humana.
    """
    entities: list[ParsedEntity] = []
    errors: list[str] = []

    for raw in pages:
        try:
            batch = parser.parse(raw)
        except Exception as exc:
            msg = f"Error parseando pagina {raw.get('page')}: {exc}"
            log.warning(msg)
            errors.append(msg)
            quarantine_batch.append(
                _quarantine_from_text(
                    source=source,
                    text=_to_text(raw.get("raw_content")),
                    reason_code="invalid_schema",
                    risk_level="medium",
                    detail=msg,
                    source_url=raw.get("source_url"),
                )
            )
            continue

        entities.extend(batch)
        if limit is not None and len(entities) >= limit:
            entities = entities[:limit]
            break

    return entities, errors


# Campos PII crudos que nunca deben llegar a export aunque tokenize falle
_PII_FIELD_NAMES = {"cedula", "cédula", "identity_document", "documento_identidad",
                    "telefono", "teléfono", "phone", "mobile", "celular"}


def _strip_raw_pii(d: dict) -> dict:
    """Elimina campos PII crudos sin hashear (defensa en profundidad)."""
    return {k: v for k, v in d.items() if k.lower() not in _PII_FIELD_NAMES}


def _apply_pii(
    entities: list[ParsedEntity],
    errors: list[str],
    source: SourceConfig,
    quarantine_batch: list[QuarantineRecord],
) -> list[dict]:
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

    Nota (#256): la URL de origen ya NO se propaga como meta-campo ``_source_url``.
    Vive en ``raw_artifacts.source_url`` (Bronze), que el pipeline registra por
    pagina; el exporter dejo de emitir ``source_url``/``parser_version`` en el
    aporte, asi que ese cableado quedo obsoleto.
    """
    pii_salt_available = bool(os.getenv("PII_SALT"))

    result: list[dict] = []
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
            # _source_record_id (prefijo _) es el meta-campo que _build_payload
            # lee para basar external_id en el UUID nativo; clean lo excluye de raw_json.
            if d.get("source_record_id"):
                d["_source_record_id"] = d["source_record_id"]
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
            except Exception as rescue_exc:
                # No se pudo ni rescatar sin PII: a cuarentena (PII no tratable),
                # riesgo alto. NUNCA se descarta en silencio (Issue #88).
                try:
                    preview_text = _to_text(entity.model_dump())
                except Exception:
                    preview_text = repr(entity)
                quarantine_batch.append(
                    _quarantine_from_text(
                        source=source,
                        text=preview_text,
                        reason_code="pii_untreatable",
                        risk_level="high",
                        detail=(
                            f"PII no tratable para {type(entity).__name__}: "
                            f"{exc} (rescate fallo: {rescue_exc})"
                        ),
                    )
                )
    return result


def _enrich_records(records: list[dict], errors: list[str]) -> list[dict]:
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

    enriched: list[dict] = []
    for rec in records:
        try:
            # Trazabilidad (issue #236): version del normalizador que corrio.
            # Se setea primero, antes de cualquier paso que pueda lanzar (p. ej.
            # normalize_location): si algo falla, el registro cae al except y se
            # agrega a enriched, pero debe conservar el meta-campo para que el
            # exporter no lo exporte con normalizer_version null.
            rec["_normalizer_version"] = _PIPELINE_VERSION

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
    records: list[dict],
    errors: list[str],
) -> list[dict]:
    """Calcula y escribe confidence_score en cada registro."""
    _MODEL_MAP = {"Person": Person, "AcopioCenter": AcopioCenter, "Event": Event}
    result: list[dict] = []
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
    records: list[dict],
    errors: list[str],
    source: SourceConfig,
    quarantine_batch: list[QuarantineRecord],
) -> list[dict]:
    """Reduce campos identificables en registros con is_minor=True antes de exportar.

    No afecta a Event/AcopioCenter (no tienen is_minor) ni a Person con
    is_minor en None/False — ver scrapers/sanitizers/minor_protection.py.
    """
    result: list[dict] = []
    for rec in records:
        try:
            result.append(protect_minor_fields(rec))
        except Exception as exc:
            log.error("Error en proteccion de menores: %s", exc)
            errors.append(f"Error en proteccion de menores (registro omitido): {exc}")
            # Fail-closed para STAGING: el registro sin redactar NO se exporta.
            # Pero NO se descarta: va a cuarentena con riesgo alto para redaccion
            # manual (review_status 'needs_manual_redaction' en el backend). El
            # preview va redactado igual, defensa en profundidad (Issue #88).
            quarantine_batch.append(
                _quarantine_from_text(
                    source=source,
                    text=_to_text(rec),
                    reason_code="pii_untreatable",
                    risk_level="high",
                    detail=(
                        "proteccion de menores fallo; registro preservado para "
                        f"redaccion manual: {exc}"
                    ),
                )
            )
    return result


# ---------------------------------------------------------------------------
# Domain allowlist
# ---------------------------------------------------------------------------

def _check_allowed_domain(source: SourceConfig) -> str | None:
    """Valida el host de ``source.url`` contra ``source.allowed_domains``.

    Match **exacto, case-insensitive** (sin subdominios — decision del equipo,
    issue #132). Devuelve ``None`` si pasa (o si no hay allowlist configurada,
    caso retrocompatible), o un mensaje de error si el host no esta permitido.
    """
    if not source.allowed_domains:
        return None
    host = (urlparse(source.url).hostname or "").lower()
    allowed = {domain.strip().lower() for domain in source.allowed_domains}
    if host not in allowed:
        return (
            f"dominio no permitido: {host!r} no esta en allowed_domains "
            f"{sorted(allowed)} (fuente {source.id} omitida sin fetchear)"
        )
    return None


# ---------------------------------------------------------------------------
# Orquestador por fuente
# ---------------------------------------------------------------------------

def _run_source(
    source: SourceConfig,
    limit: int | None,
    all_errors: list[str],
    event_id: str,
    exporter: StagingExporter,
    quarantine_batch: list[QuarantineRecord],
    provenance: ProvenanceExporter,
) -> ExportResult:
    """
    Ejecuta el pipeline completo para una fuente.

    Devuelve un ``ExportResult`` agregado (sent, duplicates, errors). Los
    errores previos de la fuente (parse/PII/enriquecimiento) se arrastran en
    ``ExportResult.errors``. Cualquier excepcion no capturada sube al
    orquestador principal.

    ``quarantine_batch`` acumula los registros no procesables de esta fuente
    (parser ausente, parseo fallido, PII no tratable, menor no redactable). El
    orquestador lo vacia a la Quarantine DB al terminar la fuente — NADA se
    descarta en silencio (Issue #88).

    ``provenance`` escribe la capa Bronze (issue #256): una fila ``scrape_runs``
    por esta fuente y una fila ``raw_artifacts`` APPEND-ONLY por pagina fetcheada.
    Cada aporte lleva su ``artifact_id`` (meta-campo ``_artifact_id``). Si no se
    puede crear la corrida o registrar la pagina (modo enabled), la fuente falla
    CERRADO: no se exportan aportes sin su ``raw_artifact`` y el error se acumula
    en ``source_errors`` para que el watermark NO avance (re-fetch la proxima
    corrida, semantica at-least-once). Esto es deliberado: un timestamp de
    watermark no puede expresar "todas las paginas menos la 1 estan hechas", asi
    que ante un fallo de procedencia se retiene la fuente entera.
    """
    log.info("Iniciando fuente: %s (type=%s, parser=%s)", source.id, source.type, source.parser_asignado)
    source_errors: list[str] = []

    # 0. Domain allowlist — antes de construir el adapter, para no abrir ninguna
    # conexion ni hacer ningun request a un dominio no auditado. La omision queda
    # visible en summary["errors"], mismo patron que "parser no implementado".
    domain_error = _check_allowed_domain(source)
    if domain_error is not None:
        log.warning("[%s] %s", source.id, domain_error)
        all_errors.append(f"[{source.id}] {domain_error}")
        return ExportResult(errors=[domain_error])

    # 1. Adapter
    adapter = _get_adapter(source)
    if adapter is None:
        # Tipo de fuente sin adapter implementado: NO hay payload que cuarentenar
        # (nunca se hizo fetch), pero la omision NO debe ser silenciosa — queda
        # VISIBLE en summary["errors"] para que el operador sepa que esa fuente
        # entera no se proceso (Issue #88, principio "nada invisible").
        msg = (
            f"adapter no implementado para type={source.type!r} "
            f"(fuente {source.id} omitida — sin payload que cuarentenar)"
        )
        all_errors.append(f"[{source.id}] {msg}")
        return ExportResult(errors=[msg])

    # 2. Parser
    parser = _get_parser(source, event_id)
    if parser is None:
        # Sin parser NO se descarta la fuente: se baja el contenido crudo y se
        # manda a cuarentena (parser_unavailable) para que un humano lo revise y
        # se pueda escribir el parser. El fetch puede ser costoso (Playwright),
        # pero perder registros en una crisis no es aceptable (Issue #88).
        msg = (
            f"parser no implementado: {source.parser_asignado} "
            f"(fuente {source.id} — registros a cuarentena)"
        )
        all_errors.append(f"[{source.id}] {msg}")
        try:
            watermark_at = exporter.get_watermark(source.id)
            for raw in _fetch_pages(adapter, source, watermark_at):
                quarantine_batch.append(
                    _quarantine_from_text(
                        source=source,
                        text=_to_text(raw.get("raw_content")),
                        reason_code="parser_unavailable",
                        risk_level="medium",
                        detail=msg,
                        source_url=raw.get("source_url"),
                    )
                )
        except Exception as exc:
            all_errors.append(
                f"[{source.id}] fetch fallo al intentar cuarentenar sin parser: {exc}"
            )
        finally:
            if hasattr(adapter, "close"):
                try:
                    adapter.close()
                except Exception as exc:
                    log.warning("adapter.close() fallo: %s", exc)
        return ExportResult(errors=[msg])

    # 3-9. Fetch → parse → PII → enriquecimiento → score → menores → export por página
    # (streaming: el adapter genera páginas una a una; cada página se exporta
    # inmediatamente en lugar de acumular todo en memoria antes del primer POST)

    combined = ExportResult()
    source_fetched_ats: list[str] = []
    pages_seen = 0
    entities_seen = 0
    artifacts_written = 0
    run_id: str | None = None
    # True si la fuente no exporto por un fallo de la capa Bronze (start_run o
    # record_artifact): se propaga en el ExportResult para contarla aparte de las
    # fuentes procesadas-ok y no enmascarar un apagon de Bronze (#256).
    bronze_failed = False

    try:
        watermark_at = exporter.get_watermark(source.id)
        # Bronze (issue #256): abrir la corrida de esta fuente antes de fetchear.
        # Si no se puede crear (modo enabled), fail-closed: no exportar aportes sin
        # su raw_artifact. Se marca error de fuente para bloquear el watermark.
        run_id = provenance.start_run(source.id)
        if run_id is None:
            msg = (
                f"no se pudo iniciar scrape_run para {source.id}; se omite el "
                "export de la fuente (fail-closed, watermark retenido)"
            )
            log.error("[%s] %s", source.id, msg)
            source_errors.append(msg)
            bronze_failed = True

        for page in _fetch_pages(adapter, source, watermark_at) if run_id is not None else ():
            pages_seen += 1

            # Registrar la pagina cruda en Bronze (append-only) ANTES de parsear:
            # una fila raw_artifacts por pagina fetcheada, sea cual sea el numero
            # de entidades. artifact_id viaja luego en cada aporte de esta pagina.
            artifact_id = provenance.record_artifact(run_id, page)
            if artifact_id is None:
                # Sin raw_artifact no puede existir el aporte (FK NOT NULL). No se
                # exportan los aportes de esta pagina; se marca error de fuente
                # para retener el watermark y re-fetchear la proxima corrida.
                msg = f"no se pudo registrar raw_artifact (pagina {page.get('page')})"
                log.warning("[%s] %s", source.id, msg)
                source_errors.append(msg)
                bronze_failed = True
                continue
            artifacts_written += 1

            # El basis del watermark solo cuenta paginas efectivamente registradas
            # en Bronze: el timestamp de una pagina cuyo artifact fallo NUNCA entra
            # (defensa en profundidad sobre el gate de source_errors, #256).
            if source.cursor_field:
                page_cursors = [
                    _strip_ms(str(item[source.cursor_field]))
                    for item in ((page.get("raw_content") or {}).get("items") or [])
                    if item.get(source.cursor_field)
                ]
                if page_cursors:
                    source_fetched_ats.append(max(page_cursors))
            elif page.get("fetched_at"):
                source_fetched_ats.append(str(page.get("fetched_at")))

            page_entities, page_errors = _parse_pages(parser, [page], None, source, quarantine_batch)
            source_errors.extend(page_errors)

            if limit is not None:
                page_entities = page_entities[: limit - entities_seen]

            entities_seen += len(page_entities)

            if page_entities:
                records = _apply_pii(
                    page_entities, source_errors, source, quarantine_batch,
                )
                records = _enrich_records(records, source_errors)
                records = _apply_confidence(records, source_errors)
                records = _apply_minor_protection(records, source_errors, source, quarantine_batch)

                # Stampar el artifact_id de esta pagina en cada aporte antes de
                # exportar: el exporter lo emite como aportes.artifact_id (#256).
                for rec in records:
                    rec["_artifact_id"] = artifact_id

                batch_result = exporter.export_batch(
                    records, source_slug=source.id, batch_size=source.bulk_size,
                    max_concurrent_posts=source.max_concurrent_posts,
                )
                combined.sent += batch_result.sent
                combined.errors.extend(batch_result.errors)

            if limit is not None and entities_seen >= limit:
                break

    finally:
        if hasattr(adapter, "close"):
            try:
                adapter.close()
            except Exception as exc:
                log.warning("adapter.close() fallo en %s: %s", source.id, exc)
        # Cerrar la corrida Bronze: finished_at + stats (SOLO conteos numericos,
        # nunca strings de error que podrian contener texto derivado de PII).
        provenance.finish_run(
            run_id,
            {
                "pages": pages_seen,
                "artifacts": artifacts_written,
                "entities": entities_seen,
                "sent": combined.sent,
            },
        )

    log.info("%s: %d página(s), %d entidades", source.id, pages_seen, entities_seen)
    if entities_seen == 0 and pages_seen > 0:
        log.warning("%s: no se parsearon entidades de %d página(s)", source.id, pages_seen)

    if entities_seen == 0:
        all_errors.extend([f"[{source.id}] {e}" for e in source_errors])
        return ExportResult(errors=list(source_errors), failed_closed=bronze_failed)

    # Watermark una sola vez al final: avanza si sent>0 y sin errores pre-export
    wm_err = exporter.advance_watermark(
        source.id, source_fetched_ats, bool(source_errors), combined.sent
    )
    if wm_err is not None:
        combined.errors.append(wm_err)

    combined.errors[0:0] = source_errors
    all_errors.extend([f"[{source.id}] {e}" for e in combined.errors])
    log.info(
        "%s: %d enviados, %d duplicados, %d errores",
        source.id, combined.sent, combined.duplicates, len(combined.errors),
    )
    for err in combined.errors:
        log.warning("[%s] %s", source.id, err)
    combined.failed_closed = bronze_failed
    return combined


def _process_source_safe(
    source: SourceConfig,
    limit: int | None,
    all_errors: list[str],
    event_id: str,
    exporter: StagingExporter,
    provenance: ProvenanceExporter,
) -> tuple[ExportResult,list[QuarantineRecord], bool]:
    """Ejecuta ``_run_source`` capturando cualquier excepcion fatal de la fuente.

    Aisla el manejo de errores fatales (try/except con log + acumulado en
    ``all_errors``) para que tanto el modo secuencial como el paralelo
    (``ThreadPoolExecutor``) lo reusen igual. Devuelve ``(resultado, ok)``
    donde ``ok=False`` si la fuente entera fallo (no cuenta como
    ``sources_processed``).
    """

    quarantine_batch: list[QuarantineRecord] = []
    try:
        result = _run_source(
            source, limit, all_errors, event_id, exporter, quarantine_batch, provenance
        )
        return result,quarantine_batch, True
    except Exception as exc:
        msg = f"[{source.id}] Error fatal en fuente: {exc}"
        log.error(msg, exc_info=True)
        all_errors.append(msg)
        return ExportResult(), quarantine_batch, False
        


# ---------------------------------------------------------------------------
# Punto de entrada publico
# ---------------------------------------------------------------------------

def run_pipeline(
    config_path: Path,
    output_dir: Path,
    limit: int | None = None,
    max_workers: int = 1,
) -> dict:
    """
    Orquestador principal del pipeline.

    Parameters
    ----------
    config_path:
        Ruta al YAML de configuracion de fuentes.
    output_dir:
        Reservado para artefactos/logs. El export a JSONL desaparecio; el
        destino ahora es la tabla aportes via PostgREST directo
        (/rest/v1/aportes, StagingExporter, no /api/aportes de Vercel,
        deprecado para ingest desde #200/#203). Se conserva en la firma por
        compatibilidad con la CLI.
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
                   staging_errors, quarantined, quarantine_errors, errors.
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
    quarantined = 0
    quarantine_errors = 0
    sources_processed = 0
    sources_failed_closed = 0
    quarantine_batch: list[QuarantineRecord] = []
    all_errors: list[str] = []

    # Un solo run_id por corrida, COMPARTIDO entre staging y cuarentena: permite
    # correlacionar que se exporto y que se cuarentenó en la misma corrida (#88).
    run_id = str(uuid.uuid4())
    # Dos exporters HTTP independientes (cada uno con su httpx.Client y sus env
    # vars). Ambos hacen dry-run silencioso si faltan sus credenciales.
    exporter = StagingExporter(StagingConfig.from_env(), run_id=run_id)
    quarantine_exporter = QuarantineExporter(QuarantineConfig.from_env(), run_id=run_id)
    # Bronze (issue #256): escribe scrape_runs + raw_artifacts. Reusa las mismas
    # SUPABASE_* que el staging exporter, asi que ambos entran/salen de dry-run
    # juntos. El run_id de scrape_runs lo genera la DB por fuente (no este uuid).
    provenance = ProvenanceExporter(StagingConfig.from_env())

    try:
        if max_workers <= 1 or len(enabled) <= 1:
        
            for source in enabled:
                result,thread_quarantine_batch, ok = _process_source_safe(source, limit, all_errors, event_id, exporter, provenance)
                thread_quarantine_batch = thread_quarantine_batch or []
                staging_sent += result.sent
                staging_duplicates += result.duplicates
                staging_errors += len(result.errors)
                sources_processed += int(ok)
                sources_failed_closed += int(result.failed_closed)
                quarantine_batch.extend(thread_quarantine_batch)
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = [
                    pool.submit(_process_source_safe, source, limit, all_errors, event_id, exporter, provenance)
                    for source in enabled
                ]
                for future in as_completed(futures):
                    result,thread_quarantine_batch, ok = future.result()
                    thread_quarantine_batch = thread_quarantine_batch or []
                    staging_sent += result.sent
                    staging_duplicates += result.duplicates
                    staging_errors += len(result.errors)
                    sources_processed += int(ok)
                    sources_failed_closed += int(result.failed_closed)
                    quarantine_batch.extend(thread_quarantine_batch)


    finally:
        if quarantine_batch:
            qres = quarantine_exporter.quarantine_many(quarantine_batch)
            quarantined += qres.sent
            quarantine_errors += len(qres.errors)
            all_errors.extend(
                f"cuarentena: {e}" for e in qres.errors
            )
            # Slugs reales del batch — no usar `source` del loop (queda en la
            # ultima fuente iterada; en max_workers>1 ni existe en este scope).
            source_slugs = sorted({r.source_slug for r in quarantine_batch})
            log.info(
                "cuarentena [%s] — total: %d enviados (%d duplicados, %d errores)",
                ", ".join(source_slugs),
                qres.sent,
                qres.duplicates,
                len(qres.errors),
            )

        exporter.close()
        quarantine_exporter.close()
        provenance.close()

    summary = {
        "sources_processed": sources_processed,
        "sources_failed_closed": sources_failed_closed,
        "staging_sent": staging_sent,
        "staging_duplicates": staging_duplicates,
        "staging_errors": staging_errors,
        "quarantined": quarantined,
        "quarantine_errors": quarantine_errors,
        "errors": all_errors,
    }

    log.info(
        "Pipeline finalizado — fuentes=%d enviados=%d duplicados=%d cuarentena=%d errores=%d",
        sources_processed, staging_sent, staging_duplicates, quarantined, len(all_errors),
    )
    return summary
