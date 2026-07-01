# VZLA_DEDUP — Documentación técnica

Esta carpeta contiene la documentación técnica del proyecto VZLA_DEDUP.

El objetivo de estos documentos es definir contratos claros entre equipos para que scrapers, limpieza, deduplicación, base de datos, API y verificación puedan avanzar sin romper compatibilidad entre sí.

Este proyecto trabaja con información sensible en un contexto de emergencia. Por eso, toda decisión técnica debe priorizar:

1. Protección de datos personales.
2. Trazabilidad hacia las fuentes.
3. Evitar falsos merges.
4. Evitar pérdida irreversible de información.
5. Validación humana cuando exista incertidumbre.

---

## Documentos

### `pipeline.md`

Describe el flujo completo del sistema:

```text
Fuentes → Adapters → Parsers → Modelos tipados → Limpieza → Deduplicación → Export → DB/API
```

Úsalo para entender qué responsabilidad tiene cada capa y qué cosas no deben mezclarse.

---

### `scraper_contract.md`

Define el contrato de salida que deben cumplir los scrapers.

Incluye:

* Archivos JSONL esperados.
* Campos obligatorios y opcionales.
* Reglas de `null`.
* Formato de fechas.
* Enums.
* Reglas para no exponer PII.
* Ejemplos de salida.

Este es el documento más importante para quienes desarrollan scrapers.

---

### `source_config.md`

Define cómo declarar nuevas fuentes.

Incluye:

* Formato YAML/JSON de una fuente.
* Tipos de fuente soportados.
* `trust_tier`.
* `parser` asignado.
* Reglas de rate limit.
* Dominios permitidos.
* Metadatos mínimos.

---

### `pfif-import.md`

Define el subconjunto PFIF 1.5 soportado para import offline.

Incluye:

* Mapeo de XML PFIF a `Person`.
* Status soportados.
* Campos sensibles que no se importan.
* Alcance fuera de este primer parser.

---

### `schema.md`

Define las entidades principales del proyecto.

Incluye:

* `events`
* `persons`
* `person_notes`
* `person_sources`
* `person_photos`
* `acopio_centers`
* `dedup_candidates`

Este documento debe mantenerse alineado con los modelos de base de datos y los JSONL exportados.

---

## Reglas globales

Todos los documentos técnicos deben respetar estas reglas:

* Fechas en UTC e ISO 8601.
* IDs internos como UUID v4.
* `null` explícito para valores desconocidos.
* Nunca usar `""`, `"N/A"` o `0` como sustituto de datos desconocidos.
* Enums como strings controlados.
* JSONL con una entidad válida por línea.
* Nada de datos reales en ejemplos, fixtures o documentación.
* Nada de cédulas, teléfonos, direcciones exactas o nombres reales de víctimas.
* Todo dato exportado debe mantener trazabilidad hacia una fuente.

---

## Orden recomendado de lectura

Para colaboradores nuevos:

1. `../README.md`
2. `../CONTRIBUTING.md`
3. `docs/README.md`
4. `docs/pipeline.md`
5. `docs/scraper_contract.md`

Para quienes agregan una nueva fuente:

1. `docs/source_config.md`
2. `docs/pipeline.md`
3. `docs/scraper_contract.md`

Para quienes trabajan en base de datos o API:

1. `docs/schema.md`
2. `docs/scraper_contract.md`

---

## Regla de oro

En una crisis, un duplicado es molesto.

Un falso merge puede ser peligroso.

Por eso:

```text
Duplicar es tolerable.
Perder trazabilidad no.
Exponer PII no.
Confirmar automáticamente identidades dudosas no.
```
