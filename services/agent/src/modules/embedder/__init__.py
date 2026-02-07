"""Embedder module using LiteLLM proxy.

Routes embedding requests through the LiteLLM proxy to OpenRouter,
using qwen/qwen3-embedding-8b which produces 4096-dimensional vectors.
"""

import logging

from core.core.litellm_client import LiteLLMClient

logger = logging.getLogger(__name__)


class LiteLLMEmbedder:
    """Embedder using LiteLLM proxy for OpenRouter embeddings.

    Routes through LiteLLM for centralized logging and cost tracking.
    Uses qwen/qwen3-embedding-8b (4096-dimensional, multilingual).
    """

    def __init__(self, client: LiteLLMClient, *, model: str = "embedder") -> None:
        self._client = client
        self._model = model
        self._dimension = 4096

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts via LiteLLM proxy."""
        return await self._client.embed(texts, model=self._model)

    @property
    def dimension(self) -> int:
        """Vector dimension size (4096 for qwen3-embedding-8b)."""
        return self._dimension


__all__ = ["LiteLLMEmbedder"]
