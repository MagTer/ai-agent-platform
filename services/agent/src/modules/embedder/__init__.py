import logging
import os
from typing import Any

import httpx
import numpy as np

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


class OpenRouterEmbedder:
    """Embedder using OpenRouter API directly.

    Uses qwen/qwen3-embedding-8b which produces 4096-dimensional vectors.
    Supports Swedish and English text (multilingual).
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "qwen/qwen3-embedding-8b",
    ) -> None:
        self._api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not self._api_key:
            raise ValueError("OPENROUTER_API_KEY environment variable required")
        self._model = model
        self._dimension = 4096  # qwen3-embedding-8b
        self._client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            timeout=60.0,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts using OpenRouter API."""
        response = await self._client.post(
            "/embeddings",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "HTTP-Referer": "https://ai-agent-platform.local",
                "Content-Type": "application/json",
            },
            json={"model": self._model, "input": texts},
        )
        if response.status_code >= 400:
            logger.error("OpenRouter embedding error %s: %s", response.status_code, response.text)
            raise RuntimeError(f"Embedding failed: {response.status_code} - {response.text[:200]}")
        data = response.json()
        return [item["embedding"] for item in data["data"]]

    @property
    def dimension(self) -> int:
        """Vector dimension size (4096 for qwen3-embedding-8b)."""
        return self._dimension

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


# Global instance for easy access
_embedder = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder


__all__ = ["Embedder", "OpenRouterEmbedder", "get_embedder"]
