# Cómo contribuir a VZLA_DEDUP

Gracias por unirte! Este documento tiene todo lo que necesitas para empezar.

## Antes de empezar

1. Asegúrate de estar asignado a tu equipo en la organización de GitHub (`scrapers`, `db-api`, o `validation`).
2. Clona el repo y crea tu entorno:

```bash
git clone https://github.com/DataVenezuela/VZLA_DEDUP.git
cd VZLA_DEDUP
python3 -m venv .venv
source .venv/bin/activate
pip install -r scrapers/requirements.txt
```

3. Verifica que los tests pasan antes de tocar nada:

```bash
pytest scrapers/tests
```

## Flujo de trabajo

**No hagas push directo a `main`.** Todo cambio va por rama + Pull Request.

### 1. Crea tu rama desde main

Usa el prefijo de tu equipo:

```bash
git checkout main
git pull
git checkout -b scrapers/nombre-descriptivo
# o
git checkout -b db-api/nombre-descriptivo
# o
git checkout -b validation/nombre-descriptivo
```

Ejemplos válidos: `scrapers/fix-paginacion-encuentralos`, `db-api/esquema-postgres`, `validation/criterios-verificacion`

### 2. Haz tus cambios

- Un PR = una cosa. No mezcles features distintos en la misma rama.
- Corre los tests antes de hacer push: `pytest scrapers/tests`
- No commitees datos reales, dumps, CSVs, PDFs ni imágenes con información de personas.

### 3. Abre el Pull Request

- Título descriptivo: qué hace el cambio.
- Describe brevemente qué cambiaste y por qué.
- Asigna al menos 1 reviewer de tu equipo.
- El PR no se mergea sin 1 aprobación.

## Reglas de seguridad (obligatorias)

Antes de cada commit, verifica:

```bash
# Que no haya archivos de datos
find . -type f \( -name "*.csv" -o -name "*.jsonl" -o -name "*.pdf" -o -name "*.db" \)

# Que no haya datos sensibles en el código
grep -rIn "cedula\|cédula\|telefono\|teléfono\|password\|secret\|api_key" --include="*.py" .
```

Si aparece algo, no hagas commit. Los outputs del pipeline van a `scrapers/runtime_output/` que está en `.gitignore`.

## Estructura del repo

```
api/          → FastAPI: endpoints, auth, rutas
scrapers/     → pipeline de scraping, limpieza y dedup
shared/       → config, hashing, storage compartido
verification/ → herramientas del equipo de validación
```

## ¿Dudas?

Abre un Issue en el repo o escribe en el canal de tu equipo en Telegram.