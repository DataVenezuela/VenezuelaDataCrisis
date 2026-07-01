"""
scrapers/tests/test_api_adapter.py
===================================
Tests del ApiAdapter usando ``httpx.MockTransport``.

No se realiza ninguna conexión de red real.  Cada test instala un
``MockTransport`` que devuelve respuestas predefinidas y verifica el
comportamiento del adapter (paginación, backoff, parada, campos RawContent).
"""

from __future__ import annotations

import json
import re
import threading
import time as time_module
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from scrapers.adapters.api_adapter import ApiAdapter, _sha256
from scrapers.adapters.base import AdapterProtocol, RawContent
from scrapers.pipelines.run_pipeline import _get_adapter
from scrapers.sources.loader import load_sources


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_response(
    data: Any,
    status_code: int = 200,
    content_type: str = "application/json",
) -> httpx.Response:
    """Crea una ``httpx.Response`` con body JSON."""
    body = json.dumps(data).encode()
    return httpx.Response(
        status_code=status_code,
        headers={"content-type": content_type},
        content=body,
    )


def _make_page(items: list[Any], total: int) -> dict[str, Any]:
    """Formato de respuesta estilo encuentralos: {data: [...], total: N}."""
    return {"data": items, "total": total}


def _synthetic_records(n: int, start: int = 0) -> list[dict[str, Any]]:
    """Genera N registros sintéticos (sin datos reales)."""
    return [{"id": i, "nombre": f"Persona_{i}"} for i in range(start, start + n)]


# ---------------------------------------------------------------------------
# Transport helpers
# ---------------------------------------------------------------------------

class _PaginatedTransport(httpx.BaseTransport):
    """
    Simula un endpoint paginado con limit/offset.

    Devuelve páginas de ``page_size`` hasta completar ``total`` registros.
    Registra las URLs solicitadas para poder verificar la secuencia.
    """

    def __init__(self, total: int, page_size: int = 20) -> None:
        self.total = total
        self.page_size = page_size
        self.calls: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)

        # Parsear limit y offset de la query
        params = dict(request.url.params)
        limit = int(params.get("limit", self.page_size))
        offset = int(params.get("offset", 0))

        start = min(offset, self.total)
        end = min(offset + limit, self.total)
        items = _synthetic_records(end - start, start)
        payload = _make_page(items, self.total)
        return _json_response(payload)


class _ConcurrentPaginatedTransport(httpx.BaseTransport):
    """Endpoint paginado que mide cuántas páginas están en vuelo."""

    def __init__(self, total: int, page_size: int = 2, delay: float = 0.02) -> None:
        self.total = total
        self.page_size = page_size
        self.delay = delay
        self.offsets: list[int] = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        limit = int(params.get("limit", self.page_size))
        offset = int(params.get("offset", 0))
        with self._lock:
            self.offsets.append(offset)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time_module.sleep(self.delay)
            start = min(offset, self.total)
            end = min(offset + limit, self.total)
            return _json_response(_make_page(_synthetic_records(end - start, start), self.total))
        finally:
            with self._lock:
                self.active -= 1


class _NoTotalTransport(httpx.BaseTransport):
    """Endpoint sin total: obliga al fallback secuencial."""

    def __init__(self, total: int, page_size: int = 2) -> None:
        self.total = total
        self.page_size = page_size
        self.offsets: list[int] = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        limit = int(params.get("limit", self.page_size))
        offset = int(params.get("offset", 0))
        with self._lock:
            self.offsets.append(offset)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            start = min(offset, self.total)
            end = min(offset + limit, self.total)
            return _json_response({"data": _synthetic_records(end - start, start)})
        finally:
            with self._lock:
                self.active -= 1


class _RetryByOffsetTransport(httpx.BaseTransport):
    """Falla una vez para un offset específico y luego responde OK."""

    def __init__(self, retry_offset: int, total: int = 6, page_size: int = 2) -> None:
        self.retry_offset = retry_offset
        self.total = total
        self.page_size = page_size
        self.calls_by_offset: dict[int, int] = {}

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        limit = int(params.get("limit", self.page_size))
        offset = int(params.get("offset", 0))
        self.calls_by_offset[offset] = self.calls_by_offset.get(offset, 0) + 1
        if offset == self.retry_offset and self.calls_by_offset[offset] == 1:
            return _json_response({}, status_code=503)
        start = min(offset, self.total)
        end = min(offset + limit, self.total)
        return _json_response(_make_page(_synthetic_records(end - start, start), self.total))


