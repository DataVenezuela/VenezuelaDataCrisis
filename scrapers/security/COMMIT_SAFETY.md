# Seguridad antes de commit

Ejecutar antes de subir cambios:

```bash
find scrapers/ -type f \( -name "*.csv" -o -name "*.xlsx" -o -name "*.pdf" -o -name "*.zip" -o -name "*.sql" -o -name "*.db" -o -name "*.jsonl" \)

grep -RInE "api[_-]?key|token|secret|password|cedula|cédula|dni|rut|telefono|teléfono|phone" scrapers/ || true

pytest scrapers/tests
```

Si aparece data real o sensible, no hacer commit.
