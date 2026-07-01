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
python -m scrapers.cli --verbose ingest --config <config> --source <id> --output-dir scrapers/runtime_output
```

Sin ese flag, el CLI no configura logging y los mensajes de progreso (páginas descargadas, entidades parseadas) no se muestran en ningún lado.

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
