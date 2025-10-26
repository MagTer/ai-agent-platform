import os
from typing import List, Optional

import numpy as np
from fastapi import FastAPI, Body
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer


MODEL_NAME = os.getenv("MODEL_NAME", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
NORMALIZE = os.getenv("NORMALIZE", "true").lower() == "true"

app = FastAPI(title="Embedder (CPU)", version="0.1.0")


class EmbedRequest(BaseModel):
    inputs: List[str]
    normalize: Optional[bool] = None


_model: Optional[SentenceTransformer] = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME, device="cpu")
    return _model


@app.get("/health")
def health():
    return {"ok": True, "model": MODEL_NAME}


@app.get("/model")
def model_info():
    return {"model": MODEL_NAME, "normalize": NORMALIZE}


@app.post("/embed")
def embed(req: EmbedRequest):
    model = get_model()
    norm = NORMALIZE if req.normalize is None else req.normalize
    vectors = model.encode(req.inputs, convert_to_numpy=True, normalize_embeddings=norm)
    return {"vectors": vectors.tolist(), "normalize": norm, "dim": int(vectors.shape[1] if isinstance(vectors, np.ndarray) else len(vectors[0]) )}

