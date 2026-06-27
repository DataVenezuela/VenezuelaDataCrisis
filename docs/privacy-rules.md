# Privacy rules

The repository must not contain real crisis data dumps.

## Do not commit

- Real cedulas or identity document numbers.
- Real phone numbers.
- Medical notes tied to a person.
- Raw PDFs, images, screenshots, spreadsheets, CSVs, JSONL dumps, or local database files.
- `.env` files or hashing/encryption secrets.
- Generated scraper outputs.

## Sensitive fields

Documents such as cedulas must be hashed before dumps are shared.

Recommended approach:

- Normalize the identifier first.
- Hash with HMAC-SHA256 using a secret outside git.
- Store only the hash in shareable dumps.

Plain SHA256 without a secret is not ideal for cedulas because the possible value space is small enough to brute-force.

## Other personal data

Names and addresses may be needed for matching, but they can still identify people when combined with other fields. Treat these carefully:

- Mark records as sensitive when they contain personal data.
- Redact phone numbers unless DB/API explicitly asks for them.
- Mark minors, deceased people, missing people, and medical notes as requiring review.
- Use fake data only in committed examples.

## When unsure

- Keep the data out of git.
- Mark the record as `NEEDS_REVIEW`.
- Ask DB/API or verification before sharing/publication.

