"""Semantic memory integration backed by Qdrant."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import uuid4

import numpy as np
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from .config import Settings
from .embedder import EmbedderClient, EmbedderError

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class MemoryRecord:
    """Representation of a semantic memory snippet."""

    conversation_id: str
    text: str


class MemoryStore:
    """Persist conversations inside Qdrant for long-term recall."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._vector_size = settings.qdrant_vector_size
        self._embedder = EmbedderClient(str(settings.embedder_url))
        self._client: AsyncQdrantClient | None = None
        # _ensure_client is now async, so it needs to be awaited.
        # This means __init__ cannot directly call it with await.
        # It needs to be called after instantiation in an async context,
        # or we could make MemoryStore a dependency that's created async.
        # For now, I'll remove the call from __init__ and add a note.

    async def ainit(self) -> None:  # Async initialization method
        await self._async_ensure_client()

    async def _async_ensure_client(self) -> None:
        try:
            self._client = AsyncQdrantClient(
                url=str(self._settings.qdrant_url),
                api_key=self._settings.qdrant_api_key,
            )
            try:
                # Use await for async client methods
                await self._client.get_collection(self._settings.qdrant_collection)
            except UnexpectedResponse:
                await self._client.create_collection(
                    collection_name=self._settings.qdrant_collection,
                    vectors_config=VectorParams(
                        size=self._vector_size, distance=Distance.COSINE  # type: ignore[attr-defined]
                    ),
                )
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
        points = []
        for record, vector in zip(record_list, vectors, strict=True):
            points.append(
                PointStruct(
                    id=uuid4().hex,
                    vector=vector,
                    payload={
                        "conversation_id": record.conversation_id,
                        "text": record.text,
                    },
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
        query_filter: Filter | None = None
        if conversation_id:
            query_filter = Filter(
                must=[
                    FieldCondition(
                        key="conversation_id",
                        match=MatchValue(value=conversation_id),
                    )
                ]
            )

        LOGGER.info(
            f"Searching memory for query='{query}' conversation_id='{conversation_id or 'all'}'"
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
        """Embed a batch of strings via the embedder with a local fallback."""

        if not texts:
            return []
        try:
            # Use await for async embedder methods
            vectors = await self._embedder.embed(texts)
            return vectors
        except EmbedderError as exc:
            LOGGER.warning("Embedder request failed: %s", exc)
        return []



__all__ = ["MemoryStore", "MemoryRecord"]
