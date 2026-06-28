# Cómo contribuir a VZLA_DEDUP

Gracias por querer ayudar. Este proyecto maneja información sensible en un contexto de emergencia. Cada contribución debe priorizar tres cosas:

1. No exponer datos personales.
2. No romper trazabilidad.
3. No hacer cambios destructivos sin revisión.

Si tienes dudas, abre un Issue antes de implementar.

---

## Antes de empezar

Revisa el mapa de dependencias en el README. Muchos issues están bloqueados, agarrar uno bloqueado sin resolver sus dependencias primero es trabajo perdido.

Puedes contribuir en uno de estos equipos:

- `scrapers`: adapters, parsers, PII masking, normalización, staging exporter
- `db-api`: Supabase schema, consolidation job, Cloudflare Worker + D1, build job
- `verification`: revisión de candidatos de duplicado, validación de claims
- `DevSecOps`: CI/CD config.
- `docs` — documentación técnica, contratos, guías

---

## Configuración local

```bash
git clone https://github.com/DataVenezuela/VZLA_DEDUP.git
cd VZLA_DEDUP
python3 -m venv .venv
source .venv/bin/activate
pip install -r scrapers/requirements.txt
pytest scrapers/tests   # debe pasar antes de tocar nada
```

---

## Flujo de trabajo

No hagas push directo a `master`. Todo cambio va por rama y Pull Request.

```bash
git checkout master && git pull origin master
git checkout -b scrapers/nombre-descriptivo
```

Prefijos de rama por equipo:

```
scrapers/parser-encuentralos
db-api/staging-exporter
verification/criteria-trust-tier
docs/pipeline-4-layers
fix/pii-hmac-prefix
```

---

## Reglas para Pull Requests

Un PR debe resolver un issue. No mezcles un parser nuevo con cambios de schema o refactors no relacionados.

Todo PR debe describir:
- Qué cambiaste y por qué
- Cómo se prueba
- Qué riesgo tiene
- Si toca PII, cómo la protege

Checklist mental:

```md
- [ ] Corrí los tests (`pytest scrapers/tests`).
- [ ] No incluí datos reales (personas, cédulas, teléfonos, PDFs, CSVs, JSONL).
- [ ] No logueo PII en ningún mensaje de log o error.
- [ ] El cambio mantiene trazabilidad hacia la fuente original.
- [ ] Actualicé documentación si cambié contratos, schemas o comportamiento.
- [ ] El PR resuelve una sola cosa.
- [ ] Si agrego un parser nuevo, los registros sin parser van a cuarentena, no al basura.
```

Requiere al menos una aprobación antes de mergear. Branch protection activo: la rama debe estar al día con `master` antes de mergear.

---

## Dónde aterriza cada cosa

El pipeline **no escribe JSONL en disco**. El destino de las entidades procesadas es staging en Supabase vía `POST /api/v1/dedup/*`.

```
Adapter → Parser → PII → Normalización → staging exporter → aportes (Supabase)
                                                   ↓
                                         consolidation job (cada 20 min)
                                                   ↓
                                         persons / events / acopio_centers
```

Si no hay parser para una fuente, el registro va a **cuarentena**. No se descarta, no se procesa con un fallback genérico. La cuarentena es trazable y revisable.

---

## Estándares para parsers

Todo parser nuevo debe:
1. Implementar `ParserProtocol` (`scrapers/parsers/base.py`).
2. Producir `list[Person | AcopioCenter | Event]` — entidades tipadas, nunca texto crudo.
3. Aplicar HMAC a cédulas **antes** de crear la entidad, usando `shared/hashing.identity_token`.
4. Usar `trust_tier` en letra (`A`/`B`/`C`/`D`), no entero.
5. Dejar campos desconocidos como `None`, nunca inventar valores.
6. Tener tests con fixtures sintéticos en `scrapers/tests/`.
7. Estar declarado en el YAML de fuentes con `parser_asignado`.

Documentar al menos:
- Nombre y URL de la fuente
- Tipo de fuente (`api_json`, `html_static`, `webapp_js`, `pdf`)
- Entidad que produce (`Person`, `AcopioCenter`, `Event`)
- `trust_tier` asignado y justificación
- Limitaciones conocidas del parser

---

## Reglas de seguridad

Este proyecto puede procesar datos de personas desaparecidas, heridas, fallecidas, menores de edad y sus familiares.
- No subas datos reales al repo bajo ninguna circunstancia
- No subas outputs generados por el pipeline (ni JSONL, ni CSVs, ni nada de `runtime_output/`)
- No guardes cédulas ni teléfonos en claro, ni siquiera en tests
- No imprimas PII en logs
- No expongas información de menores sin revisión explícita
- `cedula_hmac` = hex puro de 64 chars, **sin prefijo** `hmac_sha256:`
- `trust_tier` en scrapers = letras A/B/C/D, nunca enteros

---

## Integración continua

Cada PR contra `master` corre `ci.yml` automáticamente:
1. `pytest scrapers/tests`, bloqueante
2. `ruff check .`, bloqueante
3. Bloqueo de archivos `.csv`, `.jsonl`, `.pdf`, `.db`, `.sqlite`, `.xlsx`, bloqueante
4. Scan de keywords PII/secretos en el diff, bloqueante (con allowlist para archivos legítimos)
5. `gitleaks`, informativo (no bloqueante aún)
6. `bandit` y `pip-audit`, informativo

Corre localmente antes de hacer push:

```bash
pytest scrapers/tests
pip install ruff && ruff check .
```

---

## Datos de prueba

Los tests deben usar exclusivamente datos ficticios:

```text
JOSE LUIS PEREZ DEMO
V-****0000
https://example.org/demo
Hospital Demo
Centro de Acopio Demo
```

Nunca: nombres reales, cédulas reales, teléfonos reales, URLs con datos personales, imágenes o PDFs reales.

---

## Convenciones de código

- Identificadores en inglés, comentarios en español
- Los comentarios explican **por qué**, no **qué**
- Funciones pequeñas con una sola responsabilidad
- Nunca silencies errores importantes
- No prints de debugging permanentes
- Los logs no incluyen PII

---

## Cuándo abrir un Issue antes de codear

Abre un Issue si tu cambio:
- Modifica el schema de entidades (`Person`, `AcopioCenter`, `Event`)
- Cambia enums o el contrato de campos
- Toca lógica de PII o hashing
- Agrega o modifica reglas de deduplicación
- Cambia la estructura de la BD
- Agrega una fuente con datos sensibles o privados

---

## Definition of Done

```md
- [ ] El código corre localmente.
- [ ] Los tests pasan.
- [ ] No hay datos reales en el PR.
- [ ] No hay PII en logs, fixtures ni ejemplos.
- [ ] El output cumple el contrato esperado.
- [ ] Hay trazabilidad hacia la fuente original.
- [ ] La documentación fue actualizada si hacía falta.
- [ ] El PR fue revisado y aprobado.
```

---

## Variables de entorno para desarrollo local

El pipeline funciona sin credenciales de producción. Sin `PII_HMAC_SECRET`, `cedula_hmac` queda `null`, comportamiento esperado en CI offline.

Para correr los tests completos localmente:
```bash
cp .env.example .env   # valores dummy, no reales
```

Las credenciales de producción (Supabase, Cloudflare, PII_HMAC_SECRET real) solo las configura el admin del repo en GitHub Settings → Secrets.  No las pidas por Telegram ni las pongas en ningún archivo del repo.

---

## ¿Dudas?

Abre un Issue o escribe en el canal del equipo correspondiente.

Si algo puede poner en riesgo a una persona, no lo implementes por tu cuenta. Pide revisión.