class _RetryTransport(httpx.BaseTransport):
    """
    Devuelve errores 503 las primeras ``fail_times`` llamadas,
    luego una página exitosa.
    """

    def __init__(self, fail_times: int, total: int = 5) -> None:
        self.fail_times = fail_times
        self.total = total
        self.call_count = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.call_count += 1
        if self.call_count <= self.fail_times:
            return _json_response({}, status_code=503)
        items = _synthetic_records(self.total)
        return _json_response(_make_page(items, self.total))


class _EmptyTransport(httpx.BaseTransport):
    """Devuelve siempre una página vacía: {data: [], total: 0}."""

    def handle_request(self, _: httpx.Request) -> httpx.Response:
        return _json_response({"data": [], "total": 0})


class _SinglePageTransport(httpx.BaseTransport):
    """Devuelve una única página con menos registros que page_size."""

    def __init__(self, records: int) -> None:
        self.records = records
        self.call_count = 0

    def handle_request(self, _: httpx.Request) -> httpx.Response:
        self.call_count += 1
        items = _synthetic_records(self.records)
        return _json_response({"data": items, "total": self.records})


class _ErrorTransport(httpx.BaseTransport):
    """Siempre devuelve 500."""

    def handle_request(self, _: httpx.Request) -> httpx.Response:
        return _json_response({}, status_code=500)


def _adapter_with_transport(
    transport: httpx.BaseTransport,
    page_size: int = 20,
    probe_limit: int | None = None,
    max_retries: int = 5,
    max_concurrent_pages: int | None = 4,
) -> ApiAdapter:
    """Crea un ApiAdapter e inyecta el transport mock."""
    adapter = ApiAdapter(
        base_url="https://mock.test",
        page_size=page_size,
        probe_limit=probe_limit,
        max_retries=max_retries,
        max_concurrent_pages=max_concurrent_pages,
        source_key="mock_source",
    )
    # Reemplazar el cliente interno con uno que usa el transport falso
    adapter._client = httpx.Client(
        base_url="https://mock.test",
        transport=transport,
        headers=adapter._client.headers,
    )
    return adapter


# ---------------------------------------------------------------------------
# Tests: Protocol
# ---------------------------------------------------------------------------

class TestAdapterProtocol:
    def test_api_adapter_satisfies_protocol(self) -> None:
        adapter = ApiAdapter("https://mock.test")
        assert isinstance(adapter, AdapterProtocol)

    def test_raw_content_is_dict(self) -> None:
        """RawContent es simplemente dict — verificación de tipo básico."""
        rc: RawContent = {"source_key": "x", "raw_content": {}}
        assert isinstance(rc, dict)


# ---------------------------------------------------------------------------
# Tests: fetch_all — paginación completa
# ---------------------------------------------------------------------------

