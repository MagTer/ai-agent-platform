from unittest.mock import patch

from fastapi.testclient import TestClient

# Import the embedder FastAPI app without loading the model
from services.embedder.app import app


# Create a dummy model class for mocking
class MockSentenceTransformer:
    def encode(self, inputs, **kwargs):
        return [[0.1] * 384 for _ in inputs]  # Return dummy embeddings

    @property
    def max_seq_length(self):
        return 128


def test_embedder_health_and_model_info():
    with patch("services.embedder.app._model", new=MockSentenceTransformer()):
        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True, f"Health check failed: {data}"

        r2 = client.get("/model")
        assert r2.status_code == 200
        info = r2.json()
        assert "model" in info
        assert "normalize" in info
