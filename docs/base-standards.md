---
description: Base development rules for VZLA_DEDUP, applicable to all AI agents such as Claude, Cursor, Codex, Gemini, Copilot, and similar tools.
alwaysApply: true
---

# VZLA_DEDUP — Base Agent Standards

This document is the entry-point for AI agents working on VZLA_DEDUP.

Work fast, but never break project contracts, expose sensitive data, or introduce unnecessary coupling.

---

## 1. Core Principles

- **Small tasks, one at a time.** Make focused changes. Do not mix unrelated work.
- **Test-backed changes.** New behavior must include tests. Prefer test-first when practical.
- **Type safety.** All new Python code must use type hints.
- **Clear naming.** Descriptive names for variables, functions, classes, modules, tests, configs.
- **Incremental changes.** Avoid large rewrites unless explicitly requested.
- **Question assumptions.** Do not invent fields, contracts, folders, tools, or behavior.
- **Safety first.** Never expose PII or real crisis data.

---

## 2. Architecture — read this first

VZLA_DEDUP is split across two repos:

- **`DataVenezuela/VZLA_DEDUP`** — Python scraping pipeline (this repo)
- **`DataVenezuela/dataVenezuela`** — Next.js + Supabase (BD/API layer)

The pipeline has four layers. Understand them before touching anything:

```
Adapters + Parsers + PII masking + Normalization
      ↓
Raw DB (Cloudflare R2 + Supabase metadata)    ←── Quarantine DB
      ↓
Staging (aportes table in Supabase)
      ↓  consolidation job
Canonical (persons / events / acopio_centers)
      ↓  build job
Cloudflare Worker + D1  →  Public API
```

**The pipeline does not write JSONL to disk.** Output goes to staging via `POST /api/v1/dedup/*`.

**Records without a parser go to quarantine, not to a generic fallback.**

---

## 3. Required Context

Before coding, read the relevant docs:

```
docs/pipeline.md          — full technical flow, implementation status per component
docs/scrapper_contract.md — entity contract for parsers
docs/source_config.md     — how to declare sources in YAML
docs/schema.md            — entity schema and enums
docs/adr/                 — architecture decision records
```

If documentation and code contradict each other, stop and surface the ambiguity. Do not silently choose a contract.

---

## 4. Language Standards

Code identifiers must be in English:

```
variables · functions · classes · modules
tables · columns · config keys
test names · error names · log fields
```

Code comments in Spanish. Comments explain **why**, not **what**.

Good:
```python
# We use HMAC so we can compare IDs without storing the original value.
cedula_hmac = identity_token(raw_cedula, secret)
```

Bad:
```python
# Generate the HMAC.
cedula_hmac = identity_token(raw_cedula, secret)
```

Documentation may be in Spanish. Contract field names stay in English:
```
person_record_id · cedula_hmac · source_url · trust_tier · verification_status
```

---

## 5. Python and Dependencies

Use Python + `pip`. Do not introduce `uv`, `poetry`, or `pipenv` without explicit approval.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r scrapers/requirements.txt
```

---

## 6. Non-Negotiable Contracts

| Rule | Detail |
|---|---|
| `cedula_hmac` | Pure 64-char hex, **no prefix**. Never `hmac_sha256:...` |
| `trust_tier` (scrapers) | Letters `A`/`B`/`C`/`D`, never integers |
| `trust_tier` (DB) | Integers `1`/`2`/`3`. Conversion happens in the staging exporter |
| `Person.status` | English: `missing`/`found`/`injured`/`deceased`/`unknown` |
| `is_minor` | Always declare. `None` if unknown — never omit the field |
| PII timing | HMAC before creating the entity, never after |
| No JSONL on disk | Output goes to staging in Supabase, not local files |
| No FallbackParser | No parser → quarantine, not discard |
| No auto-merge on Person | The consolidation job generates candidates; a human decides |

---

## 7. Issue Dependencies

Before picking up an issue, check whether it is blocked:

```
#85  fix(models)          → unblocked
  └── #81  staging exp.  → blocked by #85
        └── #82  consol. → blocked by #81
```

Do not implement a blocked issue without resolving its dependency first. Check the README for the full dependency tree.

---

## 8. Testing

```bash
pytest scrapers/tests   # must pass before and after every change
```

- All tests use synthetic fixtures. Never real data.
- Mock external HTTP calls (`httpx.MockTransport`). Never real network in tests.
- New behavior = new tests. No exceptions.

---

## 9. Commits and PRs

Commit messages follow Conventional Commits:

```
feat(parsers): add encuentralos parser → Person
fix(models): add missing is_minor field to Person
docs(pipeline): update flow to reflect 4-layer architecture
test(parsers): add encuentralos fixture for unknown status
```

One PR = one thing. Do not mix parser changes with schema changes or unrelated refactors.

PR description must include: what changed, why, how to test, what risk it carries.

---

## 10. Security

- Never commit real data (names, IDs, phones, PDFs, CSVs, JSONL with real records).
- Never log PII.
- Never store `cedula` in clear text anywhere.
- `runtime_output/` is git-ignored — never force-add it.
- If you find a potential PII leak in the codebase, open an issue and flag it before fixing.

---

## 11. Architecture Exception

`base-standards.md §5` (Python only) applies to the entire pipeline and the internal BD layer.

The **public serving plane** (`serving/` directory, Cloudflare Worker) is implemented in TypeScript. This is an explicit exception documented in ADR 0001 (`docs/adr/0001-arquitectura-serving-publico.md`). This exception does not extend beyond `serving/`.