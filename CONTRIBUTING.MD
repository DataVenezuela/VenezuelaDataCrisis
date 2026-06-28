# Cómo contribuir a VZLA_DEDUP

Gracias por querer ayudar. Este proyecto maneja información sensible en un contexto de emergencia, así que cada contribución debe priorizar tres cosas:

1. No exponer datos personales.
2. No romper trazabilidad.
3. No hacer cambios destructivos sin revisión.

Si tienes dudas, abre un Issue antes de implementar.

---

## Antes de empezar

Asegúrate de estar trabajando en una tarea clara.

Puedes contribuir en uno de estos equipos:

* `scrapers`: recolección, parsing, limpieza, normalización y exportación.
* `db-api`: base de datos, modelos, endpoints, cifrado e ingestión.
* `verification`: validación de registros, fuentes y claims.
* `docs`: documentación técnica, guías y contratos.

---

## Configuración local

Clona el repositorio:

```bash
git clone https://github.com/DataVenezuela/VZLA_DEDUP.git
cd VZLA_DEDUP
```

Crea el entorno virtual:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Instala dependencias de scrapers:

```bash
pip install -r scrapers/requirements.txt
```

Corre los tests antes de cambiar algo:

```bash
python -m pytest scrapers/tests
```

---

## Flujo de trabajo

No hagas push directo a la rama principal. Todo cambio debe ir por rama y Pull Request.

### 1. Actualiza tu rama base

Actualmente la rama base del repositorio es `master`.

```bash
git checkout master
git pull origin master
```

Si el proyecto migra oficialmente a `main`, usa `main` en lugar de `master`.

### 2. Crea una rama descriptiva

Usa el prefijo del área que estás tocando:

```bash
git checkout -b scrapers/nombre-descriptivo
git checkout -b db-api/nombre-descriptivo
git checkout -b verification/nombre-descriptivo
git checkout -b docs/nombre-descriptivo
git checkout -b fix/nombre-descriptivo
```

Ejemplos:

```bash
scrapers/parser-hospital-central
scrapers/normalize-person-status
db-api/person-sources-model
verification/criteria-trust-tier
docs/scraper-output-contract
fix/pii-redaction-phones
```

---

## Reglas para Pull Requests

Un PR debe resolver una sola cosa.

Evita mezclar cambios no relacionados. Por ejemplo, no mezcles un parser nuevo con cambios de base de datos, refactors grandes o documentación no relacionada.

Todo PR debe incluir:

* Qué cambiaste.
* Por qué era necesario.
* Cómo se prueba.
* Qué riesgo tiene.
* Si toca PII, cómo la protege.
* Ejemplo de salida usando datos ficticios, si aplica.

Checklist obligatorio:

```md
- [ ] Corrí los tests.
- [ ] No incluí datos reales.
- [ ] No incluí dumps, CSVs, PDFs, imágenes reales ni JSONL con información sensible.
- [ ] No logueo cédulas, teléfonos, direcciones, nombres completos sensibles ni secretos.
- [ ] El cambio mantiene trazabilidad hacia la fuente original.
- [ ] Actualicé documentación si cambié contratos, schemas o comportamiento esperado.
- [ ] El PR resuelve una sola cosa.
```

El PR necesita al menos una aprobación antes de mergear.

---

## Reglas de seguridad obligatorias

Este proyecto puede procesar datos de personas desaparecidas, heridas, fallecidas, menores de edad, familiares, hospitales y centros de ayuda.

Por eso:

* No subas datos reales al repo.
* No subas archivos de output generados por el pipeline.
* No subas screenshots con datos personales.
* No subas PDFs, CSVs, JSONL, bases de datos o imágenes reales.
* No guardes cédulas o teléfonos en claro.
* No imprimas PII en logs.
* No uses datos reales en tests.
* No hagas commits con claves, tokens o secretos.
* No expongas información de menores sin revisión explícita.

Los outputs locales deben ir en:

```bash
scrapers/runtime_output/
```

Esa carpeta debe permanecer ignorada por Git.

---

## Integración continua (CI)

Cada PR contra `master` corre automáticamente el workflow `.github/workflows/ci.yml` en GitHub Actions, que:

1. Instala `scrapers/requirements.txt` y corre `python -m pytest scrapers/tests`.
2. Bloquea el PR si agrega archivos `.csv`, `.jsonl`, `.pdf`, `.db`, `.sqlite` o `.xlsx`.
3. Escanea las líneas **nuevas** del diff buscando `cedula`, `telefono`, `password`, `secret`, `api_key`, `token` (la misma búsqueda de la sección de abajo, normalizando tildes antes de comparar), con una allowlist para los archivos donde esas palabras son nombres de campo legítimos del pipeline de tokenización de PII (`shared/hashing.py`, `scrapers/sanitizers/pii_tokenizer.py`, tests de PII, etc.). El log del check solo muestra qué palabra clave hizo match y en qué archivo, nunca el valor real de la línea, para no exponer datos sensibles si el match es real.