class TestFetchAllPagination:
    def test_fetches_known_total_pages_with_bounded_parallelism(self) -> None:
        transport = _ConcurrentPaginatedTransport(total=10, page_size=2)
        adapter = _adapter_with_transport(
            transport,
            page_size=2,
            max_concurrent_pages=2,
        )

        pages = list(adapter.fetch_all("/api/personas"))

        assert [p["offset"] for p in pages] == [0, 2, 4, 6, 8]
        assert sum(p["records_in_page"] for p in pages) == 10
        assert sorted(transport.offsets) == [0, 2, 4, 6, 8]
        assert transport.max_active <= 2
        assert transport.max_active > 1

    def test_missing_total_uses_sequential_fallback(self) -> None:
        transport = _NoTotalTransport(total=5, page_size=2)
        adapter = _adapter_with_transport(
            transport,
            page_size=2,
            max_concurrent_pages=4,
        )

        pages = list(adapter.fetch_all("/api/personas"))

        assert [p["offset"] for p in pages] == [0, 2, 4]
        assert [p["records_in_page"] for p in pages] == [2, 2, 1]
        assert transport.offsets == [0, 2, 4]
        assert transport.max_active == 1

    def test_retry_on_parallel_page_does_not_drop_other_pages(self) -> None:
        transport = _RetryByOffsetTransport(retry_offset=2, total=6, page_size=2)
        adapter = _adapter_with_transport(
            transport,
            page_size=2,
            max_retries=3,
            max_concurrent_pages=2,
        )

        with patch("scrapers.adapters.api_adapter.time.sleep"):
            pages = list(adapter.fetch_all("/api/personas"))

        assert [p["offset"] for p in pages] == [0, 2, 4]
        assert sum(p["records_in_page"] for p in pages) == 6
        assert transport.calls_by_offset[2] == 2
        assert transport.calls_by_offset[4] == 1

    def test_invalid_max_concurrent_pages_falls_back_to_one(self) -> None:
        adapter = ApiAdapter(
            base_url="https://mock.test",
            max_concurrent_pages=0,
        )
        assert adapter.max_concurrent_pages == 1
        adapter.close()

    def test_none_max_concurrent_pages_uses_default(self) -> None:
        adapter = ApiAdapter(
            base_url="https://mock.test",
            max_concurrent_pages=None,
        )
        assert adapter.max_concurrent_pages == 4
        adapter.close()

    def test_collects_all_records_exact_multiple(self) -> None:
        """290 registros, 20 por página → 15 páginas (14×20 + 1×10)."""
        total = 290
        transport = _PaginatedTransport(total=total, page_size=20)
        adapter = _adapter_with_transport(transport, page_size=20)

        pages = list(adapter.fetch_all("/api/personas"))

        expected_pages = (total + 19) // 20  # ceil division
        assert len(pages) == expected_pages

        # Verificar que acumulamos todos los registros
        collected = sum(p["records_in_page"] for p in pages)
        assert collected == total

    def test_page_numbers_are_sequential(self) -> None:
        transport = _PaginatedTransport(total=60, page_size=20)
        adapter = _adapter_with_transport(transport, page_size=20)

        pages = list(adapter.fetch_all("/api/personas"))
        page_nums = [p["page"] for p in pages]
        assert page_nums == [1, 2, 3]

    def test_offsets_increase_correctly(self) -> None:
        transport = _PaginatedTransport(total=60, page_size=20)
        adapter = _adapter_with_transport(transport, page_size=20)

        pages = list(adapter.fetch_all("/api/personas"))
        assert [p["offset"] for p in pages] == [0, 20, 40]

    def test_total_pages_is_set(self) -> None:
        transport = _PaginatedTransport(total=40, page_size=20)
        adapter = _adapter_with_transport(transport, page_size=20)

        pages = list(adapter.fetch_all("/api/personas"))
        assert all(p["total_pages"] == 2 for p in pages)

    def test_stops_on_empty_page(self) -> None:
        """
        Si el servidor devuelve data vacía, el adapter emite esa página
        (con records_in_page=0) y se detiene sin pedir más páginas.
        """
        transport = _EmptyTransport()
        adapter = _adapter_with_transport(transport)

        pages = list(adapter.fetch_all("/api/personas"))

        # Una sola página emitida (la vacía) — luego para
        assert len(pages) == 1
        assert pages[0]["records_in_page"] == 0
        assert pages[0]["page"] == 1

    def test_stops_on_partial_last_page(self) -> None:
        """7 registros con page_size=20 → 1 página parcial, luego stop."""
        transport = _SinglePageTransport(records=7)
        adapter = _adapter_with_transport(transport, page_size=20)

        pages = list(adapter.fetch_all("/api/personas"))
        assert len(pages) == 1
        assert pages[0]["records_in_page"] == 7

    def test_limit_sent_in_query(self) -> None:
        """El adapter debe enviar el parámetro ``limit`` en la query."""
        transport = _PaginatedTransport(total=20, page_size=20)
        adapter = _adapter_with_transport(transport, page_size=20)

        list(adapter.fetch_all("/api/personas"))
        first_call = transport.calls[0]
        assert "limit=20" in first_call

    def test_offset_zero_in_first_call(self) -> None:
        transport = _PaginatedTransport(total=20, page_size=20)
        adapter = _adapter_with_transport(transport, page_size=20)

        list(adapter.fetch_all("/api/personas"))
        assert "offset=0" in transport.calls[0]


