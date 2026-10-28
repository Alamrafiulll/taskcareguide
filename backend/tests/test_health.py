from fastapi.testclient import TestClient
from app.main import app


def test_health_endpoint():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_endpoint():
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] in {"ready", "degraded"}
    assert response.json()["checks"]["database"] == "ok"
    assert "worker" in response.json()["checks"]
    assert "ocr" in response.json()["checks"]
    assert "local_ai" in response.json()["checks"]
