## Qué cambié

<!-- Descripción clara de qué cambia este PR -->

## Por qué era necesario

Resuelve #<!-- número de issue -->

## Cómo se prueba

<!-- Pasos para verificar el cambio, o referencia a tests agregados -->

## Qué riesgo tiene

<!-- Qué podría romperse, qué alcance tiene el blast radius -->

## Cómo protege PII

<!-- O "No aplica" si el cambio no toca datos personales -->

## Ejemplo de salida (datos ficticios)

<!-- Si aplica: payload, log, o resultado de ejemplo con datos sintéticos -->

---

## Checklist

- [ ] Corrí los tests (`pytest scrapers/tests`).
- [ ] `ruff check .` pasa limpio.
- [ ] No incluí datos reales (personas, cédulas, teléfonos, PDFs, CSVs, JSONL).
- [ ] No logueo cédulas, teléfonos, direcciones, nombres completos sensibles ni secretos.
- [ ] El cambio mantiene trazabilidad hacia la fuente original.
- [ ] Actualicé documentación si cambié contratos, schemas o comportamiento esperado.
- [ ] Si agregué un campo a `SourceConfig` o al YAML de fuentes, actualicé `docs/source_config.md` en el mismo PR.
- [ ] El PR resuelve una sola cosa.
- [ ] Si agrego un parser nuevo, los registros sin parser van a cuarentena, no al basura.