# VenezuelaCrisisData
> Limpiemos los registros en esta crisis!

Tras los terremotos del 24 de junio, miles de familias buscan a sus seres queridos en decenas de páginas distintas. La misma persona aparece en cuatro lugares con cuatro nombres distintos.  Este proyecto recolecta esos registros, los unifica en una base de datos limpia y deduplicada, y los expone via API para que cualquier dev pueda construir encima.

→ [Contribuir](CONTRIBUTING.md) · [Scraping](./scrapers/README.md) · [Pipeline de Limpieza](scrapers/PIPELINE.md) · [Reportar un problema](../../issues)

---

## Cómo funciona

```
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

MIT License · 2026 · DataVenezuela