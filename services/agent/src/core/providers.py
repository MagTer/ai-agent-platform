"""Dependency provider for injecting module implementations into core.

This module provides a simple service locator pattern for injecting
implementations of core protocols at application startup.

The interfaces layer (e.g., app.py) is responsible for:
1. Importing concrete implementations from modules/
2. Registering them via set_* functions
3. Core tools then access them via get_* functions

This breaks the circular dependency while maintaining loose coupling.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.protocols import ICodeIndexer, IEmbedder, IFetcher, IRAGManager

LOGGER = logging.getLogger(__name__)

# Module-level singletons (set by interfaces layer at startup)
_embedder: IEmbedder | None = None
_fetcher: IFetcher | None = None
_rag_manager: IRAGManager | None = None
_code_indexer_factory: type[ICodeIndexer] | None = None


class ProviderError(Exception):
    """Raised when a required provider is not configured."""

    pass


# --- Embedder ---
def set_embedder(embedder: IEmbedder) -> None:
    """Register the embedder implementation."""
    global _embedder
    _embedder = embedder
    LOGGER.info("Embedder provider registered")


def get_embedder() -> IEmbedder:
    """Get the registered embedder. Raises if not configured."""
    if _embedder is None:
        raise ProviderError("Embedder not configured. Call set_embedder() at startup.")
    return _embedder


# --- Fetcher ---
def set_fetcher(fetcher: IFetcher) -> None:
    """Register the fetcher implementation."""
    global _fetcher
    _fetcher = fetcher
    LOGGER.info("Fetcher provider registered")


def get_fetcher() -> IFetcher:
    """Get the registered fetcher. Raises if not configured."""
    if _fetcher is None:
        raise ProviderError("Fetcher not configured. Call set_fetcher() at startup.")
    return _fetcher


# --- RAG Manager ---
def set_rag_manager(rag: IRAGManager) -> None:
    """Register the RAG manager implementation."""
    global _rag_manager
    _rag_manager = rag
    LOGGER.info("RAG Manager provider registered")


def get_rag_manager() -> IRAGManager:
    """Get the registered RAG manager. Raises if not configured."""
    if _rag_manager is None:
        raise ProviderError("RAG Manager not configured. Call set_rag_manager() at startup.")
    return _rag_manager


# --- Code Indexer Factory ---
def set_code_indexer_factory(factory: type[ICodeIndexer]) -> None:
    """Register the code indexer class (factory pattern for path-specific instances)."""
    global _code_indexer_factory
    _code_indexer_factory = factory
    LOGGER.info("Code Indexer factory registered")


def get_code_indexer_factory() -> type[ICodeIndexer]:
    """Get the code indexer factory. Raises if not configured."""
    if _code_indexer_factory is None:
        raise ProviderError(
            "Code Indexer not configured. Call set_code_indexer_factory() at startup."
        )
    return _code_indexer_factory


__all__ = [
    "ProviderError",
    "set_embedder",
    "get_embedder",
    "set_fetcher",
    "get_fetcher",
    "set_rag_manager",
    "get_rag_manager",
    "set_code_indexer_factory",
    "get_code_indexer_factory",
]
