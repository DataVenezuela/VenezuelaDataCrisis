---

description: Base development rules for VZLA_DEDUP, applicable to all AI agents such as Claude, Cursor, Codex, Gemini, Copilot, and similar tools.
alwaysApply: true
-----------------

# VZLA_DEDUP — Base Agent Standards

This document is the entry-point for AI agents working on VZLA_DEDUP.

Agents must work fast, but never break project contracts, expose sensitive data, or introduce unnecessary coupling.

---

## 1. Core Principles

* **Small tasks, one at a time**: make focused changes. Do not mix unrelated work.
* **Test-backed changes**: new behavior must include tests. Prefer test-first when practical.
* **Type safety**: all new Python code must use type hints.
* **Clear naming**: use descriptive names for variables, functions, classes, modules, tests, and configs.
* **Incremental changes**: avoid large rewrites unless explicitly requested.
* **Question assumptions**: do not invent fields, contracts, folders, tools, or behavior.
* **Pattern detection**: detect repeated logic and extract it only when the abstraction is stable.
* **Safety first**: never expose PII or real crisis data.

---

## 2. Required Context

Before coding, agents must inspect the relevant docs:

```text
docs/pipeline.md
docs/source_config.md
docs/scraper_contract.md
docs/schema.md
docs/base-standards.md
```

If documentation and code contradict each other, stop and surface the ambiguity.

Do not silently choose a contract.

---

## 3. Language Standards

Code identifiers must be in English:

```text
variables
functions
classes
modules
tables
columns
config keys
test names
error names
log fields
```

Good:

```python
def normalize_person_name(raw_name: str) -> str:
    ...
```

Code comments should preferably be in Spanish.

Comments must explain **why**, not repeat **what**.

Good:

```python
# Usamos HMAC para comparar cédulas sin guardar el valor original.
cedula_hmac = generate_hmac_sha256(raw_cedula, secret)
```

Bad:

```python
# Genera el HMAC.
cedula_hmac = generate_hmac_sha256(raw_cedula, secret)
```

Documentation may be written in Spanish, but contract names must remain unchanged:

```text
person_record_id
cedula_hmac
source_url
trust_tier
verification_status
```

---

## 4. Python and Dependencies

This project uses Python.

Use `pip`.

Do not introduce:

```text
uv
poetry
pipenv
```

unless explicitly approved.

Expected setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If a module has its own requirements file, use the existing one.

All new code must be typed.

Keep functions small and focused.

Avoid hidden global state. Pass dependencies explicitly.

---

## 5. Database Standards

This project uses SQLAlchemy as ORM.

Agents must:

* Use SQLAlchemy for ORM models and normal database access.
* Not introduce another ORM.
* Avoid raw SQL for normal CRUD unless technically justified.
* Keep database models aligned with `docs/schema.md`.

Schema-related changes require:

```text
code change
documentation update
tests or validation coverage
clear explanation
```

---

## 6. Architecture Standards

Avoid unnecessary coupling.

Keep these responsibilities separated:

```text
fetching
parsing
PII sanitization
normalization
deduplication
validation
export
database ingestion
```

Rules:

* A scraper should not directly mutate production database tables.
* A parser should not know database logic.
* An exporter should not depend on a specific source.
* Source-specific logic must stay close to that source.

Prefer interfaces, protocols, or explicit contracts over concrete dependencies.

Good:

```python
from typing import Protocol

class PersonParser(Protocol):
    def parse(self, raw_content: str) -> list[Person]:
        ...
```

---

## 7. Shared/Common Utilities

Use `shared/common` for small reusable utilities that enforce project-wide behavior.

Good candidates:

```text
HMAC generation
cedula masking
UTC datetime parsing
ISO 8601 validation
text normalization
unicode cleanup
JSONL writing
JSONL validation
enum validation
null handling
UUID generation
source config validation
```

Do not put source-specific logic in `shared/common`.

Each shared/common file should solve one clear problem.

Prefer:

```text
shared/common/hmac_sha256.py
shared/common/mask_cedula.py
shared/common/normalize_text.py
shared/common/parse_utc_datetime.py
shared/common/validate_enum.py
shared/common/write_jsonl.py
```

Avoid dumping-ground files:

```text
shared/common/utils.py
shared/common/helpers.py
shared/common/misc.py
```

A shared utility should expose one main public function whenever possible.

Internal private helpers are allowed.

Every shared utility requires tests.

---

## 8. Testing Standards

New behavior requires tests.

This includes:

```text
parsers
normalizers
PII sanitizers
JSONL exporters
schema validators
source config validators
database logic
shared/common utilities
```

Tests must not depend on live websites, APIs, PDFs, or network calls.

Use fixtures, mocks, or fake local samples.

Never use real crisis data in tests, fixtures, logs, docs, or examples.

Use obvious fake/demo data:

```text
JOSE LUIS PEREZ DEMO
V-****0000
https://example.org/demo
Hospital Demo
Centro de Acopio Demo
```

---

## 9. Scraper and JSONL Standards

Scrapers must follow:

```text
docs/pipeline.md
docs/source_config.md
docs/scraper_contract.md
docs/schema.md
```

Agents must not:

```text
invent JSONL fields
change enum values silently
export raw PII
discard incomplete records because optional fields are missing
merge people automatically from a scraper
```

Unknown values must use:

```json
null
```

Do not use:

```text
""
"N/A"
"null"
0
```

unless the schema explicitly defines that value.

---

## 10. PII and Safety

Treat the following as sensitive:

```text
cedulas
phone numbers
exact addresses
names associated with vulnerable persons
photos
medical status
hospital information
minor-related information
private source URLs
tokens
cookies
secrets
```

Never include PII in:

```text
commits
tests
fixtures
logs
error messages
documentation examples
generated JSONL examples
```

The schema currently defines:

```text
cedula_hmac
cedula_masked
```

Do not export raw cedulas.

Do not add phone fields unless `docs/schema.md` and `docs/scraper_contract.md` are updated first.

---

## 11. Agent Workflow

Before coding:

```text
read relevant docs
inspect existing code patterns
identify the smallest useful change
check existing tests
surface ambiguity before inventing behavior
```

While coding:

```text
make the smallest useful change
use type hints
prefer interfaces
avoid direct coupling
use pip only
use SQLAlchemy for ORM work
avoid unrelated refactors
keep source-specific logic out of shared/common
```

After coding:

```text
run or update tests
verify JSONL contracts if scrapers changed
verify no PII is exposed
update docs if behavior changed
mention pending ambiguities or risks
```

---

## 12. Stop Conditions

Stop and ask before continuing if:

```text
a required field is missing from the schema
a new JSONL field seems necessary
a phone-related field is needed
a deduplication decision would merge people
a source appears private or sensitive
a schema change is required
the codebase contradicts the documentation
the task requires changing multiple layers at once
a new dependency manager would be needed
shared/common would need source-specific logic
```

---

## 13. Final Rule

```text
Small steps.
Typed code.
Pip only.
SQLAlchemy only.
Tests required.
Prefer interfaces.
Use shared/common for focused reusable utilities.
No dumping-ground utils.py files.
No raw PII.
No invented contracts.
Comments explain why, preferably in Spanish.
Docs and code must stay aligned.
```
