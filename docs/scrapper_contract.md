# Contrato de scrapers: parser a entidad

> **Estado:** Propuesta
> **CONTRACT_VERSION:** 1.0
> **Issue:** #231 (documenta el contrato existente), #235 (rediseño parser a entidad)
> **Origen:** issue #224 (punto 4), ADR 0004 (versionado), ADR 0006 (identidad/PII objetivo)
> **Fecha:** 2026-07-04

Contrato que deben cumplir los parsers de VZLA_DEDUP: convierten el `RawContent`
de un adapter en `list[Person | AcopioCenter | Event]`. `CONTRACT_VERSION`
describe lo que existe hoy en el código (`scrapers/models/`); lo que aún no está
construido vive en la sección 10, marcado "no implementado". El contrato que sigue
a este (entidad a staging en Supabase) es `docs/specs/db-scraper-contract.md`.

---

## 1. Alcance

Cubre las entidades que produce un parser, sus campos y tipos, los enums
permitidos, las reglas de PII y las convenciones de `null`.

No cubre: endpoints de API, schema de base de datos, reglas del consolidation job,
ni verificación humana. El contrato entidad a staging vive en
`docs/specs/db-scraper-contract.md`.

---

## 2. Precondiciones y flujo del parser

Un parser convierte el `RawContent` de un adapter en
`list[Person | AcopioCenter | Event]`. No persiste nada, no hace requests
adicionales, no toma decisiones de dedup.

El parser debe:

1. Extraer campos del raw según la estructura de su fuente.
2. Aplicar HMAC a las cédulas **antes** de crear la entidad
   (`shared/hashing.identity_token`, ver §5).
3. Mapear el status al enum correcto (§4).
4. Usar `trust_tier` en letra: `A`, `B`, `C` o `D`.
5. Dejar en `None` cualquier campo no disponible. Nunca inventar valores ni
   descartar un registro por tener campos ausentes.

Las entidades son modelos Pydantic con `extra="forbid"`
(`scrapers/models/person.py`, `event.py`, `acopio_center.py`): un campo no
declarado es un error de validación, no se ignora.

Va a **cuarentena** en vez de al parser: fuente sin parser asignado, PII no
redactable automáticamente, schema inválido o inesperado, PDF sin texto
extraíble (§6).

---

## 3. Entidades y campos

Campos reales de los modelos de hoy. Los defaults y validadores son los que
aplica el código.

### 3.1. Person (`scrapers/models/person.py`)

| Campo | Tipo | Notas |
|---|---|---|
| `full_name` | `str` | requerido, no vacío |
| `event_id` | `str` | requerido, UUID válido (FK al evento) |
| `cedula_hmac` | `str \| None` | 64 hex minúscula, sin prefijo. `None` si no hay cédula |
| `cedula_masked` | `str \| None` | máx 15 chars. Ver §10 (lo supera `cedula_partial`) |
| `age_range` | `dict \| None` | claves `min`/`max`; `min <= max` |
| `is_minor` | `bool \| None` | si no se declara, se deriva de `age_range` (max < 18) |
| `last_known_location` | `str \| None` | |
| `status` | `str` | default `missing`, enum §4 |
| `verification_status` | `str` | default `unverified` (valores por convención, §4) |
| `trust_tier` | `str` | default `D`, `A`/`B`/`C`/`D` |
| `confidence_score` | `float` | default `0.0`, rango `[0.0, 1.0]`, rechaza bool |
| `foto` | `str \| None` | URL, sin descargar |
| `nota` | `str \| None` | |
| `deterministic_id` | `str \| None` | id determinístico de dedup, ver `docs/specs/db-scraper-contract.md` §6 |
| `source_record_id` | `str \| None` | id nativo del registro en la fuente |
| `fuente` | `str` | requerido, slug de la fuente |

### 3.2. Event (`scrapers/models/event.py`)

| Campo | Tipo | Notas |
|---|---|---|
| `event_type` | `str` | requerido, enum §4 |
| `description` | `str` | requerido, no vacío |
| `location_text` | `str \| None` | |
| `date_iso` | `str \| None` | ISO-8601 válido (acepta sufijo `Z`) |
| `trust_tier` | `str` | default `D`, `A`/`B`/`C`/`D` |
| `confidence_score` | `float` | default `0.0`, `[0.0, 1.0]`, rechaza bool |
| `fuente` | `str` | requerido |
| `nota` | `str \| None` | |

### 3.3. AcopioCenter (`scrapers/models/acopio_center.py`)

