from fastapi.testclient import TestClient

# Import the embedder FastAPI app without loading the model
from embedder.app import app


def test_embedder_health_and_model_info():
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok") is True

    r2 = client.get("/model")
    assert r2.status_code == 200
    info = r2.json()
    assert "model" in info
    assert "normalize" in info

