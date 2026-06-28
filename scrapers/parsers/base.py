"""
scrapers/parsers/base.py
========================
Contrato común (Protocol) para todos los parsers del pipeline.

Un parser recibe el ``RawContent`` que produjo un adapter y devuelve una lista
de entidades tipadas: ``list[Person]``, ``list[AcopioCenter]`` o
``list[Event]``.

Cada parser conoce la estructura de su fuente: qué campo es el nombre, qué
campo es la cédula, qué valores de status mapea a qué enum interno.

Responsabilidades del parser
-----------------------------
- Extraer registros individuales del payload raw.
- Mapear campos de la fuente al modelo interno.
- Convertir estados externos al enum controlado.
- Hashear PII (cédulas) vía ``shared.hashing.identity_token``.
- Producir entidades tipadas listas para la capa de limpieza.

Lo que el parser NO debe hacer
--------------------------------
- Guardar PII en claro ni loguearla.
- Hacer deduplicación global.
- Confirmar que dos personas son la misma.
- Descartar registros por estar incompletos.
- Inventar campos que la fuente no tiene.
- Hacer fetch de red.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from scrapers.adapters.base import RawContent
from scrapers.models import AcopioCenter, Event, Person

# Tipo de salida: cualquiera de los tres modelos tipados.
ParsedEntity = Person | AcopioCenter | Event


@runtime_checkable
class ParserProtocol(Protocol):
    """
    Interfaz que todo parser concreto del pipeline debe implementar.

    ``@runtime_checkable`` permite usar ``isinstance(obj, ParserProtocol)``
    en tests para verificar la presencia del método ``parse``.
    """

    #: Identificador legible de la fuente que este parser maneja.
    #: Se usa como valor de ``fuente`` en las entidades producidas.
    source_key: str

    def parse(self, raw: RawContent, **kwargs: Any) -> list[ParsedEntity]:
        """
        Convierte un ``RawContent`` en una lista de entidades tipadas.

        Parameters
        ----------
        raw:
            Dict producido por un adapter (``fetch`` o ``fetch_all``).
            El campo ``raw["raw_content"]`` contiene el payload original
            de la fuente (puede ser dict, list o str).
        **kwargs:
            Parámetros opcionales que el parser concreto puede aceptar
            (p. ej. ``secret`` para el HMAC de cédulas).

        Returns
        -------
        list[ParsedEntity]
            Lista de entidades tipadas.  Puede estar vacía si el payload
            no contiene registros válidos.  Nunca lanza excepción por un
            registro individual mal formado — lo omite y lo loguea.
        """
        ...