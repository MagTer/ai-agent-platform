"""Embedder module using OpenRouter API.

Uses qwen/qwen3-embedding-8b which produces 4096-dimensional vectors.
Supports multilingual text (Swedish and English).
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)


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


__all__ = ["OpenRouterEmbedder"]
