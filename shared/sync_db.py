from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
from dateutil import parser as date_parser

from shared.storage import SessionLocal, DocumentModel, ClaimModel, init_db


def parse_datetime(val: str | None) -> datetime:
    if not val:
        return datetime.utcnow()
    try:
        return date_parser.parse(val)
    except Exception:
        return datetime.utcnow()


def sync_jsonl_to_db(documents_jsonl_path: Path, claims_jsonl_path: Path) -> dict:
    """Sincroniza archivos JSONL a la base de datos aplicando lógica de upsert."""
    init_db()

    db = SessionLocal()
    docs_synced = 0
    claims_synced = 0

    try:
        if documents_jsonl_path.exists():
            with open(documents_jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    doc_data = json.loads(line)
                    raw_hash = doc_data.get("raw_hash")
                    if not raw_hash:
                        continue

                    db_doc = db.query(DocumentModel).filter(DocumentModel.raw_hash == raw_hash).first()
                    
                    fetched_at = parse_datetime(doc_data.get("fetched_at"))

                    if db_doc:
                        db_doc.source_id = doc_data.get("source_id", db_doc.source_id)
                        db_doc.source_name = doc_data.get("source_name", db_doc.source_name)
                        db_doc.source_url = doc_data.get("source_url", db_doc.source_url)
                        db_doc.title = doc_data.get("title", db_doc.title)
                        db_doc.text = doc_data.get("text", db_doc.text)
                        db_doc.trust_tier = doc_data.get("trust_tier", db_doc.trust_tier)
                        db_doc.fetched_at = fetched_at
                        db_doc.metadata_json = doc_data.get("metadata", db_doc.metadata_json)
                    else:
                        new_doc = DocumentModel(
                            source_id=doc_data.get("source_id"),
                            source_name=doc_data.get("source_name"),
                            source_url=doc_data.get("source_url"),
                            title=doc_data.get("title"),
                            text=doc_data.get("text"),
                            raw_hash=raw_hash,
                            trust_tier=doc_data.get("trust_tier"),
                            fetched_at=fetched_at,
                            metadata_json=doc_data.get("metadata"),
                        )
                        db.add(new_doc)
                    
                    docs_synced += 1

        if claims_jsonl_path.exists():
            with open(claims_jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    claim_data = json.loads(line)
                    claim_id = claim_data.get("claim_id")
                    if not claim_id:
                        continue

                    db_claim = db.query(ClaimModel).filter(ClaimModel.claim_id == claim_id).first()
                    fetched_at = parse_datetime(claim_data.get("fetched_at"))

                    if db_claim:
                        # Actualizar campos
                        db_claim.fingerprint = claim_data.get("fingerprint", db_claim.fingerprint)
                        db_claim.description = claim_data.get("description", db_claim.description)
                        db_claim.location_text = claim_data.get("location_text", db_claim.location_text)
                        db_claim.confidence_score = claim_data.get("confidence_score", db_claim.confidence_score)
                        # No pisamos el verification_status si ya está modificado manualmente, a menos que venga explícito
                        if "verification_status" in claim_data and claim_data["verification_status"] != "new":
                            db_claim.verification_status = claim_data["verification_status"]
                        db_claim.evidence_text = claim_data.get("evidence_text", db_claim.evidence_text)
                        db_claim.fetched_at = fetched_at
                        db_claim.metadata_json = claim_data.get("metadata", db_claim.metadata_json)
                    else:
                        # Crear nuevo
                        new_claim = ClaimModel(
                            claim_id=claim_id,
                            fingerprint=claim_data.get("fingerprint"),
                            event_id=claim_data.get("event_id"),
                            source_id=claim_data.get("source_id"),
                            source_name=claim_data.get("source_name"),
                            source_url=claim_data.get("source_url"),
                            claim_type=claim_data.get("claim_type"),
                            description=claim_data.get("description"),
                            location_text=claim_data.get("location_text"),
                            confidence_score=claim_data.get("confidence_score", 0.0),
                            verification_status=claim_data.get("verification_status", "new"),
                            evidence_text=claim_data.get("evidence_text"),
                            fetched_at=fetched_at,
                            metadata_json=claim_data.get("metadata"),
                        )
                        db.add(new_claim)

                    claims_synced += 1

        db.commit()
    except Exception as exc:
        db.rollback()
        raise exc
    finally:
        db.close()

    return {
        "documents_synced": docs_synced,
        "claims_synced": claims_synced,
        "synced_at": datetime.utcnow().isoformat(),
    }


if __name__ == "__main__":
    import sys
    # Ejecutar sync autónomo usando los paths estándar del pipeline
    base_dir = Path(__file__).resolve().parents[1]
    docs_path = base_dir / "scrapers" / "runtime_output" / "sanitized" / "documents.jsonl"
    claims_path = base_dir / "scrapers" / "runtime_output" / "sanitized" / "claims.jsonl"
    
    print(f"Sincronizando desde:\n- {docs_path}\n- {claims_path}")
    res = sync_jsonl_to_db(docs_path, claims_path)
    print("Sincronización finalizada:", res)
