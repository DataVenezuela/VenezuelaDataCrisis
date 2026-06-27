import os
import pytest
from fastapi.testclient import TestClient

# Configurar variables de entorno antes de importar almacenamiento/app
os.environ["DATABASE_URL"] = "sqlite:///./test_vzla_dedup.db"

@pytest.fixture(scope="module", autouse=True)
def set_env():
    old_key = os.environ.get("API_SECRET_KEY")
    os.environ["API_SECRET_KEY"] = "test-secret-key-123"
    yield
    if old_key is not None:
        os.environ["API_SECRET_KEY"] = old_key
    else:
        os.environ.pop("API_SECRET_KEY", None)

from shared.storage import init_db, SessionLocal, ClaimModel, Base, engine
from api.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    # Inicializar base de datos vaciando y creando tablas de manera segura
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    
    # Insertar algunos datos de prueba
    claim = ClaimModel(
        claim_id="claim_test_123",
        fingerprint="fp123",
        event_id="venezuela_earthquake",
        source_id="test_source",
        source_name="Twitter Report",
        source_url="http://twitter.com/test",
        claim_type="casualties.missing",
        description="Se busca a Maria Perez",
        location_text="Sector Central",
        confidence_score=0.9,
        verification_status="new",
        evidence_text="Tweet publico",
    )
    db.add(claim)
    db.commit()
    db.close()
    yield
    # Limpieza básica
    db = SessionLocal()
    db.query(ClaimModel).delete()
    db.commit()
    db.close()


def test_root_endpoint():
    res = client.get("/")
    assert res.status_code == 200
    assert res.json()["status"] == "online"


def test_list_claims():
    res = client.get("/api/v1/claims")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["claim_id"] == "claim_test_123"
    assert data["items"][0]["description"] == "Se busca a Maria Perez"


def test_list_claims_filter():
    # Filtro que coincide
    res1 = client.get("/api/v1/claims?search=Maria")
    assert res1.json()["total"] == 1
    
    # Filtro que no coincide
    res2 = client.get("/api/v1/claims?search=Claudio")
    assert res2.json()["total"] == 0


def test_get_single_claim():
    res = client.get("/api/v1/claims/claim_test_123")
    assert res.status_code == 200
    assert res.json()["claim_id"] == "claim_test_123"


def test_get_single_claim_not_found():
    res = client.get("/api/v1/claims/non_existent_id")
    assert res.status_code == 404


def test_update_claim_unauthorized():
    payload = {"verification_status": "verified"}
    res = client.patch("/api/v1/claims/claim_test_123", json=payload)
    assert res.status_code == 401


def test_update_claim_authorized():
    payload = {"verification_status": "verified"}
    headers = {"X-API-Key": "test-secret-key-123"}
    res = client.patch("/api/v1/claims/claim_test_123", json=payload, headers=headers)
    assert res.status_code == 200
    
    # Verificar que cambió
    res_get = client.get("/api/v1/claims/claim_test_123")
    assert res_get.json()["verification_status"] == "verified"


def test_sync_unauthorized():
    res = client.post("/api/v1/sync")
    assert res.status_code == 401
