from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SourceConfig:
    id: str
    name: str
    type: str
    enabled: bool
    trust_tier: str
    url: str
    refresh_minutes: int
    parser_asignado: str = "auto"
    required_keywords: list[str] = field(default_factory=list)
    notes: str | None = None
    timeout_seconds: float | None = None
    max_retries: int | None = None
    page_size: int | None = None
    probe_limit: int | None = None
    max_concurrent_pages: int | None = None
    max_concurrent_posts: int | None = None
    # Allowlist de hosts exactos para `url` (match exacto, case-insensitive).
    # None/ausente = sin restriccion (retrocompatible). Ver run_pipeline._run_source.
    allowed_domains: list[str] | None = None
    # Tope de requests por ventana de 60s. Solo lo aplica ApiAdapter (paginacion);
    # None/ausente = sin limite. Ver scrapers/adapters/_shared.RateLimiter.
    rate_limit_per_minute: int | None = None
    # Cuántos aportes enviar por request a /api/aportes/bulk. Si está seteado,
    # export_source_bulk() reemplaza el loop individual de export_source().
    # None/ausente = comportamiento original (un POST por registro).
    bulk_size: int | None = None
    # Cuando es True, el pipeline omite el parámetro updated_after en el fetch
    # porque la API upstream lo ignora o no lo soporta. El pipeline baja el
    # dataset completo en cada run y delega el dedup al upsert por external_id.
    full_scan: bool = False
    # Campo del API (e.g. "creado") cuyo valor ISO 8601 se usa como cursor de
    # paginación incremental. Cuando está seteado, _fetch_pages hace early-stop
    # en cuanto min(cursor_field de la página) ≤ watermark_at, y avanza el
    # watermark con max(cursor_field) en vez de fetched_at.
    cursor_field: str | None = None

    @property
    def parser(self) -> str:
        """Backward-compatible alias for older code/config wording."""
        return self.parser_asignado
