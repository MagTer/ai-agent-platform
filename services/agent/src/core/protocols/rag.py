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
        chunker: Any | None = None,
        document_type: str = "prose",
    ) -> int:
        """Ingest a document into the vector store.

        Uses semantic chunking with configurable chunker for structure-aware
        splitting. Content-type routing via document_type parameter.

        Args:
            content: Document text content.
            metadata: Metadata to attach to all chunks.
            chunker: Optional SemanticChunker instance (uses instance default if not provided).
            document_type: Type of document ('markdown' or 'prose') for content-type routing.

        Returns:
            Number of chunks ingested.
        """
        ...

    async def close(self) -> None:
        """Clean up resources."""
        ...


__all__ = ["IRAGManager"]
