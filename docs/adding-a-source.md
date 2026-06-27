# Adding a source

Use this checklist when taking ownership of a source.

1. Pick a stable `source_id`, for example `hospitales_google_sheet`.
2. Identify the source type: `HTML`, `DYNAMIC_HTML`, `GOOGLE_SHEET`, `API`, `PDF`, `MAP`, `DRIVE`, `SOCIAL`, or `OTHER`.
3. Fetch the source and preserve the raw snapshot outside git.
4. Extract source rows/items.
5. Normalize each item into the dump contract.
6. Hash sensitive identifiers before writing shareable dumps.
7. Mark doubtful records as `NEEDS_REVIEW`.
8. Emit `manifest.json`.
9. Emit `records.normalized.jsonl`.
10. Emit `dedupe_candidates.jsonl` when possible.

## Minimum done definition

A source is ready for review when it has:

- A clear `source_id`.
- A generated manifest.
- Normalized records using `docs/dump-contract.md`.
- No real raw data committed to git.
- Sensitive identifiers hashed or omitted.