El check `ci` es requerido para mergear a `master`: si falla, el PR no se puede mergear hasta corregirlo.

Esto **no reemplaza** la revisión manual de abajo: el grep del CI es léxico (busca palabras, no entiende contexto), así que puede dar falsos positivos o falsos negativos. Sigue corriendo la revisión local antes de hacer push.

---

## Revisión rápida antes de commitear

Busca archivos de datos que no deberían entrar al repo:

```bash
find . -type f \( \
  -name "*.csv" -o \
  -name "*.jsonl" -o \
  -name "*.pdf" -o \
  -name "*.db" -o \
  -name "*.sqlite" -o \
  -name "*.xlsx" \
\)
```

Busca posibles datos sensibles o secretos:

```bash
grep -rIn \
  "cedula\|cédula\|telefono\|teléfono\|password\|secret\|api_key\|token" \
  --include="*.py" \
  --include="*.js" \
  --include="*.ts" \
  --include="*.json" \
  --include="*.yaml" \
  --include="*.yml" \
  .
```

Si aparece algo sensible, no hagas commit hasta revisarlo.

---

## Estándares para scrapers

Todo scraper nuevo debe cumplir estas reglas:

1. Debe tener una fuente declarada en configuración.
2. Debe poder correr con fixtures o datos ficticios.
3. Debe separar fetch, parsing, sanitización, normalización y export.
4. Debe producir salida compatible con el contrato JSONL del proyecto.
5. Debe usar `null` para datos desconocidos.
6. Debe usar fechas en UTC.
7. Debe usar enums controlados.
8. Debe proteger PII antes del export.
9. Debe tener tests.
10. No debe hacer merge destructivo de personas.

Regla importante:

> En personas, un falso merge puede hacer más daño que un duplicado. Si hay duda, se debe proponer revisión, no colapsar registros automáticamente.

---

## Datos de prueba

Los tests deben usar datos ficticios.

Permitido:

```text
JOSE LUIS PEREZ DEMO
V-****0000
https://example.org/demo
Hospital Demo
Centro de Acopio Demo
```

No permitido:

```text
Nombres reales de víctimas
Cédulas reales
Teléfonos reales
URLs reales con datos personales
Imágenes reales
PDFs reales con listados de personas
```

---

## Convenciones de código

Para Python:

* Usa nombres claros.
* Mantén funciones pequeñas.
* Evita lógica de negocio escondida en scripts sueltos.
* Agrega tests cuando agregues comportamiento nuevo.
* No silenciosamente ignores errores importantes.
* No uses prints para debugging permanente.
* Los logs no deben incluir PII.

Cuando agregues una fuente nueva, documenta al menos:

* Nombre de la fuente.
* Tipo de fuente.
* Parser usado.
* Entidad producida.
* `trust_tier`.
* Limitaciones conocidas.

---

## Estructura del repositorio

```text
api/          → FastAPI: endpoints, auth, rutas y modelos de API
scrapers/     → pipeline de scraping, limpieza, dedup y exportación
shared/       → configuración, hashing y utilidades compartidas
verification/ → herramientas y criterios de validación
docs/         → documentación técnica, contratos y guías
```

---

## Documentación técnica

Antes de cambiar contratos, schemas o comportamiento del pipeline, revisa la documentación en `docs/`.

Documentos recomendados:

```text
docs/pipeline.md
docs/schema.md
docs/scraper_contract.md
docs/source_config.md
docs/deduplication.md
docs/verification.md
```

Si alguno no existe todavía y tu cambio depende de eso, puedes abrir un PR de documentación primero.

---

## Cuándo abrir un Issue antes de codear

Abre un Issue si tu cambio:

* Cambia el contrato JSONL.
* Cambia enums.
* Cambia lógica de deduplicación.
* Cambia reglas de PII.
* Cambia estructura de base de datos.
* Agrega una fuente sensible.
* Agrega scraping sobre una fuente no pública.
* Puede afectar exposición pública de datos.

---

## Definition of Done

Una contribución está lista cuando:

```md
- [ ] El código corre localmente.
- [ ] Los tests pasan.
- [ ] No hay datos reales en el PR.
- [ ] No hay PII en logs, fixtures ni ejemplos.
- [ ] El output cumple el contrato esperado.
- [ ] Hay trazabilidad hacia la fuente.
- [ ] La documentación fue actualizada si hacía falta.
- [ ] El PR fue revisado y aprobado.
```

---

## ¿Dudas?

Abre un Issue en el repo o escribe en el canal del equipo correspondiente.

Si algo puede poner en riesgo a una persona, no lo implementes por tu cuenta. Pide revisión.
