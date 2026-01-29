"""Protocol for RAG (Retrieval-Augmented Generation) services."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IRAGManager(Protocol):
    """Abstract interface for RAG operations.

    This protocol defines the contract for retrieval and ingestion
    of documents into a vector database for RAG workflows.
    """

    async def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        filters: dict[str, Any] | None = None,
        collection_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve relevant documents for a query.

        Uses MMR (Maximal Marginal Relevance) for diversity in results.

        Args:
            query: The search query.
            top_k: Maximum number of results to return.
            filters: Optional metadata filters.
            collection_name: Target collection (optional override).

        Returns:
            List of document dicts containing:
                - uri: Document identifier
                - text: Document content
                - score: Relevance score
                - source: Origin of the document
        """
        ...

    async def ingest_document(
        self,
        content: str,
        metadata: dict[str, Any],
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
    ) -> int:
        """Ingest a document into the vector store.

        Chunks the content and stores embeddings with metadata.

        Args:
            content: Document text content.
            metadata: Metadata to attach to all chunks.
            chunk_size: Size of each chunk in characters.
            chunk_overlap: Overlap between chunks.

        Returns:
            Number of chunks ingested.
        """
        ...

    async def close(self) -> None:
        """Clean up resources."""
        ...


__all__ = ["IRAGManager"]