| Campo | Tipo | Notas |
|---|---|---|
| `name` | `str` | requerido, no vacío |
| `event_id` | `str` | requerido, UUID válido (FK al evento) |
| `location_text` | `str` | requerido, no vacío |
| `coordinates` | `dict \| None` | claves exactas `lat`/`lon`, en rango |
| `needs` | `list[str]` | default `[]`, texto libre hoy (ver §10 para la lista cerrada objetivo) |
| `status` | `str` | default `unverified`, enum §4 |
| `trust_tier` | `str` | default `D`, `A`/`B`/`C`/`D` |
| `confidence_score` | `float` | default `0.0`, `[0.0, 1.0]`, rechaza bool |
| `fuente` | `str` | requerido |
| `nota` | `str \| None` | |

### 3.4. Metadatos de trazabilidad (claves `_*`)

El pipeline inyecta claves con prefijo `_` tras `model_dump()`. Reales hoy:
`_entity_type` y `_source_record_id` (poblados). `_source_url` y `_parser_version`
se leen aguas abajo pero **hoy nunca se asignan** (cableado muerto, siempre
`null`): ver `docs/specs/db-scraper-contract.md` §4 y la nota de seguimiento. El
sobre tipado (`ScraperEnvelope`) es objetivo, no existe hoy (§10).

---

## 4. Enums y valores

- **`Person.status`** (validado): `missing`, `found`, `injured`, `deceased`,
  `unknown`.
- **`Person.verification_status`** (por convención, no validado en el modelo):
  `unverified`, `verified`, `disputed`.
- **`Event.event_type`** (validado): `earthquake`, `flood`, `landslide`, `other`.
- **`AcopioCenter.status`** (validado): `active`, `full`, `closed`, `unverified`.
- **`trust_tier`** (validado en las tres entidades): `A` fuente oficial, `B` ONG o
  medio establecido, `C` voluntario con ownership visible, `D` anónima o sin
  verificar. El parser produce un default restrictivo; su elevación es objetivo y
  gobernada (§10, ADR 0005).

---

## 5. PII: reglas no negociables

1. Calcular `cedula_hmac` **antes** de crear la entidad con
   `shared/hashing.identity_token(cedula, secret)`. El pipeline repite el
   tokenizado como segunda capa (`tokenize_pii_fields`).
2. La `cedula` cruda **no** entra al modelo. Nunca.
3. `cedula_hmac` = 64 hex minúscula, sin el prefijo `hmac_sha256:`.
4. El prefijo de nacionalidad (`V`/`E`) es parte del identificador canónico:
   `V12345678` y `E12345678` producen HMAC distintos.
5. Los teléfonos de contacto de familiares (`telefono_contacto`) se descartan en
   el parser; el pipeline aplica un backstop central (`_strip_raw_pii`), porque a
   la fuente se le escapan registros en claro.
6. `is_minor` se declara y se deriva de `age_range`; el pipeline reduce la PII del
   menor (`protect_minor_fields`: anula `foto`/`cedula_masked`, acota la ubicación
   a nivel estado), y falla hacia cuarentena si la reducción no se puede aplicar.

La frontera parser/pipeline y la doctrina de dos capas están en la ADR 0006
(`ADR 0002 = borde/público; ADR 0006 = ingesta/interno`).

---

## 6. Garantías y cuarentena

- **Nada se descarta en silencio.** Un registro que falla PII, protección de
  menores o validación de schema va a cuarentena con un motivo nombrado, no
  desaparece.
- **`Person` nunca hace auto-merge**: genera candidatos para revisión humana
  (`DedupSpec.allow_automerge=False`). `Event` y `AcopioCenter` sí auto-mergean
  (`allow_automerge=True`). Ver `docs/specs/person-dedup.md`.
- **Motivos de cuarentena** (`REASON_CODES`,
  `scrapers/exporters/quarantine_exporter.py`): `pii_untreatable`,
  `invalid_schema`, `parser_unavailable`, `pdf_no_text`, `unclassified_sensitive`,
  `contradictory_sources`, `ambiguous_manual_review`. Cada registro lleva
  `risk_level` (`low`/`medium`/`high`) y un preview redactado.

---

## 7. Downstream (contexto)

Las entidades pasan al exporter de staging (`list[...] -> aportes`), descrito en
`docs/specs/db-scraper-contract.md`. El consolidation job (fuera de este repo) lee
`aportes` y escribe las tablas canónicas. Nada de eso es responsabilidad del
parser.

---

## 8. Ejemplo (datos ficticios)

