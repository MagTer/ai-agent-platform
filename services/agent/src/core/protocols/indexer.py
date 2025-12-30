"""Protocol for code indexing services."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class ICodeIndexer(Protocol):
    """Abstract interface for code indexing operations.

    This protocol defines the contract for indexing code files
    into a vector database for semantic code search.
    """

    def __init__(self, root_path: Path) -> None:
        """Initialize the indexer with a root path.

        Args:
            root_path: Root directory to index.
        """
        ...

    async def index_file(self, file_path: Path) -> None:
        """Index a single file.

        Reads the file, splits into chunks, embeds, and stores.
        Skips unchanged files based on content hash.

        Args:
            file_path: Path to the file to index.
        """
        ...

    async def scan_and_index(self) -> None:
        """Scan the root path and index all relevant files.

        Respects .gitignore patterns and skips hidden directories.
        """
        ...

    async def close(self) -> None:
        """Clean up resources."""
        ...


__all__ = ["ICodeIndexer"]
