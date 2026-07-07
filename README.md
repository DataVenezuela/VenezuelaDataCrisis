# VenezuelaCrisisData
> Limpiemos los registros en esta crisis!

Tras los terremotos del 24 de junio, miles de familias buscan a sus seres queridos en decenas de páginas distintas. La misma persona aparece en cuatro lugares con cuatro nombres distintos.  Este proyecto recolecta esos registros, los unifica en una base de datos limpia y deduplicada, y los expone via API para que cualquier dev pueda construir encima.

→ [Contribuir](CONTRIBUTING.md) · [Scraping](./scrapers/README.md) · [Pipeline de Limpieza](docs/pipeline.md) · [Reportar un problema](../../issues)

---

## Cómo funciona

```
scrapers/
├── cli.py                          # Punto de entrada CLI
├── config/
│   ├── sources.demo.yaml           # Demo offline con datos sintéticos
│   └── sources.custom.yaml         # Config thin de producción (uuid -> parser)
├── adapters/
│   ├── base.py                     # RawContent dataclass + AdapterProtocol
│   ├── api_adapter.py              # httpx, paginación, retry
│   ├── html_adapter.py             # BeautifulSoup
│   ├── playwright_adapter.py       # Playwright headless
│   ├── pdf_adapter.py              # pdfplumber
│   ├── local_file.py               # archivos locales
│   └── _shared.py                  # helpers compartidos (timestamp, hash, backoff)
├── parsers/
│   ├── base.py                     # ParserProtocol
│   ├── demo_text_parser.py         # Parser demo sintético → list[Person]
│   └── encuentralos_parser.py      # Parser concreto → list[Person]
├── models/
│   ├── person.py                   # Person (Pydantic)
│   ├── acopio_center.py            # AcopioCenter (Pydantic)
│   ├── event.py                    # Event (Pydantic)
│   └── source.py                   # SourceConfig
├── normalizers/
│   ├── text.py
│   ├── date.py
│   ├── location.py
│   ├── person.py
│   ├── phonetic.py                 # Double Metaphone / NYSIIS
│   └── nlp_extractor.py            # spaCy es_core_news_sm
├── sanitizers/
│   ├── pii_detector.py
│   ├── pii_redactor.py
│   └── pii_tokenizer.py
├── pipelines/
│   └── run_pipeline.py             # Orquestador principal
├── sources/
│   └── loader.py                   # Carga y valida el YAML de fuentes
├── validators/
│   ├── quality.py                  # confidence_score
│   └── source_validator.py
└── tests/
    ├── fixtures/                   # Datos sintéticos para tests
    └── test_*.py
Fuentes externas
      ↓
Adapters + Parsers + PII masking + Normalización
      ↓
raw_artifacts (bronze, Supabase)   ←── Quarantine DB   [en desarrollo]
      ↓
aportes (silver / staging)     ← inbox cross-source  [✅ en producción]
      ├─ materializer → persons / acopio_centers (silver 1:1) + events (catálogo)  [en desarrollo]
      │
      ↓  consolidation job: similaridad sobre aportes → aristas   [en desarrollo]
dedup_candidates (edges: ced: fuertes / phon: difusas)
      ↓  gold clustering (agrupa por relación, no por tiempo)
gold_entities / gold_members / gold_history (gold, fusión canónica)
      ↓  build job: gold publicado + aportes huérfanos (datos tipados de silver)
Cloudflare Worker + D1         ← API pública          [en desarrollo]
```

---

## Equipos

| Equipo | Responsabilidad |
|---|---|
| **Scrapers/Cleaners** | Adapters, parsers, PII masking, normalización, ingesta a staging |
| **DB/API** | Supabase schema, consolidation job, Cloudflare Worker + D1 |
| **Verification** | Revisar candidatos de duplicado, validar claims |

---

## Quickstart

```bash
git clone https://github.com/DataVenezuela/VZLA_DEDUP.git
cd VZLA_DEDUP
python3 -m venv .venv && source .venv/bin/activate
pip install -r scrapers/requirements.txt
pytest scrapers/tests
python -m scrapers.cli run --config scrapers/config/sources.demo.yaml
```

Para ver progreso real del pipeline (no solo el resultado final), agregá `--verbose` antes del subcomando:

```bash
# Obligatorio en producción para HMAC de cédulas
export PII_HMAC_SECRET="valor-secreto"
export PII_SALT="mismo-valor"

# Staging exporter (escritura directa a Supabase vía PostgREST)
export SUPABASE_URL="https://<proyecto>.supabase.co"
export SUPABASE_PUBLISHABLE_KEY="clave publishable (header apikey)"
export SUPABASE_INGEST_JWT="JWT firmado con rol scraper_ingest"

# Cuarentena (quarantine exporter, Issue #88) — POST /api/v1/quarantine
export QUARANTINE_API_KEY="x-api-key del scraper"
export QUARANTINE_BASE_URL="https://..."
```

Sin `PII_HMAC_SECRET`, el pipeline corre pero `cedula_hmac` queda `None`. Aceptable en CI offline; obligatorio en producción.

Sin `QUARANTINE_API_KEY` / `QUARANTINE_BASE_URL`, el quarantine exporter entra en
dry-run silencioso (no envía nada, no falla). En producción son obligatorias:
los registros no procesables deben preservarse, no perderse.

---

## Agregar una fuente nueva

La identidad de la fuente (url, name, keywords, tier) vive solo en la tabla
`sources` de la DB, nunca en el repo (ADR 0009). El repo solo referencia la
fuente por su `source_id` (UUID opaco) y le asigna un parser.

1. El mantenedor siembra la fila en `sources` (url, source_type, display_name,
   required_keywords, governed_tier, refresh_minutes, active) y comparte el
   `source_id` UUID generado.

2. Agregar la entrada thin (versionada) en `scrapers/config/sources.custom.yaml`:
   ```yaml
   - id: 00000000-0000-0000-0000-000000000000   # el source_id de la DB
     parser_asignado: mi_parser
     enabled: true
   ```
   Es un mapa `uuid -> parser`: no expone identidad (url/name/keywords viven en
   la DB), solo el UUID opaco y el parser, que ya aparecen en los logs.

3. Escribir el parser en `scrapers/parsers/mi_parser.py` implementando `ParserProtocol`.

4. Registrar el parser en `run_pipeline.py::_get_parser`.

5. Agregar tests en `scrapers/tests/test_mi_parser.py` con fixtures sintéticos.

Si la fuente no tiene parser todavía, dejar la entrada thin con `enabled: false`
(o `active: false` en la DB). Los registros sin parser van a **cuarentena**, no se descartan.

---

## Reglas de seguridad

Este proyecto maneja datos de personas desaparecidas. Las reglas no son negociables:
- No commitear datos reales bajo ninguna circunstancia
- No commitear nada de `scrapers/runtime_output/` (está en `.gitignore`)
- Cédulas y teléfonos se HMAC antes de cualquier persistencia, nunca en claro
- `cedula_hmac` = hex puro de 64 chars, sin prefijo (nunca `hmac_sha256:`)
- `trust_tier` = letras A/B/C/D en código de scrapers, nunca enteros
- La API pública nunca expone PII directa
- Los logs no incluyen PII
