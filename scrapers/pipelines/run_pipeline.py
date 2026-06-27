from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

from scrapers.dedup.deduplicator import deduplicate_by_fingerprint
from scrapers.dedup.fingerprint import build_fingerprint
from scrapers.extractors.claim_extractor import extract_claim_candidates
from scrapers.extractors.html_extractor import extract_html_text
from scrapers.extractors.json_extractor import extract_json_text
from scrapers.extractors.rss_extractor import extract_rss_items
from scrapers.extractors.text_extractor import extract_plain_text
from scrapers.fetchers.http_client import fetch_url
from scrapers.fetchers.local_file import read_local_file
from scrapers.models.document import Document
from scrapers.outputs.jsonl_writer import write_json, write_jsonl
from scrapers.sanitizers.pii_redactor import redact_pii
from scrapers.sources.loader import load_sources
from scrapers.validators.quality import assert_sanitized, confidence_from_tier


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _passes_keywords(text: str, keywords: Iterable[str]) -> bool:
    kw = [k.lower() for k in keywords or [] if k]
    if not kw:
        return True
    lower = (text or "").lower()
    return any(k in lower for k in kw)


def _fetch_and_extract(source) -> list[Document]:
    if source.type == "manual_file":
        raw = read_local_file(source.url)
        title, text = extract_plain_text(raw)
        return [
            Document(
                source_id=source.id,
                source_name=source.name,
                source_url=source.url,
                title=title,
                text=text,
                raw_hash=_hash_text(raw),
                trust_tier=source.trust_tier,
            )
        ]

    raw, content_type = fetch_url(source.url)

    if source.type == "html_static":
        title, text = extract_html_text(raw)
        return [
            Document(
                source_id=source.id,
                source_name=source.name,
                source_url=source.url,
                title=title,
                text=text,
                raw_hash=_hash_text(raw),
                trust_tier=source.trust_tier,
                metadata={"content_type": content_type},
            )
        ]

    if source.type == "api_json":
        title, text, metadata = extract_json_text(raw)
        metadata["content_type"] = content_type
        return [
            Document(
                source_id=source.id,
                source_name=source.name,
                source_url=source.url,
                title=title,
                text=text,
                raw_hash=_hash_text(raw),
                trust_tier=source.trust_tier,
                metadata=metadata,
            )
        ]

    if source.type == "rss":
        docs: list[Document] = []
        for title, text in extract_rss_items(raw):
            docs.append(
                Document(
                    source_id=source.id,
                    source_name=source.name,
                    source_url=source.url,
                    title=title,
                    text=text,
                    raw_hash=_hash_text(text),
                    trust_tier=source.trust_tier,
                    metadata={"content_type": content_type},
                )
            )
        return docs

    raise ValueError(f"Tipo no soportado: {source.type}")


def run_pipeline(
    config_path: Path,
    output_dir: Path,
    limit: int | None = None,
    keep_raw: bool = False,
) -> dict:
    project, sources = load_sources(config_path)

    event_id = project.get("event_id", "unknown_event")
    default_country = project.get("default_country")

    sanitized_dir = output_dir / "sanitized"
    raw_dir = output_dir / "raw_quarantine"

    all_documents: list[dict] = []
    all_claims: list[dict] = []
    errors: list[dict] = []
    sources_processed = 0

    for source in sources:
        if not source.enabled:
            continue

        sources_processed += 1

        try:
            documents = _fetch_and_extract(source)
            if limit is not None:
                documents = documents[:limit]

            for doc in documents:
                if not _passes_keywords(doc.text, source.required_keywords):
                    continue

                sanitized_text = redact_pii(doc.text)
                sanitized_title = redact_pii(doc.title or "")

                if not assert_sanitized(sanitized_text):
                    errors.append(
                        {
                            "source_id": source.id,
                            "error": "PII detectada después de saneamiento",
                        }
                    )
                    continue

                doc_row = {
                    "source_id": doc.source_id,
                    "source_name": doc.source_name,
                    "source_url": doc.source_url,
                    "title": sanitized_title or None,
                    "text": sanitized_text[:2000],
                    "raw_hash": doc.raw_hash,
                    "trust_tier": doc.trust_tier,
                    "fetched_at": doc.fetched_at,
                    "metadata": doc.metadata,
                }
                all_documents.append(doc_row)

                if keep_raw:
                    raw_dir.mkdir(parents=True, exist_ok=True)
                    (raw_dir / f"{doc.source_id}_{doc.raw_hash[:12]}.txt").write_text(
                        sanitized_text,
                        encoding="utf-8",
                    )

                sanitized_doc = Document(
                    source_id=doc.source_id,
                    source_name=doc.source_name,
                    source_url=doc.source_url,
                    title=sanitized_title,
                    text=sanitized_text,
                    raw_hash=doc.raw_hash,
                    trust_tier=doc.trust_tier,
                    fetched_at=doc.fetched_at,
                    published_at=doc.published_at,
                    metadata=doc.metadata,
                )

                candidates = extract_claim_candidates(
                    sanitized_doc,
                    event_id=event_id,
                    default_country=default_country,
                )

                for candidate in candidates:
                    fingerprint = build_fingerprint(
                        event_id=candidate["event_id"],
                        claim_type=candidate["claim_type"],
                        location_text=candidate.get("location_text"),
                        description=candidate["description"],
                    )
                    claim_id = f"claim_{fingerprint[:16]}"
                    claim = {
                        "claim_id": claim_id,
                        "fingerprint": fingerprint,
                        "event_id": candidate["event_id"],
                        "source_id": candidate["source_id"],
                        "source_name": candidate["source_name"],
                        "source_url": candidate["source_url"],
                        "claim_type": candidate["claim_type"],
                        "description": candidate["description"],
                        "location_text": candidate.get("location_text"),
                        "confidence_score": confidence_from_tier(doc.trust_tier),
                        "verification_status": "new",
                        "evidence_text": candidate["evidence_text"],
                        "fetched_at": candidate["fetched_at"],
                        "metadata": {
                            "trust_tier": doc.trust_tier,
                            "raw_hash": doc.raw_hash,
                        },
                    }
                    all_claims.append(claim)

        except Exception as exc:
            errors.append({"source_id": source.id, "error": str(exc)})

    deduped_claims, duplicates = deduplicate_by_fingerprint(all_claims)

    documents_path = sanitized_dir / "documents.jsonl"
    claims_path = sanitized_dir / "claims.jsonl"
    summary_path = output_dir / "run_summary.json"

    documents_exported = write_jsonl(documents_path, all_documents)
    claims_exported = write_jsonl(claims_path, deduped_claims)

    summary = {
        "config_path": str(config_path),
        "output_dir": str(output_dir),
        "sources_processed": sources_processed,
        "documents_exported": documents_exported,
        "claims_detected": len(all_claims),
        "claims_exported": claims_exported,
        "claims_deduplicated": duplicates,
        "errors": errors,
        "outputs": {
            "documents": str(documents_path),
            "claims": str(claims_path),
            "summary": str(summary_path),
        },
    }

    write_json(summary_path, summary)
    return summary