```json
{
  "full_name": "JOSE LUIS PEREZ DEMO",
  "event_id": "f0e1d2c3-b4a5-6789-0fed-cba987654321",
  "cedula_hmac": "3b4c9e2a1fd82f6a0bc347e1a9f2c8d5e047b3a12f9c6d71e8b405a3c2d1f9e0",
  "cedula_masked": "V-****5821",
  "age_range": {"min": 30, "max": 40},
  "is_minor": false,
  "last_known_location": "El Tocuyo, Lara",
  "status": "missing",
  "verification_status": "unverified",
  "trust_tier": "C",
  "confidence_score": 0.42,
  "foto": "https://encuentralos.tecnosoft.dev/registro/demo-12345",
  "fuente": "encuentralos.tecnosoft.dev"
}
```

---

## 9. Lo que NO cubre

- No define el schema de producción ni las migraciones (viven en el repo de
  BD/API, ver ADR 0003).
- No define endpoints de API ni reglas del consolidation job.
- No garantiza dedup cross-source de `Person` (solo genera candidatos) ni
  verificación de identidad (revisión humana).

---

## 10. Modelo objetivo (no implementado)

Forma a la que apunta el contrato pero que **aún no existe en el código**. No
altera `CONTRACT_VERSION` (que describe lo real); una idea aquí se vuelve parte de
la versión solo cuando se implementa y se sube la versión (ADR 0004). Las
decisiones de identidad y PII están en la **ADR 0006**.

**Identidad explícita: discriminador `identity_kind`** (construir pronto:
Encuéntralos entrega la cédula enmascarada hoy). `identity_kind` en
`{hmac, partial, none}`, con `cedula_partial` (2 a 4 dígitos) y
`cedula_partial_pattern` en `{suffix_4, suffix_3, suffix_2, edges_2_2}`.
Validadores: `hmac` exige `cedula_hmac`; `partial` exige `cedula_partial`;
`cedula_partial` exige `cedula_partial_pattern`. Regla asimétrica: la cédula
parcial **solo suma** confianza cuando coincide (mismo patrón, mismos dígitos),
nunca descarta cuando difiere. Supera a `cedula_masked`.

**Procedencia de PII: `_pii_provenance`** (construir pronto) en
`{cleartext, source_masked_lossy, source_hashed, source_encrypted}`. Invariante:
si no es `cleartext`, `cedula_hmac` puede ser `None` y `identity_kind != hmac`.

**Manejo de ausencias y campos desconocidos.** Campos `*_status` explícitos para
`foto` y `last_known_location` en
`{present, absent_source, removed_minor, removed_pii}`, para no confundir "la
fuente no lo tenía" con "se removió por protección". Captura de campos no mapeados
en `_unmapped` (escaneado por el detector de PII), manteniendo `extra="forbid"` en
la entidad: rigor sin fragilidad, sin violar "nada se descarta en silencio".

**Lista cerrada de `needs`** (hoy es texto libre): `agua`, `alimentos`,
`medicamentos`, `colchonetas`, `ropa`, `calzado`, `higiene`, `pañales`,
`leche_formula`, `generador`, `combustible`, `herramientas`, `voluntarios`,
`transporte`, `otro`.

**Elevación gobernada de `trust_tier`** (hoy inexistente): el parser no se
auto-eleva; una allowlist gobernada por el deployment sube el tier de una fuente
(ADR 0005).

**Campos documentados antes pero no presentes en el modelo** (parqueados aquí, sin
afirmarse como reales): Person `alternate_names`, `sex`, `source_url`; Event
`name`, `occurred_at`, `magnitude`, `depth_km`, `affected_states`, `external_ids`,
`status`; AcopioCenter `managing_org`, `capacity`, `current_load`, `contact_hmac`,
`contact_masked`, `last_verified_at`, ubicación estructurada. Se incorporan solo si
se deciden y se implementan, con su bump de versión.

---

## 11. Conformidad (fixtures)

Objetivo (no construido): un contribuidor valida su parser localmente con fixtures
`contract-v1.0/valid/*.json` y `contract-v1.0/invalid/*.json`, ejecutables con
`pytest`, sin desplegar. Los fixtures inválidos documentan el contrato de
cuarentena (§6). Hoy los tests de parser viven en `scrapers/tests/fixtures/` (§ver
Referencias).

---

## 12. Referencias

- `scrapers/models/person.py`, `event.py`, `acopio_center.py`, `_validators.py`.
- `scrapers/exporters/quarantine_exporter.py` (`REASON_CODES`).
- `scrapers/sanitizers/` (`pii_tokenizer.py`, `minor_protection.py`), `shared/hashing.py`.
- `docs/specs/db-scraper-contract.md` (contrato entidad a staging).
- `docs/adr/0004-versionado-de-contrato.md`, `docs/adr/0006-proteccion-pii-ingesta.md`.
- `docs/specs/person-dedup.md`, `docs/source_config.md`.
- issue #224 (punto 4), issue #231, issue #235.
