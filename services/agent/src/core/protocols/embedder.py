"""Protocol for embedding services."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class IEmbedder(Protocol):
    """Abstract interface for text embedding services.

    This protocol defines the contract that embedding implementations must follow.
    Implementations use the LiteLLM proxy for embedding requests.
    """

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts into vectors.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors, one per input text.
        """
        ...

    @property
    def dimension(self) -> int:
        """Vector dimension size."""
        ...


__all__ = ["IEmbedder"]
