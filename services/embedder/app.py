import os
from typing import TYPE_CHECKING, Any

import numpy as np
from fastapi import FastAPI
from pydantic import BaseModel

try:  # pragma: no cover - exercised indirectly via runtime dependency
    from sentence_transformers import SentenceTransformer
except ImportError:  # When optional heavyweight deps (transformers, torch) are absent
    SentenceTransformer = None  # type: ignore[assignment]

if TYPE_CHECKING:  # pragma: no cover - only used for typing
    from sentence_transformers import SentenceTransformer as SentenceTransformerType
else:  # Fallback runtime alias when the dependency is missing
    SentenceTransformerType = Any

MODEL_NAME = os.getenv(
    "MODEL_NAME", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
NORMALIZE = os.getenv("NORMALIZE", "true").lower() == "true"

app = FastAPI(title="Embedder (CPU)", version="0.1.0")


class EmbedRequest(BaseModel):
    inputs: list[str]
    normalize: bool | None = None


_model: SentenceTransformerType | None = None


def get_model() -> SentenceTransformerType:
    global _model
    if _model is None:
        if SentenceTransformer is None:
            msg = (
                "sentence-transformers is not available. Install optional dependencies "
                "or ensure compatible huggingface-hub/transformers versions to enable embedding."
            )
            raise RuntimeError(msg)
        _model = SentenceTransformer(MODEL_NAME, device="cpu")
    return _model


# Eagerly load the model on startup
try:
    get_model()
    print(f"Model {MODEL_NAME} loaded successfully.")
except Exception as e:
    print(f"Failed to load model {MODEL_NAME}: {e}")


@app.get("/health")
def health():
    if _model is None:
        return {"ok": False, "model": MODEL_NAME, "error": "Model not loaded"}
    return {"ok": True, "model": MODEL_NAME}


@app.get("/model")
def model_info():
    return {"model": MODEL_NAME, "normalize": NORMALIZE}


@app.post("/embed")
def embed(req: EmbedRequest):
    model = get_model()
    norm = NORMALIZE if req.normalize is None else req.normalize
    vectors = model.encode(req.inputs, convert_to_numpy=True, normalize_embeddings=norm)
    dim = int(vectors.shape[1] if isinstance(vectors, np.ndarray) else len(vectors[0]))
    return {"vectors": vectors.tolist(), "normalize": norm, "dim": dim}
