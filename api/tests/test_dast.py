import os
import time
import pytest
from fastapi.testclient import TestClient

# Configurar variables de entorno de prueba antes de importar
os.environ["DATABASE_URL"] = "sqlite:///./test_vzla_dedup.db"

@pytest.fixture(scope="module", autouse=True)
def set_env():
    old_key = os.environ.get("API_SECRET_KEY")
    os.environ["API_SECRET_KEY"] = "super-secret-api-key-for-dast-testing"
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
    
    # Insertar un registro testigo para comprobar que SQLi no lo salta ilegítimamente
    claim = ClaimModel(
        claim_id="target_claim_123",
        fingerprint="fp123",
        event_id="venezuela_earthquake",
        source_id="test_source",
        source_name="Twitter Report",
        source_url="http://twitter.com/test",
        claim_type="need.water",
        description="Se busca agua en Zona A",
        location_text="Zona A",
        confidence_score=0.9,
        verification_status="new",
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


# ==============================================================================
# 1. Módulo SQLi (Fuzzing de parámetros de búsqueda difusa y filtros)
# ==============================================================================
def test_dast_sqli_fuzzing():
    sqli_payloads = [
        "' OR '1'='1",
        "'; DROP TABLE claims; --",
        "' UNION SELECT NULL, NULL, NULL, NULL --",
        "1; SELECT pg_sleep(5)",
        "admin'--",
    ]

    for payload in sqli_payloads:
        # Bombardear parámetro de búsqueda difusa 'search'
        res_search = client.get(f"/api/v1/claims?search={payload}")
        assert res_search.status_code == 200, f"Fallo SQLi en search con payload: {payload}"
        # La consulta debe ser segura y simplemente retornar 0 resultados
        assert res_search.json()["total"] == 0

        # Bombardear parámetro de ubicación 'location_text'
        res_loc = client.get(f"/api/v1/claims?location_text={payload}")
        assert res_loc.status_code == 200, f"Fallo SQLi en location_text con payload: {payload}"
        assert res_loc.json()["total"] == 0


# ==============================================================================
# 2. Módulo Brute Force / Timing Attack (Prueba de mitigación por compare_digest)
# ==============================================================================
def test_dast_timing_attack_resilience():
    # Realizar solicitudes con keys de diferentes longitudes y prefijos
    keys_to_test = [
        "a",
        "wrong-key",
        "super-secret-api-key-for-dast-testing-almost-matching",
        "super-secret-api-key-for-dast-testing"  # Clave correcta
    ]
    
    times = []
    for key in keys_to_test:
        start_time = time.perf_counter()
        res = client.post("/api/v1/sync", headers={"X-API-Key": key})
        end_time = time.perf_counter()
        times.append(end_time - start_time)
        
        if key == "super-secret-api-key-for-dast-testing":
            assert res.status_code == 200
        else:
            assert res.status_code == 401
            
    # La desviación estándar de los tiempos de rechazo 401 debe ser muy baja,
    # lo cual valida la comparación en tiempo constante (compare_digest)
    reject_times = times[:-1]
    mean_time = sum(reject_times) / len(reject_times)
    variance = sum((t - mean_time) ** 2 for t in reject_times) / len(reject_times)
    std_dev = variance ** 0.5
    
    # Tolerancia de variación temporal mínima típica en TestClient/FastAPI en localhost
    assert std_dev < 0.005, f"La desviación estándar temporal es demasiado alta ({std_dev}s), posible vulnerabilidad a Timing Attack"


# ==============================================================================
# 3. Módulo CORS y Headers (Auditoría de cabeceras de respuesta y middlewares)
# ==============================================================================
def test_dast_cors_and_headers():
    res = client.get("/", headers={"Origin": "http://localhost:3000"})
    assert res.status_code == 200
    
    # 1. Comprobar que las cabeceras CORS están presentes y son abiertas
    assert "access-control-allow-origin" in res.headers
    assert res.headers["access-control-allow-origin"] in ("*", "http://localhost:3000")
    
    # 2. Comprobar que no se filtra información sensible del servidor ni depuración
    assert "x-powered-by" not in res.headers
    assert "server" not in res.headers or "uvicorn" not in res.headers.get("server", "").lower()
