# VZLA_DEDUP — Scraper Contract

Este documento define el contrato que deben cumplir los parsers de VZLA_DEDUP.

El objetivo es que todos los parsers produzcan entidades tipadas consistentes, seguras y listas para ser enviadas a staging sin que cada fuente invente su propio formato.

---

## 1. Alcance

Este contrato aplica a cualquier parser o módulo que produzca entidades para VZLA_DEDUP.

Cubre:
- Entidades que debe producir un parser
- Campos por entidad y sus tipos
- Enums permitidos
- Reglas de PII
- Convenciones de `null`

No cubre:
- Endpoints de API
- Schema de base de datos
- Reglas del consolidation job
- Reglas de verificación humana

---

## 2. Principio general

Un parser convierte el `RawContent` de un adapter en `list[Person | AcopioCenter | Event]`.

El parser no persiste nada. No hace requests adicionales. No toma decisiones de dedup.

Si un valor no existe o no puede determinarse, usa `None`. Nunca inventes valores. Nunca descartes un registro porque tenga campos ausentes.

---

## 3. Flujo de un parser

```
RawContent (del adapter)
  ↓
Parser.parse(raw) → list[Person | AcopioCenter | Event]
```

El parser debe:
1. Extraer campos del raw según la estructura de su fuente
2. Aplicar HMAC a cédulas **antes** de crear la entidad (`shared/hashing.identity_token`)
3. Mapear status al enum correcto (ver §6)
4. Usar `trust_tier` en letra: `A`, `B`, `C` o `D`
5. Dejar como `None` cualquier campo no disponible

Lo que va a **cuarentena** en vez de al parser:
- Fuente sin parser asignado
- PII no redactable automáticamente
- Schema inválido o inesperado
- PDF sin texto extraíble

---

## 4. Entidades

### Person

```python
Person(
    full_name="JOSE LUIS PEREZ DEMO",          # str, obligatorio
    cedula_hmac="3b4c9e...1f9e0",              # str | None, 64-hex sin prefijo
    cedula_masked="V-****5821",                # str | None, máx 15 chars
    age_range={"min": 30, "max": 40},          # dict | None
    sex="M",                                   # "M" | "F" | None
    is_minor=False,                            # bool | None — OBLIGATORIO declarar
    last_known_location="El Tocuyo, Lara",     # str | None
    status="missing",                          # ver §6
    verification_status="unverified",          # ver §6
    trust_tier="C",                            # "A"|"B"|"C"|"D"
    source_url="https://encuentralos.org/12",  # str | None
    alternate_names=["JOSELO PEREZ"],          # list[str] | None
    event_id="uuid-v4",                        # str | None — FK al evento
    nota="observaciones adicionales",          # str | None
    foto="https://...",                        # str | None — URL, sin descargar
    fuente="encuentralos.tecnosoft.dev",       # str, obligatorio
)
```

**`is_minor` es obligatorio declarar.** Si se desconoce, usar `None`. Si se sabe que es menor, `True`. No omitir el campo.

**`telefono_contacto` de familiares se descarta explícitamente.** Nunca persistir contacto de terceros.

### AcopioCenter

```python
AcopioCenter(
    name="Centro de Acopio Polideportivo San Felipe",
    location={
        "raw": "Polideportivo Municipal, San Felipe, Yaracuy",
        "estado": "Yaracuy",
        "municipio": "San Felipe",
        "lat": 10.3401,
        "lng": -68.7456,
    },
    status="active",                           # "active" | "inactive" | "unknown"
    needs=["agua", "alimentos", "medicamentos"],
    last_verified_at="2026-06-26T08:00:00Z",
    managing_org="Cruz Roja Venezuela",
    contact_hmac="9f1c3e...",                  # str | None — HMAC del teléfono
    contact_masked="+58 412 ***7834",          # str | None
    capacity=400,                              # int | None
    current_load=283,                          # int | None
    confidence_score=0.85,                     # float 0.0–1.0
    trust_tier="B",
    event_id="uuid-v4",
    fuente="acopio-ve.org",
)
```

**`needs`** acepta solo keywords normalizadas:
`agua` · `alimentos` · `medicamentos` · `colchonetas` · `ropa` · `calzado` · `higiene` · `pañales` · `leche_formula` · `generador` · `combustible` · `herramientas` · `voluntarios` · `transporte` · `otro`

El parser mapea texto libre al keyword antes de crear la entidad. Valor desconocido → `"otro"`.

### Event

```python
Event(
    name="Terremoto Yaracuy 24-06-2026",
    event_type="earthquake",                   # ver §6
    occurred_at="2026-06-24T14:32:00Z",        # ISO 8601 UTC
    affected_states=["Yaracuy", "Lara"],       # list[str] | None
    magnitude=7.3,                             # float | None
    depth_km=12.5,                             # float | None
    status="active",                           # "active" | "closed" | "unknown"
    external_ids={"usgs": "us7000n4xy"},       # dict | None
    trust_tier="A",
    fuente="usgs.gov",
)
```

---

## 5. Convenciones globales

| Decisión | Valor |
|---|---|
| Fechas | ISO 8601 UTC con `Z` |
| Nulos | `None` en Python, `null` en JSON. Nunca `""`, `"N/A"`, `0` |
| IDs | UUID v4 |
| `trust_tier` | Letras `A`/`B`/`C`/`D` — nunca enteros en el scraper |
| `cedula_hmac` | Hex puro 64 chars — nunca con prefijo `hmac_sha256:` |
| Booleanos | `True`/`False` — nunca `1`/`0`/`"Si"` |

---

## 6. Enums

### `Person.status`
```
missing   — desaparecido/a
found     — encontrado/a
injured   — herido/a
deceased  — fallecido/a
unknown   — se desconoce
```

### `Person.verification_status`
```
unverified   — sin verificar
verified     — verificado
disputed     — en disputa
```

### `Event.event_type`
```
earthquake
flood
landslide
other
```

### `trust_tier` (en scrapers)
```
A — fuente oficial (gobierno, USGS, Cruz Roja)
B — ONG verificada o medio de comunicación establecido
C — voluntario/comunidad con ownership visible
D — fuente anónima o sin verificar
```

---

## 7. PII — reglas no negociables

1. Calcular `cedula_hmac` **antes** de crear la entidad, usando `shared/hashing.identity_token(cedula, secret)`.
2. El campo `cedula` crudo **no** entra al modelo. Nunca.
3. `cedula_hmac` = hex puro 64 chars. Sin el prefijo `hmac_sha256:`.
4. Teléfonos de contacto de familiares se descartan. Si la fuente los expone, no los persistas.
5. El prefijo de nacionalidad (V/E) es parte del identificador canónico: `"V12345678"` y `"E12345678"` producen HMAC distintos.

---

## 8. Tests obligatorios para un parser nuevo

Cada parser nuevo debe incluir tests que verifiquen:

- Un registro completo produce la entidad correcta
- Un registro sin cédula produce `cedula_hmac=None` sin lanzar excepción
- El mapeo de todos los valores de status al enum correcto
- Un registro con status desconocido produce `status="unknown"`
- Los campos ausentes en el raw producen `None` en la entidad (no excepción)
- `cedula_hmac` es hex de 64 chars sin prefijo cuando hay cédula

Los tests usan fixtures en `scrapers/tests/fixtures/`. Nunca datos reales.