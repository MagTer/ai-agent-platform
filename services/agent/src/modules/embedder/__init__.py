import logging
import os
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from core.core.litellm_client import LiteLLMClient

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None  # type: ignore

logger = logging.getLogger(__name__)


class Embedder:
    """Singleton embedder with lazy model loading for faster container startup."""

    _instance: "Embedder | None" = None
    _model: Any = None

    def __new__(cls) -> "Embedder":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        # Lazy loading: Do NOT load model in __init__
        # Model is loaded on first use via _ensure_model_loaded()
        pass

    def _ensure_model_loaded(self) -> None:
        """Load the model if not already loaded (lazy initialization)."""
        if self._model is None:
            self._load_model()

    def _load_model(self) -> None:
        model_name = os.getenv(
            "EMBEDDER_MODEL_NAME",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        )
        if SentenceTransformer is None:
            logger.error("sentence-transformers not installed.")
            raise RuntimeError("sentence-transformers not installed")

        logger.info(f"Loading embedding model: {model_name} (lazy load triggered)")
        self._model = SentenceTransformer(
            model_name, device="cpu"
        )  # Force CPU for now as per plan context

    @property
    def is_loaded(self) -> bool:
        """Check if the model is loaded without triggering lazy load."""
        return self._model is not None

    @property
    def dimension(self) -> int:
        """Vector dimension size (384 for paraphrase-multilingual-MiniLM-L12-v2)."""
        return 384

    async def embed(self, texts: list[str], normalize: bool = True) -> list[list[float]]:
        """Embed texts into vectors. Triggers lazy model load on first call."""
        self._ensure_model_loaded()

        vectors = self._model.encode(texts, convert_to_numpy=True, normalize_embeddings=normalize)
        if isinstance(vectors, np.ndarray):
            return vectors.tolist()
        return vectors


class LiteLLMEmbedder:
    """Embedder using LiteLLM proxy (OpenRouter API).

    Uses voyageai/voyage-multilingual-2 which produces 1024-dimensional vectors.
    """

    def __init__(self, litellm_client: "LiteLLMClient") -> None:
        self._client = litellm_client
        self._dimension = 1024  # voyage-multilingual-2

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts using LiteLLM proxy."""
        return await self._client.embed(texts)

    @property
    def dimension(self) -> int:
        """Vector dimension size (1024 for voyage-multilingual-2)."""
        return self._dimension


# Global instance for easy access
_embedder = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder


__all__ = ["Embedder", "LiteLLMEmbedder", "get_embedder"]
