"""
Semantic chunking module using Chonkie library.
Provides structure-aware chunking with content-type routing.
"""
from typing import Any

from chonkie import RecursiveChunker


class SemanticChunker:
    """
    Splits documents into semantically meaningful chunks using Chonkie's RecursiveChunker.

    Content-type routing:
    - Markdown documents use heading-aware splitting (RecursiveChunker.from_recipe('markdown'))
    - Prose uses standard chunking with sentence boundaries

    Note: Chonkie's chunker uses chunk_size in tokens (not characters) and does not support
    explicit chunk_overlap. Overlap can be simulated by merging chunks manually if needed.
    """

    def __init__(self, chunk_size: int = 1000) -> None:
        """
        Initialize the SemanticChunker.

        Args:
            chunk_size: Maximum size of each chunk in tokens
        """
        self.chunk_size = chunk_size

        # Initialize splitters for different content types
        # Markdown recipe handles headings and structure automatically
        self.markdown_splitter = RecursiveChunker.from_recipe(
            "markdown", chunk_size=chunk_size
        )
        # Prose uses default rules with sentence boundary detection
        self.prose_splitter = RecursiveChunker(chunk_size=chunk_size)

    def split_text(
        self, text: str, document_type: str = "prose", section_title: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Split text into semantically meaningful chunks.

        Args:
            text: The text to split
            document_type: Type of document ('markdown' or 'prose')
            section_title: Optional section title to include in metadata

        Returns:
            List of chunks, each containing text and metadata
        """
        if document_type == "markdown":
            splitter = self.markdown_splitter
        else:
            splitter = self.prose_splitter

        # Use Chonkie's chunk method (not split)
        chunks = splitter.chunk(text)

        # Build result with metadata
        result = []
        for i, chunk_text in enumerate(chunks):
            chunk_data: dict[str, Any] = {
                "text": chunk_text,
                "metadata": {
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                    "chunk_type": "semantic",
                    "document_type": document_type,
                },
            }

            if section_title:
                chunk_data["metadata"]["section_title"] = section_title

            result.append(chunk_data)

        return result

    def split_file(
        self, content: str, document_type: str = "prose", section_title: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Split file content into chunks (alias for split_text with document_type routing).

        Args:
            content: The content to split
            document_type: Type of document ('markdown' or 'prose')
            section_title: Optional section title to include in metadata

        Returns:
            List of chunks with metadata
        """
        return self.split_text(content, document_type, section_title)
