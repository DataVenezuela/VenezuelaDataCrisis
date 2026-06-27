# VZLA_DEDUP
Limpiemos los registros en esta crisis.

Venezuela necesita una base de datos centralizada y de confianza de personas desaparecidas, necesidades activas e infraestructura afectada. Hay docenas de páginas con información valiosa pero fragmentada, duplicada y sin verificar. Este proyecto la consolida, la deduplica y la expone via API segura para que cualquier desarrollador pueda construir encima soluciones humanitarias.

→ [Documentación](https://docs.google.com/document/d/1RzTa_bjouoZrjoS-fo1ojqUxjaTYy_w5Fg6Ad3fX8TU/edit?usp=sharing) · [Contribuir](./CONTRIBUTING.md) · [Reportar un problema](../../issues)

---

## El problema

Miles de personas suben datos relevantes a distintas páginas, pero están todos descentralizados. Esto genera duplicados, datos obsoletos y registros sin verificar. Cualquier dev que quiera construir algo útil hoy no tiene una fuente limpia de donde partir.

El reto es de criterio:
- ¿Cómo sabemos que dos registros son la misma persona?
- ¿Cómo descartamos datos sin cometer un error que cueste una vida?
- ¿Cómo verificamos que lo que dice una página corresponde con la realidad?

Este proyecto ataca esas preguntas en 6 etapas:
1. **Recolección**: scrapers contra páginas, APIs y archivos manuales.
2. **Serialización**: estandarizar texto, imágenes y formatos distintos.
3. **Protección**: hashear cédulas y teléfonos usando derivación de clave pesada (PBKDF2) antes de almacenar.
4. **Deduplicación**: detectar y colapsar registros duplicados utilizando fonética española y similitud de nombres.
5. **Almacenamiento**: base de datos relacional con soporte concurrente WAL y soporte multibase (SQLite/Postgres).
6. **Verificación**: corroborar claims contra fuentes externas y realidad física.

---

## Estado del Proyecto

El backend de base de datos, el pipeline de deduplicación y la API REST de consulta están **completamente desarrollados, securizados y probados**.

```
api/                        → Servidor REST FastAPI (Completado)
│   ├── auth.py             → Autenticación X-API-Key con secrets.compare_digest
│   ├── main.py             → Configuración del servidor y middlewares
│   ├── routes/             → Endpoints de consulta, edición y sincronización
│   └── tests/              → Pruebas integradas de API y escaneo de vulnerabilidades DAST
scrapers/                   → Pipeline principal (Completado)
│   ├── cli.py              → Punto de entrada CLI
│   ├── config/             → Fuentes de datos configurables (USGS GeoJSON, ReliefWeb, etc.)
│   ├── pipelines/          → Orquestador del flujo
│   ├── fetchers/           → HTTP + archivos locales
│   ├── extractors/         → Extractores HTML, RSS y Parsers JSON estructurados
│   ├── sanitizers/         → Detección de PII y Tokenización fuerte con PBKDF2 (600k iteraciones)
│   ├── dedup/              → Deduplicación difusa (Metaphone-ES + Jaro-Winkler)
│   └── tests/              → Pruebas unitarias de deduplicación, fonética y sanitizadores
shared/                     → Configuración y storage compartido (Completado)
│   ├── config.py           → Variables de entorno y secretos
│   ├── storage.py          → Engine de SQLAlchemy (SQLite WAL / Postgres)
│   └── sync_db.py          → Cargador/Sincronizador masivo de JSONL a DB relacional
verification/               → Módulo de verificación en campo (próximamente)
```

---

## Quickstart

### 1. Instalación y Dependencias

Clona el repositorio e instala los paquetes necesarios en un entorno virtual de Python:

```bash
git clone https://github.com/DataVenezuela/VZLA_DEDUP.git
cd VZLA_DEDUP
python -m venv .venv
source .venv/bin/activate  # En Windows: .venv\Scripts\activate
pip install -r scrapers/requirements.txt
pip install -r api/requirements.txt
```

### 2. Ejecutar el Pipeline de Scrapers

Descarga y procesa los datos de prueba aplicando el pipeline de normalización, sanitización de PII y deduplicación difusa:

```bash
# Ejecutar con la configuración de prueba demo
python -m scrapers.cli run --config scrapers/config/sources.demo.yaml
```

Los outputs deduplicados se exportarán en formato JSONL a la carpeta `scrapers/runtime_output/sanitized/`.

### 3. Sincronizar con la Base de Datos

Carga los documentos y reclamos consolidados en el pipeline dentro de la base de datos relacional:

```bash
python -m shared.sync_db
```

Esto generará la base de datos local `vzla_dedup.db` con soporte concurrente de escritura activando el modo WAL.

### 4. Levantar la API de Consulta

Inicia el servidor FastAPI en modo reload para desarrollo en el puerto 8000:

```bash
uvicorn api.main:app --reload
```

Puedes interactuar con los endpoints ingresando a la documentación interactiva de Swagger: [http://localhost:8000/docs](http://localhost:8000/docs).

### 5. Correr las Pruebas

Ejecuta la suite completa de pruebas unitarias, de integración y de seguridad DAST:

```bash
pytest scrapers/tests api/tests
```

---

## Stack de Tecnologías

**Scrapers & Deduplicación**
- Python 3 + `requests` + `BeautifulSoup` + `PyYAML`.
- **Deduplicación Difusa**: Procesamiento fonético nativo con **Metaphone-ES** y cálculo de similitud tipográfica **Jaro-Winkler**.
- **Sanitización de PII**: Regex de detección y protección mediante **Key Stretching (PBKDF2-HMAC SHA-256 con 600,000 iteraciones)**, sustituyendo cédulas y teléfonos por tokens hashes irreversibles correlacionables.

**Persistencia & API**
- **FastAPI** + **Uvicorn** + **Pydantic** para rutas REST rápidas con validación estricta de esquemas.
- **SQLAlchemy ORM** compatible con SQLite local (para hackathon/offline) y PostgreSQL externa (para producción).
- **Seguridad**:
  - Comparación en tiempo constante (`secrets.compare_digest`) para cabeceras de API Key, evitando Timing Attacks.
  - Modo **SQLite WAL** (Write-Ahead Logging) habilitado en el motor de base de datos para lecturas concurrentes sin bloqueos transaccionales.
  - Consultas SQL 100% parametrizadas a nivel de ORM para inmunidad ante SQL Injection.

---

## Contribuciones

Lee [CONTRIBUTING.md](./CONTRIBUTING.md) antes de empezar. Reglas rápidas:
1. Crea una rama desde main: `git checkout -b scrapers/lo-que-vas-a-hacer`
2. Haz tus cambios y corre `pytest scrapers/tests api/tests`
3. Abre un Pull Request; necesita 1 aprobación antes de fusionarse.
4. **NO** subas datos reales, dumps ni archivos con PII visible al repositorio.

---

## Licencia

MIT License · 2026 · DataVenezuela