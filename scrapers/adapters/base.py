"""
scrapers/adapters/base.py
=========================
Contrato común (Protocol) que deben cumplir todos los adapters del pipeline.

Un adapter es responsable **únicamente** de obtener contenido raw desde una
fuente.  No interpreta el significado del contenido, no normaliza, no hashea
cédulas, no persiste nada.

El output de todos los adapters es ``RawContent``: un dict con los campos
mínimos que el pipeline necesita para encaminar el payload al parser correcto
y mantener trazabilidad hacia la fuente original.

Campos de RawContent
--------------------
source_key : str
    Identificador único de la fuente (de SourceConfig.id).
source_url : str
    URL real desde la que se obtuvo el contenido.
fetched_at : str
    Timestamp ISO-8601 UTC del momento del fetch.
http_status : int
    Código HTTP de la respuesta (200, 404, …).
content_type : str
    Valor del header Content-Type devuelto por el servidor.
content_hash : str
    SHA-256 hexadecimal del raw_content (prefijo "sha256:").
raw_content : str | dict | list
    Contenido crudo.  Para adapters JSON puede ser el objeto ya deserializado;
    para HTML/texto es un str.  Puede contener PII — uso interno únicamente.
page : int | None
    Número de página (base 1) para adapters paginados.  None si no aplica.
total_pages : int | None
    Total de páginas estimadas.  None si el adapter no lo conoce.
"""

from __future__ import annotations

from typing import Any, Iterator, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Tipo público
# ---------------------------------------------------------------------------

# RawContent es un dict abierto para no acoplar el Protocol a Pydantic.
# Los campos documentados arriba son obligatorios; los adapters pueden añadir
# campos extra específicos de la fuente.
RawContent = dict[str, Any]


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class AdapterProtocol(Protocol):
    """
    Interfaz que todo adapter del pipeline debe implementar.

    Un adapter puede implementar ``fetch`` (página única) o ``fetch_all``
    (paginación completa), o ambos.  El pipeline usa ``fetch_all`` cuando
    está disponible; ``fetch`` para adapters sin paginación.

    Nota: ``@runtime_checkable`` permite usar ``isinstance(obj, AdapterProtocol)``
    en tests, pero solo verifica la presencia de los métodos — no las firmas.
    """

    def fetch(self, url: str, **kwargs: Any) -> RawContent:
        """
        Obtiene una sola página/recurso y devuelve su RawContent.

        Parameters
        ----------
        url:
            URL de la fuente a consultar.
        **kwargs:
            Parámetros adicionales (headers, params, timeout, …) que el
            adapter concreto puede aceptar.

        Returns
        -------
        RawContent
            Dict con al menos los campos documentados en el módulo.
        """
        ...

    def fetch_all(self, url: str, **kwargs: Any) -> Iterator[RawContent]:
        """
        Obtiene todas las páginas de una fuente paginada.

        Yields un ``RawContent`` por página.  El caller puede iterar con
        ``for page in adapter.fetch_all(url): ...`` sin cargar todo en memoria.

        La implementación concreta decide cuándo detenerse (campo ``next`` en
        la respuesta, ``total`` vs registros acumulados, página vacía, etc.).
        """
        ...