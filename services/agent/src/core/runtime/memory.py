"""Semantic memory integration backed by Qdrant."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID, uuid4

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    HasIdCondition,
    HasVectorCondition,
    IsEmptyCondition,
    IsNullCondition,
    MatchValue,
    NestedCondition,
    PointStruct,
    VectorParams,
)

from core.providers import get_embedder
from core.runtime.config import Settings

LOGGER = logging.getLogger(__name__)

# Cache for verified Qdrant collections to avoid redundant get_collection() calls
_verified_collections: set[str] = set()


@dataclass(slots=True)
class MemoryRecord:
    """Representation of a semantic memory snippet."""

    conversation_id: str
    text: str


class MemoryStore:
    """Persist conversations inside Qdrant for long-term recall.

    Supports context isolation - all memories are stored with a context_id
    and searches are filtered to only return memories from the same context.
    """

    def __init__(
        self,
        settings: Settings,
        context_id: UUID | None = None,
        client: AsyncQdrantClient | None = None,
    ) -> None:
        """Initialize memory store.

        Args:
            settings: Application settings
            context_id: Context UUID for multi-tenant isolation.
                       If None, context filtering is disabled (backward compatibility).
            client: Optional pre-created AsyncQdrantClient to share across requests.
                   If None, a new client will be created (backward compatibility).

        SECURITY NOTE: context_id should always be provided for user-facing operations
        to ensure proper tenant isolation. The None case is only for backwards
        compatibility and internal/admin operations.
        """
        self._settings = settings
        self._context_id = context_id
        self._client: AsyncQdrantClient | None = client
        self._owns_client = client is None  # Track if we created the client

        # SECURITY: Warn if context_id is None - this disables tenant isolation
        if context_id is None:
            import inspect

            frame = inspect.currentframe()
            caller = inspect.getouterframes(frame)[1] if frame else None
            caller_loc = f"{caller.filename}:{caller.lineno}" if caller else "unknown"
            LOGGER.warning(
                "MemoryStore initialized without context_id - tenant isolation disabled. "
                "This is expected for admin/test operations. Caller: %s",
                caller_loc,
            )

    async def ainit(self) -> None:  # Async initialization method
        await self._async_ensure_client()

    async def _async_ensure_client(self) -> None:
        # If client was provided externally, just ensure collection exists
        if self._client is not None:
            # Skip verification if collection already verified
            if self._settings.qdrant_collection in _verified_collections:
                return

            try:
                await self._client.get_collection(self._settings.qdrant_collection)
                _verified_collections.add(self._settings.qdrant_collection)
            except UnexpectedResponse:
                await self._client.create_collection(
                    collection_name=self._settings.qdrant_collection,
                    vectors_config=VectorParams(
                        size=get_embedder().dimension, distance=Distance.COSINE
                    ),
                )
                _verified_collections.add(self._settings.qdrant_collection)
            return

        # Create a new client if we own it
        try:
            self._client = AsyncQdrantClient(
                url=str(self._settings.qdrant_url),
                api_key=self._settings.qdrant_api_key,
                timeout=30,  # SECURITY: Prevent hanging under load
            )
            # Skip verification if collection already verified
            if self._settings.qdrant_collection not in _verified_collections:
                try:
                    # Use await for async client methods
                    await self._client.get_collection(self._settings.qdrant_collection)
                    _verified_collections.add(self._settings.qdrant_collection)
                except UnexpectedResponse:
                    await self._client.create_collection(
                        collection_name=self._settings.qdrant_collection,
                        vectors_config=VectorParams(
                            size=get_embedder().dimension, distance=Distance.COSINE
                        ),
                    )
                    _verified_collections.add(self._settings.qdrant_collection)
        except Exception as exc:  # pragma: no cover - depends on infra
            LOGGER.warning("Unable to initialise Qdrant client: %s", exc)
            self._client = None

    async def add_records(self, records: Iterable[MemoryRecord]) -> None:
        """Persist a batch of semantic memories."""

        if not self._client:
            LOGGER.debug("Skipping memory persistence because Qdrant is unavailable")
            return

        record_list = list(records)
        if not record_list:
            return
        vectors = await self._async_embed_texts([record.text for record in record_list])

        if len(vectors) != len(record_list):
            LOGGER.warning(
                "Embedding count mismatch (got %d vectors for %d records). "
                "Skipping memory persistence.",
                len(vectors),
                len(record_list),
            )
            return

        points = []
        for record, vector in zip(record_list, vectors, strict=False):
            payload = {
                "conversation_id": record.conversation_id,
                "text": record.text,
            }

            # Add context_id for multi-tenant isolation
            if self._context_id:
                payload["context_id"] = str(self._context_id)

            points.append(
                PointStruct(
                    id=uuid4().hex,
                    vector=vector,
                    payload=payload,
                )
            )
        try:
            # Use await for async client methods
            await self._client.upsert(
                collection_name=self._settings.qdrant_collection, points=points
            )
        except UnexpectedResponse as exc:  # pragma: no cover - defensive branch
            LOGGER.error("Failed to upsert memory points: %s", exc)

    async def search(
        self, query: str, limit: int = 5, conversation_id: str | None = None
    ) -> list[MemoryRecord]:
        """Return the most relevant stored memories for the given query."""

        client = self._client
        if not client:
            return []

        vectors = await self._async_embed_texts([query])
        if not vectors:
            return []
        vector = vectors[0]

        # Build filter conditions for context and conversation isolation
        filter_conditions: list[
            FieldCondition
            | Filter
            | IsEmptyCondition
            | IsNullCondition
            | HasIdCondition
            | HasVectorCondition
            | NestedCondition
        ] = []

        # Always filter by context_id if set (multi-tenant isolation)
        if self._context_id:
            filter_conditions.append(
                FieldCondition(
                    key="context_id",
                    match=MatchValue(value=str(self._context_id)),
                )
            )

        # Optionally filter by conversation_id
        if conversation_id:
            filter_conditions.append(
                FieldCondition(
                    key="conversation_id",
                    match=MatchValue(value=conversation_id),
                )
            )

        query_filter: Filter | None = None
        if filter_conditions:
            query_filter = Filter(must=filter_conditions)

        LOGGER.info(
            f"Searching memory for query='{query}' "
            f"context_id='{self._context_id or 'all'}' "
            f"conversation_id='{conversation_id or 'all'}'"
        )

        try:
            # Use await for async client methods
            response = await client.query_points(
                collection_name=self._settings.qdrant_collection,
                query=vector,
                limit=limit,
                query_filter=query_filter,
            )
            results = response.points
        except UnexpectedResponse as exc:  # pragma: no cover - defensive
            LOGGER.error("Memory search failed: %s", exc)
            return []

        LOGGER.info(f"Found {len(results)} memory records")

        records: list[MemoryRecord] = []
        for match in results:
            payload = match.payload or {}
            text = str(payload.get("text", ""))
            conversation_id = str(payload.get("conversation_id", ""))
            if text:
                records.append(MemoryRecord(conversation_id=conversation_id, text=text))
        return records

    async def _async_embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of strings via the registered embedder."""

        if not texts:
            return []
        try:
            vectors = await get_embedder().embed(texts)
            return vectors
        except Exception as exc:
            LOGGER.warning("Embedder request failed: %s", exc)
        return []

    async def close(self) -> None:
        """Close the Qdrant client if we own it.

        Only closes the client if it was created by this MemoryStore instance.
        Shared clients should be closed by their owner (e.g., ServiceFactory).
        """
        if self._client is not None and self._owns_client:
            await self._client.close()
            self._client = None


__all__ = ["MemoryStore", "MemoryRecord"]
