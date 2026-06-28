"""
scrapers/adapters/playwright_adapter.py
========================================
Adapter para fuentes de tipo ``webapp_js``: paginas que dependen de JavaScript
y no devuelven contenido util con un GET HTTP simple.

Usa Playwright (sync API) para renderizar la pagina en un browser headless y
devuelve el HTML resultante como ``RawContent``, igual que cualquier otro
adapter — el resto del pipeline (parser -> PII -> staging) no cambia.

Contrato de salida
------------------
``fetch``/``fetch_all`` producen un ``RawContent`` con los campos estandar
del pipeline (ver ``base.py``); ``raw_content`` es el HTML (``str``) ya
renderizado por el browser.

Testabilidad
------------
El browser real solo se lanza si no se inyecta ``page_factory``. Los tests
inyectan un ``page_factory`` falso (objeto con ``goto``/``content``/``url``/
``close``) para correr 100% offline, sin abrir un browser real ni hacer
llamadas de red.

Uso basico
----------
::

    from scrapers.adapters.playwright_adapter import PlaywrightAdapter

    with PlaywrightAdapter(source_key="mi_fuente") as adapter:
        page = adapter.fetch("https://example.org/app")
        html = page["raw_content"]
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from typing import Any, Protocol

from scrapers.adapters._shared import backoff_delay, now_utc, sha256_hex
from scrapers.adapters.base import RawContent
from scrapers.models.source import SourceConfig

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes por defecto
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = 30.0  # segundos
_MAX_RETRIES = 5
_WEBAPP_SOURCE_TYPES = {"webapp_js"}
_SUPPORTED_BROWSERS = {"chromium", "firefox", "webkit"}


class PlaywrightAdapterError(RuntimeError):
    """Error generico al obtener contenido con Playwright."""


class PlaywrightNotInstalledError(PlaywrightAdapterError):
    """Playwright no esta instalado en el entorno actual."""


# ---------------------------------------------------------------------------
# Interfaz minima de pagina (permite inyectar un fake en tests)
# ---------------------------------------------------------------------------

class RenderedPage(Protocol):
    """Subconjunto de la API de ``playwright.sync_api.Page`` que usa el adapter."""

    url: str

    def goto(self, url: str, *, timeout: float, wait_until: str) -> Any: ...

    def content(self) -> str: ...

    def close(self) -> None: ...


PageFactory = Callable[[], RenderedPage]


def _import_sync_playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PlaywrightNotInstalledError(
            "Playwright no esta instalado. Instala la dependencia con "
            "'pip install playwright' y descarga los browsers con "
            "'playwright install chromium'."
        ) from exc
    return sync_playwright


def _validate_webapp_source(source_config: SourceConfig) -> None:
    if source_config.type not in _WEBAPP_SOURCE_TYPES:
        raise ValueError(
            f"PlaywrightAdapter only supports source types "
            f"{sorted(_WEBAPP_SOURCE_TYPES)!r}; got {source_config.type!r}"
        )


# ---------------------------------------------------------------------------
# Adapter principal
# ---------------------------------------------------------------------------

class PlaywrightAdapter:
    """Adapter que renderiza paginas con JavaScript usando Playwright.

    Parameters
    ----------
    source_key:
        Identificador de la fuente (de ``SourceConfig.id``).
    timeout:
        Timeout en segundos para la navegacion (default: 30s).
    max_retries:
        Numero maximo de intentos ante errores de red/timeout (default: 5).
    headless:
        Si True, lanza el browser sin UI (default: True).
    browser_type:
        ``chromium``, ``firefox`` o ``webkit`` (default: ``chromium``).
    page_factory:
        Fabrica de paginas inyectable para tests. Si no se provee, el adapter
        lanza un browser real de Playwright de forma perezosa en el primer
        ``fetch``.
    """

    def __init__(
        self,
        source_key: str | None = None,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _MAX_RETRIES,
        headless: bool = True,
        browser_type: str = "chromium",
        page_factory: PageFactory | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError(f"timeout debe ser > 0 (recibido: {timeout})")
        if max_retries < 1:
            raise ValueError(f"max_retries debe ser >= 1 (recibido: {max_retries})")
        if browser_type not in _SUPPORTED_BROWSERS:
            raise ValueError(
                f"browser_type debe ser uno de {sorted(_SUPPORTED_BROWSERS)!r}; "
                f"recibido: {browser_type!r}"
            )

        self.source_key = source_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.headless = headless
        self.browser_type = browser_type
        self._page_factory = page_factory or self._launch_real_page

        self._playwright: Any = None
        self._browser: Any = None

    @classmethod
    def from_source_config(
        cls,
        source_config: SourceConfig,
        *,
        page_factory: PageFactory | None = None,
    ) -> "PlaywrightAdapter":
        _validate_webapp_source(source_config)
        return cls(
            source_key=source_config.id,
            timeout=(
                source_config.timeout_seconds
                if source_config.timeout_seconds is not None
                else _DEFAULT_TIMEOUT
            ),
            max_retries=(
                source_config.max_retries
                if source_config.max_retries is not None
                else _MAX_RETRIES
            ),
            page_factory=page_factory,
        )

    def fetch(self, url: str, **kwargs: Any) -> RawContent:
        """Renderiza ``url`` con Playwright y devuelve su RawContent.

        ``**kwargs`` acepta ``wait_until`` (default: ``domcontentloaded``),
        el mismo valor que acepta ``page.goto`` de Playwright.
        """
        wait_until = kwargs.get("wait_until", "domcontentloaded")
        page = self._page_factory()
        try:
            self._goto_with_retry(page, url, wait_until=wait_until)
            html = page.content()
            final_url = page.url
        finally:
            page.close()

        return RawContent(
            source_key=self.source_key,
            source_url=final_url,
            fetched_at=now_utc(),
            http_status=200,
            content_type="text/html",
            content_hash=sha256_hex(html.encode("utf-8")),
            raw_content=html,
            page=None,
            total_pages=None,
        )

    def fetch_all(self, url: str, **kwargs: Any) -> Iterator[RawContent]:
        """Renderiza ``url`` y produce un unico RawContent.

        ``webapp_js`` no tiene un mecanismo de paginacion estandarizable a
        nivel de protocolo; cada fuente que lo necesite puede manejarlo en
        su parser a partir del HTML completo devuelto aqui.
        """
        yield self.fetch(url, **kwargs)

    def _goto_with_retry(self, page: RenderedPage, url: str, *, wait_until: str) -> None:
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                page.goto(url, timeout=self.timeout * 1000, wait_until=wait_until)
                return
            except Exception as exc:  # noqa: BLE001 - errores de Playwright son heterogeneos
                last_exc = exc
                if attempt < self.max_retries:
                    delay = backoff_delay(attempt)
                    log.warning(
                        "Error en intento %d/%d (%s) al navegar a %s — reintento en %.1fs",
                        attempt,
                        self.max_retries,
                        type(exc).__name__,
                        url,
                        delay,
                    )
                    time.sleep(delay)
                else:
                    log.warning(
                        "Error en intento %d/%d (%s) al navegar a %s — sin mas reintentos",
                        attempt,
                        self.max_retries,
                        type(exc).__name__,
                        url,
                    )

        raise PlaywrightAdapterError(
            f"No se pudo renderizar {url} con Playwright tras {self.max_retries} intentos: {last_exc}"
        ) from last_exc

    def _launch_real_page(self) -> RenderedPage:
        if self._playwright is None:
            sync_playwright = _import_sync_playwright()
            self._playwright = sync_playwright().start()

        if self._browser is None:
            browser_launcher = getattr(self._playwright, self.browser_type)
            self._browser = browser_launcher.launch(headless=self.headless)

        page: RenderedPage = self._browser.new_page()
        return page

    # ------------------------------------------------------------------
    # Context manager / cierre de recursos
    # ------------------------------------------------------------------

    def __enter__(self) -> "PlaywrightAdapter":
        return self

    def __exit__(self, *_exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
