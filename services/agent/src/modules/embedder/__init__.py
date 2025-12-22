import logging
import os
from typing import Any

import numpy as np

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None  # type: ignore

logger = logging.getLogger(__name__)


class Embedder:
    _instance = None
    _model: Any = None

    def __new__(cls) -> "Embedder":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if self._model is None:
            self._load_model()

    def _load_model(self) -> None:
        model_name = os.getenv(
            "EMBEDDER_MODEL_NAME", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )
        if SentenceTransformer is None:
            logger.error("sentence-transformers not installed.")
            raise RuntimeError("sentence-transformers not installed")

        logger.info(f"Loading embedding model: {model_name}")
        self._model = SentenceTransformer(
            model_name, device="cpu"
        )  # Force CPU for now as per plan context

    def embed(self, texts: list[str], normalize: bool = True) -> list[list[float]]:
        if not self._model:
            self._load_model()

        vectors = self._model.encode(texts, convert_to_numpy=True, normalize_embeddings=normalize)
        if isinstance(vectors, np.ndarray):
            return vectors.tolist()
        return vectors


# Global instance for easy access
_embedder = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder
