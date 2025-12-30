"""Protocol for embedding services."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class IEmbedder(Protocol):
    """Abstract interface for text embedding services.

    This protocol defines the contract that embedding implementations must follow.
    Implementations can use local models (SentenceTransformers) or remote APIs.
    """

    def embed(self, texts: list[str], normalize: bool = True) -> list[list[float]]:
        """Embed a list of texts into vectors.

        Args:
            texts: List of text strings to embed.
            normalize: Whether to normalize the output vectors.

        Returns:
            List of embedding vectors, one per input text.
        """
        ...


__all__ = ["IEmbedder"]