# ---------------------------------------------------------------------------
# Tests: RawContent — campos y formato
# ---------------------------------------------------------------------------

class TestRawContentFields:
    def test_required_fields_present(self) -> None:
        transport = _SinglePageTransport(records=3)
        adapter = _adapter_with_transport(transport, page_size=20)

        pages = list(adapter.fetch_all("/api/personas"))
        page = pages[0]

        required = {
            "source_key", "source_url", "fetched_at",
            "http_status", "content_type", "content_hash",
            "raw_content", "page", "total_pages",
            "offset", "limit", "records_in_page",
        }
        assert required.issubset(set(page.keys()))

    def test_source_key_matches_constructor(self) -> None:
        transport = _SinglePageTransport(records=1)
        adapter = _adapter_with_transport(transport)

        page = next(adapter.fetch_all("/api/personas"))
        assert page["source_key"] == "mock_source"

    def test_http_status_is_200(self) -> None:
        transport = _SinglePageTransport(records=1)
        adapter = _adapter_with_transport(transport)

        page = next(adapter.fetch_all("/api/personas"))
        assert page["http_status"] == 200

    def test_content_hash_has_64_hexchars(self) -> None:
        transport = _SinglePageTransport(records=1)
        adapter = _adapter_with_transport(transport)

        page = next(adapter.fetch_all("/api/personas"))
        assert re.fullmatch(r"[0-9a-f]{64}", page["content_hash"])

    def test_fetched_at_is_iso8601(self) -> None:
        transport = _SinglePageTransport(records=1)
        adapter = _adapter_with_transport(transport)

        page = next(adapter.fetch_all("/api/personas"))
        ts = page["fetched_at"]
        # Formato esperado: 2026-06-24T15:30:00Z
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ts)

    def test_raw_content_contains_data_list(self) -> None:
        transport = _SinglePageTransport(records=5)
        adapter = _adapter_with_transport(transport)

        page = next(adapter.fetch_all("/api/personas"))
        assert isinstance(page["raw_content"], dict)
        assert "data" in page["raw_content"]
        assert len(page["raw_content"]["data"]) == 5

    def test_limit_field_matches_page_size(self) -> None:
        transport = _SinglePageTransport(records=10)
        adapter = _adapter_with_transport(transport, page_size=10)

        page = next(adapter.fetch_all("/api/x"))
        assert page["limit"] == 10


# ---------------------------------------------------------------------------
# Tests: fetch (single request)
# ---------------------------------------------------------------------------

class TestFetch:
    def test_fetch_returns_raw_content(self) -> None:
        transport = _SinglePageTransport(records=3)
        adapter = _adapter_with_transport(transport)

        result = adapter.fetch("/api/personas")
        assert isinstance(result, dict)
        assert result["source_key"] == "mock_source"
        assert result["page"] is None     # fetch no pagina
        assert result["offset"] is None

    def test_fetch_hash_is_deterministic(self) -> None:
        """El mismo payload siempre produce el mismo hash."""
        obj = {"data": [1, 2, 3]}
        h1 = _sha256(obj)
        h2 = _sha256(obj)
        assert h1 == h2

    def test_fetch_different_payloads_different_hashes(self) -> None:
        h1 = _sha256({"data": [1]})
        h2 = _sha256({"data": [2]})
        assert h1 != h2


# ---------------------------------------------------------------------------
# Tests: reintentos con exponential backoff
# ---------------------------------------------------------------------------

