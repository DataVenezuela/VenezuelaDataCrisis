# ADR 0006 — Protección de PII en la ingesta

| Campo | Valor |
|---|---|
| Estado | Propuesta |
| Fecha | 2026-07-04 |
| Decisores | Mantenedores (mathiasaiva, mayerlim), equipo de pipeline |
| Reemplaza a | (ninguno) |
| Complementa | ADR 0002 (endurecimiento del borde), ADR 0004 (versionado de contrato) |
| Relacionado con | `docs/adr/0003-reestructuracion-repos-deployment.md` <!-- pendiente PR 230 --> |

---

## 1. Contexto

La ADR 0002 endurece el **borde público**: lo que sale a la API. Falta declarar el
otro lado: cómo se protege la PII **en la ingesta**, antes de persistir en el plano
interno. Las fuentes entregan cédulas, teléfonos de terceros y datos de menores, a
veces en claro y a veces ya enmascarados de forma heterogénea (Encuéntralos entrega
la cédula como `****5675`). El pipeline necesita una doctrina explícita para no
confiar esa protección al criterio de cada parser.

---

## 2. Decisión

La protección de PII en la ingesta es responsabilidad del **pipeline**, con el
parser como primera capa, en dos capas que fallan hacia **cuarentena**, nunca a
descarte silencioso.

**Frontera:** ADR 0002 = borde / público (lo que sale); ADR 0006 = ingesta /
interno (lo que entra).

**Enforcement (dos capas).**

- **Parser:** tokeniza la cédula (`shared/hashing.identity_token`) antes de crear
  la entidad y descarta `telefono_contacto` de terceros.
- **Pipeline (backstop central, no se delega al parser):** `tokenize_pii_fields`
  re-tokeniza, `_strip_raw_pii` elimina PII cruda residual, y
  `protect_minor_fields` reduce la PII de un menor (anula `foto`/`cedula_masked`,
  acota la ubicación a nivel estado). Si la reducción no se puede aplicar, el
  registro va a cuarentena (fail-closed).

**Modelo de identidad y procedencia (objetivo).**

- `identity_kind` en `{hmac, partial, none}`: modela la ausencia legítima de
  identificador fuerte sin debilitar las garantías de quien sí lo tiene.
- `_pii_provenance`: distingue "lo enmascaramos nosotros" de "vino enmascarado por
  la fuente", para no tratar una máscara ajena como clave de dedup fuerte.
- `*_status` (`removed_minor`, `removed_pii`) en `foto`/`last_known_location`: no
  confundir "ausente en la fuente" con "removido por protección" en auditoría.

`detect_pii` es hoy **advisory** (alimenta redacción, conteos y scoring); no es un
gate obligatorio previo a la persistencia. El gate real son el tokenizado,
`_strip_raw_pii` y `protect_minor_fields`.

---

## 3. Consecuencias

**Positivas**

- La PII cruda nunca vive en reposo en el plano interno; la del menor se reduce
  centralmente; la ausencia queda auditable.
- La protección no depende del criterio de cada parser (un descuido lo atrapa el
  backstop).

**Negativas / costos asumidos**

- El modelo objetivo agrega campos y validadores. `identity_kind` y
  `pii_provenance` ya existen como columnas de `persons` en el esquema canónico
  (`docs/schema.md`); los `*_status` (p. ej. `removed_minor`/`removed_pii`) todavía
  no.
- Tokenizar y reducir tiene costo por registro.

**Riesgos y mitigaciones**

- *Un parser deja escapar PII en claro*: lo cubre el backstop del pipeline.
- *`detect_pii` advisory no bloquea*: el objetivo es promoverlo a gate; mientras
  tanto, el tokenizado y `_strip_raw_pii` son la barrera dura.

---

## 4. Estado / implementación

- **Real hoy:** `cedula_hmac` en el parser mas backstop de pipeline;
  `telefono_contacto` descartado; `derive_is_minor` + `protect_minor_fields`
  fail-closed a cuarentena; `REASON_CODES` con `risk_level`.
- **Ya en el esquema canónico:** `identity_kind` y `pii_provenance` como columnas
  de `persons` (ver `docs/schema.md`); la clave en vuelo lleva prefijo
  (`_pii_provenance`) y persiste como `pii_provenance`.
- **Objetivo (no construido):** `*_status`, y `detect_pii` como gate obligatorio.

---

## 5. Enlaces

- ADR 0002 (endurecimiento del borde), ADR 0004 (versionado de contrato).
- `scrapers/sanitizers/` (`pii_tokenizer.py`, `minor_protection.py`, `pii_detector.py`).
- `scrapers/exporters/quarantine_exporter.py`, `shared/hashing.py`.
