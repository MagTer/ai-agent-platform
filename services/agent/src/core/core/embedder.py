import logging
from collections.abc import Sequence

import httpx


class EmbedderError(Exception):
    """Raised when the embedder service returns an unexpected response."""


LOGGER = logging.getLogger(__name__)


class EmbedderClient:
    """Simple HTTP client for the embedder service."""

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def embed(self, inputs: Sequence[str]) -> list[list[float]]:
        """Return vectors for each provided input string."""

        if not inputs:
            return []
        payload = {"inputs": list(inputs), "normalize": True}
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
            try:
                response = await client.post("/embed", json=payload)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                LOGGER.error("Embedder request failed: %s", exc)
                raise EmbedderError("embedder request failed") from exc
            data = response.json()
        vectors = data.get("vectors")
        if not isinstance(vectors, list):
            LOGGER.error("Unexpected embedder payload: %s", data)
            raise EmbedderError("unexpected embedder payload")
        processed: list[list[float]] = []
        for vector in vectors:
            if not isinstance(vector, list):
                LOGGER.error("Unexpected vector shape: %s", vector)
                raise EmbedderError("unexpected vector shape")
            processed.append([float(value) for value in vector])
        return processed

    async def embed_one(self, text: str) -> list[float]:
        """Return the vector generated for a single string."""

        if not text:
            return []
        vectors = await self.embed([text])
        if not vectors:
            raise EmbedderError("embedder returned no vectors")
        return vectors[0]
