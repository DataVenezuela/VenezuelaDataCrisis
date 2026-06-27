from __future__ import annotations

from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from shared.storage import SessionLocal, ClaimModel, DocumentModel
from shared.sync_db import sync_jsonl_to_db
from api.auth import verify_api_key

router = APIRouter(prefix="/api/v1", tags=["records"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class ClaimUpdate(BaseModel):
    verification_status: str | None = Field(default=None, examples=["verified", "dismissed", "new"])
    description: str | None = Field(default=None)
    location_text: str | None = Field(default=None)


@router.get("/claims")
def list_claims(
    claim_type: str | None = Query(default=None, description="Filtrar por tipo de necesidad (ej: need.water, casualties.missing)"),
    location_text: str | None = Query(default=None, description="Filtrar por ubicacion aproximada"),
    search: str | None = Query(default=None, description="Busqueda por texto en la descripcion"),
    verification_status: str | None = Query(default=None, description="Filtrar por estado (new, verified, dismissed)"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Retorna la lista de claims consolidados y deduplicados aplicando filtros."""
    query = db.query(ClaimModel)

    if claim_type:
        query = query.filter(ClaimModel.claim_type == claim_type)
    
    if verification_status:
        query = query.filter(ClaimModel.verification_status == verification_status)

    if location_text:
        query = query.filter(ClaimModel.location_text.ilike(f"%{location_text}%"))

    if search:
        query = query.filter(ClaimModel.description.ilike(f"%{search}%"))

    total = query.count()
    items = query.order_by(ClaimModel.fetched_at.desc()).offset(offset).limit(limit).all()

    results = []
    for item in items:
        results.append({
            "claim_id": item.claim_id,
            "fingerprint": item.fingerprint,
            "event_id": item.event_id,
            "source_id": item.source_id,
            "source_name": item.source_name,
            "source_url": item.source_url,
            "claim_type": item.claim_type,
            "description": item.description,
            "location_text": item.location_text,
            "confidence_score": item.confidence_score,
            "verification_status": item.verification_status,
            "evidence_text": item.evidence_text,
            "fetched_at": item.fetched_at.isoformat() if item.fetched_at else None,
            "metadata": item.metadata_json,
        })

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": results
    }


@router.get("/claims/{claim_id}")
def get_claim(claim_id: str, db: Session = Depends(get_db)):
    """Retorna los detalles completos de un claim específico."""
    item = db.query(ClaimModel).filter(ClaimModel.claim_id == claim_id).first()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Claim con ID '{claim_id}' no encontrado."
        )

    return {
        "claim_id": item.claim_id,
        "fingerprint": item.fingerprint,
        "event_id": item.event_id,
        "source_id": item.source_id,
        "source_name": item.source_name,
        "source_url": item.source_url,
        "claim_type": item.claim_type,
        "description": item.description,
        "location_text": item.location_text,
        "confidence_score": item.confidence_score,
        "verification_status": item.verification_status,
        "evidence_text": item.evidence_text,
        "fetched_at": item.fetched_at.isoformat() if item.fetched_at else None,
        "metadata": item.metadata_json,
    }


@router.patch("/claims/{claim_id}")
def update_claim(
    claim_id: str,
    update_data: ClaimUpdate,
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key),
):
    """Actualiza de forma segura el estado o la descripción de un claim (protegido)."""
    item = db.query(ClaimModel).filter(ClaimModel.claim_id == claim_id).first()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Claim con ID '{claim_id}' no encontrado."
        )

    if update_data.verification_status is not None:
        item.verification_status = update_data.verification_status
    if update_data.description is not None:
        item.description = update_data.description
    if update_data.location_text is not None:
        item.location_text = update_data.location_text

    db.commit()
    return {"message": "Claim actualizado exitosamente.", "claim_id": claim_id}


@router.post("/sync")
def trigger_sync(db: Session = Depends(get_db), api_key: str = Depends(verify_api_key)):
    """Dispara la sincronización manual de archivos JSONL a la base de datos (protegido)."""
    base_dir = Path(__file__).resolve().parents[2]
    docs_path = base_dir / "scrapers" / "runtime_output" / "sanitized" / "documents.jsonl"
    claims_path = base_dir / "scrapers" / "runtime_output" / "sanitized" / "claims.jsonl"

    try:
        res = sync_jsonl_to_db(docs_path, claims_path)
        return {"status": "success", "result": res}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error durante la sincronizacion: {str(exc)}"
        )
