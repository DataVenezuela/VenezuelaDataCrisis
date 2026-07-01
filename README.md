# VenezuelaCrisisData
> Limpiemos los registros en esta crisis!

Tras los terremotos del 24 de junio, miles de familias buscan a sus seres queridos en decenas de páginas distintas. La misma persona aparece en cuatro lugares con cuatro nombres distintos.  Este proyecto recolecta esos registros, los unifica en una base de datos limpia y deduplicada, y los expone via API para que cualquier dev pueda construir encima.

→ [Contribuir](CONTRIBUTING.md) · [Scraping](./scrapers/README.md) · [Pipeline de Limpieza](scrapers/PIPELINE.md) · [Reportar un problema](../../issues)

---

## Cómo funciona

```
scrapers/
├── cli.py                          # Punto de entrada CLI
├── config/
│   ├── sources.demo.yaml           # Demo offline con datos sintéticos
│   ├── sources.venezuela.starter.yaml
│   └── sources.custom.template.yaml
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
Raw DB (R2 + Supabase)    ←── Quarantine DB        [en desarrollo]
      ↓
Staging (aportes)              ← inbox cross-source  [✅ en producción]
      ↓  consolidation job                            [en desarrollo]
Canonical (persons / events / acopio_centers)
      ↓  build job
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

# Credenciales de dataVenezuela (staging exporter)
export DATAVZLA_API_KEY="x-api-key del scraper"
export DATAVZLA_BASE_URL="https://..."

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

1. Declararla en `scrapers/config/sources.venezuela.starter.yaml`:
   ```yaml
   - id: mi_fuente
     name: "Mi Fuente"
     url: "https://mi-fuente.org/api/personas"
     type: api_json
     parser_asignado: mi_parser
     trust_tier: C
     enabled: true
   ```

2. Escribir el parser en `scrapers/parsers/mi_parser.py` implementando `ParserProtocol`.

3. Registrar el parser en `run_pipeline.py::_get_parser`.

4. Agregar tests en `scrapers/tests/test_mi_parser.py` con fixtures sintéticos.

Si la fuente no tiene parser todavía, declararla con `enabled: false`. Los registros sin parser van a **cuarentena**, no se descartan.

Sin `QUARANTINE_API_KEY` / `QUARANTINE_BASE_URL`, el quarantine exporter entra en
dry-run silencioso (no envía nada, no falla). En producción son obligatorias:
los registros no procesables deben preservarse, no perderse.

---

## Reglas de seguridad

Este proyecto maneja datos de personas desaparecidas. Las reglas no son negociables:
- No commitear datos reales bajo ninguna circunstancia
- Cédulas y teléfonos se HMAC antes de cualquier persistencia, nunca en claro
- `cedula_hmac` = hex puro de 64 chars, sin prefijo
- La API pública nunca expone PII directa
- `trust_tier` = letras A/B/C/D en código de scrapers, nunca enteros

---

## Reglas de seguridad

- No commitear datos reales bajo ninguna circunstancia
- No commitear nada de `scrapers/runtime_output/` (está en `.gitignore`)
- `cedula_hmac` = hex puro 64 chars, nunca con prefijo `hmac_sha256:`
- `trust_tier` = letras A/B/C/D, nunca enteros en este módulo
- Los logs no incluyen PII
