"""Embedder HTTP client used by the agent memory store."""

from __future__ import annotations

from collections.abc import Sequence

import httpx


class EmbedderError(Exception):
    """Raised when the embedder service returns an unexpected response."""


class EmbedderClient:
    """Simple HTTP client for the embedder service."""

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def embed(self, inputs: Sequence[str]) -> list[list[float]]:
        """Return vectors for each provided input string."""

        if not inputs:
            return []
        payload = {"inputs": list(inputs), "normalize": True}
        with httpx.Client(base_url=self._base_url, timeout=self._timeout) as client:
            response = client.post("/embed", json=payload)
            try:
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise EmbedderError("embedder request failed") from exc
            data = response.json()
        vectors = data.get("vectors")
        if not isinstance(vectors, list):
            raise EmbedderError("unexpected embedder payload")
        processed: list[list[float]] = []
        for vector in vectors:
            if not isinstance(vector, list):
                raise EmbedderError("unexpected vector shape")
            processed.append([float(value) for value in vector])
        return processed

    def embed_one(self, text: str) -> list[float]:
        """Return the vector generated for a single string."""

        if not text:
            return []
        vectors = self.embed([text])
        if not vectors:
            raise EmbedderError("embedder returned no vectors")
        return vectors[0]