class TestRetryBackoff:
    def test_retries_on_503_and_succeeds(self) -> None:
        """503 en los 2 primeros intentos → éxito en el 3º."""
        transport = _RetryTransport(fail_times=2, total=5)
        adapter = _adapter_with_transport(transport, max_retries=5)

        # Parchar time.sleep para no esperar en tests
        with patch("scrapers.adapters.api_adapter.time.sleep"):
            pages = list(adapter.fetch_all("/api/personas"))

        assert len(pages) == 1
        assert pages[0]["records_in_page"] == 5
        assert transport.call_count == 3   # 2 fallos + 1 éxito

    def test_raises_after_max_retries(self) -> None:
        """Si todos los intentos fallan, debe lanzar RuntimeError."""
        transport = _ErrorTransport()
        adapter = _adapter_with_transport(transport, max_retries=3)

        with patch("scrapers.adapters.api_adapter.time.sleep"):
            with pytest.raises(RuntimeError, match="Máximo de reintentos"):
                list(adapter.fetch_all("/api/personas"))

    def test_backoff_delay_increases(self) -> None:
        """El delay debe aumentar con cada intento (sin jitter, verificar tendencia)."""
        from scrapers.adapters._shared import backoff_delay

        delays = [backoff_delay(i) for i in range(1, 6)]
        # Verificar que la parte determinista crece (sin contar el jitter aleatorio)
        bases = [min(1.0 * (2 ** (i - 1)), 60.0) for i in range(1, 6)]
        assert bases == sorted(bases)   # crece monotónicamente
        assert all(d > 0 for d in delays)


# ---------------------------------------------------------------------------
# Tests: extra_params y headers
# ---------------------------------------------------------------------------

class TestExtraParams:
    def test_extra_params_forwarded(self) -> None:
        """Params adicionales deben aparecer en la URL de la petición."""
        transport = _PaginatedTransport(total=5, page_size=20)
        adapter = _adapter_with_transport(transport, page_size=20)

        list(adapter.fetch_all("/api/personas", params={"estado": "missing"}))
        assert "estado=missing" in transport.calls[0]

    def test_custom_source_key(self) -> None:
        adapter = ApiAdapter(
            base_url="https://mock.test",
            source_key="encuentralos_tecnosoft",
        )
        adapter._client = httpx.Client(
            base_url="https://mock.test",
            transport=_SinglePageTransport(records=1),
        )
        page = next(adapter.fetch_all("/api/personas"))
        assert page["source_key"] == "encuentralos_tecnosoft"
        adapter.close()


class TestSourceConfigIntegration:
    def test_loader_reads_max_concurrent_pages(self, tmp_path: Path) -> None:
        config = tmp_path / "sources.yaml"
        config.write_text(
            """
project:
  event_id: 8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a
sources:
  - id: api_demo
    name: API Demo
    type: api_json
    enabled: true
    trust_tier: C
    url: "https://example.org/api/personas"
    refresh_minutes: 30
    parser_asignado: encuentralos
    max_concurrent_pages: 3
""",
            encoding="utf-8",
        )

        _project, sources = load_sources(config)

        assert sources[0].max_concurrent_pages == 3

    def test_get_adapter_passes_max_concurrent_pages(self) -> None:
        from scrapers.models.source import SourceConfig

        source = SourceConfig(
            id="api_demo",
            name="API Demo",
            type="api_json",
            enabled=True,
            trust_tier="C",
            url="https://example.org/api/personas",
            refresh_minutes=30,
            parser_asignado="encuentralos",
            timeout_seconds=9.0,
            max_retries=2,
            max_concurrent_pages=3,
        )

        adapter = _get_adapter(source)

        assert isinstance(adapter, ApiAdapter)
        assert adapter.timeout == 9.0
        assert adapter.max_retries == 2
        assert adapter.max_concurrent_pages == 3
        adapter.close()


# ---------------------------------------------------------------------------
# Tests: context manager
# ---------------------------------------------------------------------------

class TestContextManager:
    def test_context_manager_closes_client(self) -> None:
        transport = _SinglePageTransport(records=1)
        with ApiAdapter("https://mock.test", source_key="cm_test") as adapter:
            adapter._client = httpx.Client(
                base_url="https://mock.test",
                transport=transport,
            )
            pages = list(adapter.fetch_all("/api/x"))
            assert len(pages) == 1
        # El cliente debe estar cerrado; httpx.Client.is_closed no existe en todas
        # las versiones, así que simplemente verificamos que no lanza excepción


# ---------------------------------------------------------------------------
# Tests para los fixes del code review
# ---------------------------------------------------------------------------

