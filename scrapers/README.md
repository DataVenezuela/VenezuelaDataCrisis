# VZLA_DEDUP
> Limpiemos los registros en esta crisis!

Tras los terremotos del 24 de junio, miles de familias buscan a sus seres queridos en decenas de páginas distintas. La misma persona aparece en cuatro lugares con cuatro nombres distintos.

Este proyecto recolecta esos registros, los unifica en una base de datos limpia y deduplicada, y los expone via API para que cualquier dev pueda construir encima.

→ [Contribuir](./CONTRIBUTING.MD) · [Pipeline técnico](./docs/pipeline.md) · [Reportar un problema](../../issues)

---

## Cómo funciona

```
Fuentes externas
      ↓
Adapters + Parsers + PII masking + Normalización
      ↓
Raw DB (R2 + Supabase)    ←── Quarantine DB
      ↓
Staging (aportes)              ← inbox cross-source
      ↓  consolidation job
Canonical (persons / events / acopio_centers)
      ↓  build job
Cloudflare Worker + D1         ← API pública
```

El pipeline no escribe archivos locales. El destino es staging en Supabase. La dedup real ocurre en el consolidation job, donde convergen datos de todas las fuentes.

Documentación técnica completa en [`docs/pipeline.md`](./docs/pipeline.md).

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

---

## Reglas de seguridad

Este proyecto maneja datos de personas desaparecidas. Las reglas no son negociables:

- No commitear datos reales bajo ninguna circunstancia
- Cédulas y teléfonos se HMAC antes de cualquier persistencia, nunca en claro
- `cedula_hmac` = hex puro de 64 chars, sin prefijo
- La API pública nunca expone PII directa

---

MIT License · 2026 · DataVenezuela