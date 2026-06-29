# VZLA_DEDUP — Módulo `scrapers`

Este paquete implementa el pipeline de recolección: fetch de fuentes externas, parsing a entidades tipadas, enmascaramiento de PII, normalización y envío a staging en Supabase.

**El pipeline no escribe archivos locales.** El destino final es la tabla `aportes` en Supabase vía el staging exporter.

---

## Estructura

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
```

---

## Instalación

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r scrapers/requirements.txt
```

---

## Tests

```bash
pytest scrapers/tests
```

Los tests deben pasar antes de cualquier cambio y después de él.

---

## Correr el pipeline

```bash
# Demo offline (no hace requests reales, no envía a Supabase)
python -m scrapers.cli run --config scrapers/config/sources.demo.yaml

# Limitar registros por fuente
python -m scrapers.cli run --config scrapers/config/sources.demo.yaml --limit 10

# Validar config de fuentes
python -m scrapers.cli validate --config scrapers/config/sources.demo.yaml
```

En producción, el pipeline corre via `scrapers.yml` (GitHub Actions, cron horario).

---

## Variables de entorno

```bash
# Obligatorio en producción para HMAC de cédulas
export PII_HMAC_SECRET="valor-secreto"
export PII_SALT="mismo-valor"

# Credenciales de dataVenezuela (staging exporter)
export DATAVZLA_API_KEY="x-api-key del scraper"
export DATAVZLA_BASE_URL="https://..."
```

Sin `PII_HMAC_SECRET`, el pipeline corre pero `cedula_hmac` queda `None`. Aceptable en CI offline; obligatorio en producción.

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

---

## Reglas de seguridad

- No commitear datos reales bajo ninguna circunstancia
- No commitear nada de `scrapers/runtime_output/` (está en `.gitignore`)
- `cedula_hmac` = hex puro 64 chars, nunca con prefijo `hmac_sha256:`
- `trust_tier` = letras A/B/C/D, nunca enteros en este módulo
- Los logs no incluyen PII