# VZLA_DEDUP
Limpiemos los registros en esta crisis.

Venezuela necesita una base de datos centralizada y confiable de personas desaparecidas, necesidades activas e infraestructura afectada. Hay docenas de páginas con información valiosa pero fragmentada, duplicada y sin verificar. Este proyecto la consolida y la expone via API para que cualquier dev pueda construir encima.

→ [Documentación](https://docs.google.com/document/d/1RzTa_bjouoZrjoS-fo1ojqUxjaTYy_w5Fg6Ad3fX8TU/edit?usp=sharing) · [Contribuir](./CONTRIBUTING.md) · [Reportar un problema](../../issues)

---

## El problema

Miles de personas suben datos relevantes a distintas páginas, pero están todos descentralizados. Esto genera duplicados, datos obsoletos y registros sin verificar. Cualquier dev que quiera construir algo útil hoy no tiene una fuente limpia de donde partir.

El reto es de criterio:

- Cómo sabemos que dos registros son la misma persona?
- Cómo descartamos datos sin cometer un error que cueste una vida?
- Cómo verificamos que lo que dice una página corresponde con la realidad?

Este proyecto ataca esas preguntas en 6 etapas:

1. **Recolección**: scrapers contra páginas, APIs y archivos manuales
2. **Serialización**: estandarizar texto, imágenes y formatos distintos
3. **Protección**: hashear cédulas y datos sensibles antes de almacenar
4. **Deduplicación**: detectar y colapsar registros duplicados
5. **Almacenamiento**: base de datos cifrada con trazabilidad completa
6. **Verificación**: corroborar claims contra fuentes externas y realidad física

---

## Equipos

| Equipo | Responsabilidad |
|---|---|
| **Scrapers/Cleaners** | Recolectar, normalizar, sanear y deduplicar datos |
| **DB/API** | Base de datos, cifrado, endpoints para devs externos |
| **Verification** | Contactar fuentes externas, validar claims en vivo |

¿Quieres unirte? Escribe en el canal de Telegram o abre un Issue.

---

## Arquitectura del pipeline

El siguiente diagrama muestra el flujo completo de datos: desde las fuentes originales hasta la base de datos, pasando por adapters, parsers, modelos tipados y los módulos de limpieza compartida.

![Arquitectura del pipeline](./docs/pipeline.svg)

> Fuente editable: [`docs/pipeline.dot`](./docs/pipeline.dot) (Graphviz)

---

## Mapa de dependencias

El siguiente diagrama muestra las dependencias entre los issues activos del proyecto. Úsalo como referencia para entender qué bloquea qué antes de empezar a trabajar.

![Mapa de dependencias de issues](./docs/issues_graph.svg)

> Fuente editable: [`docs/issues_graph.dot`](./docs/issues_graph.dot) (GraphViz)

---

## Estado actual

El pipeline de scrapers está operativo con datos. La API y la capa de almacenamiento están en construcción.

```
api/                        → FastAPI (en construcción)
│   ├── auth.py
│   ├── main.py
│   └── routes/
scrapers/                   → Pipeline principal
│   ├── cli.py              → Punto de entrada CLI
│   ├── config/             → Fuentes de datos configurables
│   ├── pipelines/          → Orquestador
│   ├── fetchers/           → HTTP + archivos locales
│   ├── extractors/         → HTML, JSON, RSS, texto
│   ├── sanitizers/         → Detección y redacción de PII
│   ├── dedup/              → Deduplicación por fingerprint
│   ├── outputs/            → Exportación JSONL
│   └── tests/
shared/                     → Config, hashing y storage compartido
verification/               → (próximamente)
```

---

## Quickstart

```bash
git clone https://github.com/DataVenezuela/VZLA_DEDUP.git
cd VZLA_DEDUP
python3 -m venv .venv
source .venv/bin/activate
pip install -r scrapers/requirements.txt

# Correr demo offline con datos locales
python -m scrapers.cli run --config scrapers/config/sources.demo.yaml

# Correr tests
pytest scrapers/tests
```

---

## Stack

**Scrapers/Cleaners**
- Python: `requests`, `BeautifulSoup`, `PyYAML`
- Detección de PII con regex + HMAC para correlación de cédulas

**DB/API**
- PostgreSQL
- FastAPI (Python)
- SQLAlchemy

**Deduplicación**
- Fingerprint SHA-256 por contenido normalizado
- Jaro-Winkler + Metaphone-ES para nombres (en desarrollo)

---

## Contribuciones

Lee [CONTRIBUTING.md](./CONTRIBUTING.md) antes de empezar. La versión corta:

1. Crea una rama desde main: `git checkout -b scrapers/lo-que-vas-a-hacer`
2. Haz tus cambios y corre `pytest scrapers/tests`
3. Abre un Pull Request, necesita 1 aprobación antes de mergear a main
4. No commitees datos reales, dumps ni archivos con PII

---

## Seguridad y datos personales

Este proyecto maneja información de personas desaparecidas. Las reglas son estrictas:

- Cédulas y teléfonos se redactan o se HMAC antes de exportar, nunca en claro
- Los outputs del pipeline van a `scrapers/runtime_output/` (en `.gitignore`), nunca al repo
- Existe un mecanismo de eliminación de datos a pedido

---

MIT License · 2026 · DataVenezuela