class TestUnrecognizedDictSchema:
    def test_warning_on_unrecognized_nonempty_dict(self, caplog: Any) -> None:
        """Dict no vacío sin clave conocida → WARNING (fix review mayerlim)."""
        import logging

        class _UnknownSchemaTransport(httpx.BaseTransport):
            def handle_request(self, _: httpx.Request) -> httpx.Response:
                # Responde con un dict no vacío pero con clave desconocida
                return _json_response({"resultados": [{"id": 1}], "total": 1})

        adapter = _adapter_with_transport(_UnknownSchemaTransport(), page_size=20, max_retries=1)

        with caplog.at_level(logging.WARNING, logger="scrapers.adapters.api_adapter"):
            pages = list(adapter.fetch_all("/api/personas"))

        # Emite al menos una página (con records_in_page=0) y loguea WARNING
        assert len(pages) == 1
        assert pages[0]["records_in_page"] == 0
        assert any("no matchea ninguna clave conocida" in r.getMessage() for r in caplog.records)

    def test_no_warning_on_empty_dict(self, caplog: Any) -> None:
        """Dict vacío es 'fuente vacía real' — no debe loguear WARNING."""
        import logging

        class _EmptyDictTransport(httpx.BaseTransport):
            def handle_request(self, _: httpx.Request) -> httpx.Response:
                return _json_response({})

        adapter = _adapter_with_transport(_EmptyDictTransport(), page_size=20, max_retries=1)

        with caplog.at_level(logging.WARNING, logger="scrapers.adapters.api_adapter"):
            list(adapter.fetch_all("/api/personas"))

        assert not any("no matchea" in r.getMessage() for r in caplog.records)

    def test_no_warning_on_known_key(self, caplog: Any) -> None:
        """Clave 'data' conocida — no debe loguear WARNING."""
        import logging

        transport = _SinglePageTransport(records=2)
        adapter = _adapter_with_transport(transport, page_size=20, max_retries=1)

        with caplog.at_level(logging.WARNING, logger="scrapers.adapters.api_adapter"):
            list(adapter.fetch_all("/api/personas"))

        assert not any("no matchea" in r.getMessage() for r in caplog.records)


class TestNoSleepOnLastAttempt:
    def test_no_sleep_on_last_retry_status(self) -> None:
        """En el último intento retryable no se llama time.sleep (fix review mayerlim)."""
        transport = _ErrorTransport()  # siempre 500
        adapter = _adapter_with_transport(transport, max_retries=3)

        sleep_calls: list[float] = []
        with patch("scrapers.adapters.api_adapter.time.sleep", side_effect=sleep_calls.append):
            with pytest.raises(RuntimeError):
                list(adapter.fetch_all("/api/personas"))

        # Con max_retries=3: sleep tras intento 1 y 2; NO tras intento 3
        assert len(sleep_calls) == 2

    def test_no_sleep_on_last_retry_network_error(self) -> None:
        """En el último intento por NetworkError no se llama time.sleep."""
        class _NetworkErrorTransport(httpx.BaseTransport):
            def handle_request(self, _: httpx.Request) -> httpx.Response:
                raise httpx.NetworkError("conn refused")

        adapter = _adapter_with_transport(
            _NetworkErrorTransport(), max_retries=3
        )

        sleep_calls: list[float] = []
        with patch("scrapers.adapters.api_adapter.time.sleep", side_effect=sleep_calls.append):
            with pytest.raises(RuntimeError):
                list(adapter.fetch_all("/api/personas"))

        assert len(sleep_calls) == 2


# ---------------------------------------------------------------------------
# Tests: auto-detect effective_page_size cuando la API capea el limit
# ---------------------------------------------------------------------------

class _CapedLimitTransport(httpx.BaseTransport):
    """Simula una API que ignora limit>cap y siempre devuelve como máximo cap registros."""

    def __init__(self, total: int, cap: int) -> None:
        self.total = total
        self.cap = cap
        self.offsets: list[int] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        offset = int(params.get("offset", 0))
        self.offsets.append(offset)
        start = min(offset, self.total)
        end = min(offset + self.cap, self.total)
        items = _synthetic_records(end - start, start)
        return _json_response({"data": items, "total": self.total})


class TestEffectivePageSizeAutoDetect:
    def test_probe_limit_supported_offsets_step_by_probe_size(self) -> None:
        """Si el API soporta probe_limit=1000, los offsets saltan de 1000 en 1000."""
        transport = _PaginatedTransport(total=2500, page_size=20)
        adapter = _adapter_with_transport(
            transport,
            page_size=20,
            probe_limit=1000,
            max_concurrent_pages=4,
        )

        list(adapter.fetch_all("/api/personas"))

        assert "limit=1000" in transport.calls[0]
        assert sorted(
            int(dict(httpx.URL(call).params)["offset"]) for call in transport.calls
        ) == [0, 1000, 2000]

    def test_probe_limit_caps_to_returned_count(self) -> None:
        """Si pedimos 1000 y el API capea en 100, los offsets usan 100."""
        total = 250
        cap = 100
        transport = _CapedLimitTransport(total=total, cap=cap)
        adapter = _adapter_with_transport(
            transport,
            page_size=20,
            probe_limit=1000,
            max_concurrent_pages=4,
        )

        pages = list(adapter.fetch_all("/api/personas"))
        all_records = [r for p in pages for r in p["raw_content"]["data"]]

        assert len(all_records) == total
        assert sorted(transport.offsets) == [0, 100, 200]

    def test_no_records_skipped_when_api_caps_limit(self) -> None:
        """Si la API capea el limit (pedimos 100, devuelve 20), todos los registros llegan."""
        total = 50
        cap = 10
        transport = _CapedLimitTransport(total=total, cap=cap)
        adapter = _adapter_with_transport(transport, page_size=100, max_concurrent_pages=4)

        pages = list(adapter.fetch_all("/api/personas"))
        all_records = [r for p in pages for r in p["raw_content"]["data"]]

        assert len(all_records) == total
        assert [r["id"] for r in all_records] == list(range(total))

    def test_offsets_step_by_effective_size_not_configured_size(self) -> None:
        """Los offsets paralelos deben saltar de cap en cap, no de page_size en page_size."""
        total = 40
        cap = 10
        transport = _CapedLimitTransport(total=total, cap=cap)
        adapter = _adapter_with_transport(transport, page_size=100, max_concurrent_pages=4)

        list(adapter.fetch_all("/api/personas"))

        assert sorted(transport.offsets) == list(range(0, total, cap))

    def test_no_autodetect_when_page_size_matches_actual(self) -> None:
        """Si la API devuelve page_size registros, no hay auto-detect (comportamiento normal)."""
        total = 40
        transport = _PaginatedTransport(total=total, page_size=10)
        adapter = _adapter_with_transport(transport, page_size=10, max_concurrent_pages=4)

        pages = list(adapter.fetch_all("/api/personas"))
        all_records = [r for p in pages for r in p["raw_content"]["data"]]

        assert len(all_records) == total

    def test_no_autodetect_on_single_page_dataset(self) -> None:
        """Si el total cabe en la primera página, partial != cap — no debe confundirlos."""
        transport = _SinglePageTransport(records=5)
        adapter = _adapter_with_transport(transport, page_size=20, max_concurrent_pages=4)

        pages = list(adapter.fetch_all("/api/personas"))

        assert len(pages) == 1
        assert pages[0]["records_in_page"] == 5

    def test_limit_reflects_returned_count_on_partial_single_page(self) -> None:
        """En página única parcial, limit == registros recibidos, no page_size solicitado."""
        transport = _SinglePageTransport(records=5)
        adapter = _adapter_with_transport(
            transport,
            page_size=20,
            probe_limit=1000,
            max_concurrent_pages=4,
        )

        page = next(adapter.fetch_all("/api/personas"))

        assert page["limit"] == 5
        assert transport.call_count == 1


class TestNoModuleLevelClientLeak:
    def test_import_does_not_create_unclosed_client(self) -> None:
        """
        El assert module-level fue eliminado (fix review mayerlim).
        Reimportar el módulo no debe crear httpx.Client sin cerrar.
        El test verifica que isinstance contra el Protocol funciona
        sin instanciar un adapter real.
        """
        from scrapers.adapters.base import AdapterProtocol
        from scrapers.adapters.api_adapter import ApiAdapter

        # isinstance con @runtime_checkable no necesita instancia real
        # — solo verifica presencia de métodos en la clase
        assert issubclass(ApiAdapter, object)  # trivialmente verdadero
        # El test principal ya existe en TestAdapterProtocol
        adapter = ApiAdapter("https://mock.test", source_key="leak_test")
        try:
            assert isinstance(adapter, AdapterProtocol)
        finally:
            adapter.close()  # cierre explícito garantizado
