"""Semantic memory integration backed by Qdrant."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import uuid4

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.conversions.common_types import Distance, VectorParams
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import PointStruct

from .config import Settings

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
        self._client: QdrantClient | None = None
        self._ensure_client()

    def _ensure_client(self) -> None:
        try:
            self._client = QdrantClient(
                url=str(self._settings.qdrant_url), api_key=self._settings.qdrant_api_key
            )
            try:
                self._client.get_collection(self._settings.qdrant_collection)
            except UnexpectedResponse:
                self._client.create_collection(
                    collection_name=self._settings.qdrant_collection,
                    vectors_config=VectorParams(
                        size=768, distance=Distance.COSINE  # type: ignore[attr-defined]
                    ),
                )
        except Exception as exc:  # pragma: no cover - depends on infra
            LOGGER.warning("Unable to initialise Qdrant client: %s", exc)
            self._client = None

    def add_records(self, records: Iterable[MemoryRecord]) -> None:
        """Persist a batch of semantic memories."""

        if not self._client:
            LOGGER.debug("Skipping memory persistence because Qdrant is unavailable")
            return

        points = []
        for record in records:
            points.append(
                PointStruct(
                    id=uuid4().hex,
                    vector=self._embed(record.text),
                    payload={
                        "conversation_id": record.conversation_id,
                        "text": record.text,
                    },
                )
            )
        try:
            self._client.upsert(collection_name=self._settings.qdrant_collection, points=points)
        except UnexpectedResponse as exc:  # pragma: no cover - defensive branch
            LOGGER.error("Failed to upsert memory points: %s", exc)

    def search(self, query: str, limit: int = 5) -> list[MemoryRecord]:
        """Return the most relevant stored memories for the given query."""

        if not self._client:
            return []

        vector = self._embed(query)
        try:
            results = self._client.search(
                collection_name=self._settings.qdrant_collection,
                query_vector=vector,
                limit=limit,
            )
        except UnexpectedResponse as exc:  # pragma: no cover - defensive
            LOGGER.error("Memory search failed: %s", exc)
            return []

        records: list[MemoryRecord] = []
        for match in results:
            payload = match.payload or {}
            text = str(payload.get("text", ""))
            conversation_id = str(payload.get("conversation_id", ""))
            if text:
                records.append(MemoryRecord(conversation_id=conversation_id, text=text))
        return records

    @staticmethod
    def _embed(text: str) -> list[float]:
        """Basic embedding strategy derived from character codes.

        The production system should rely on a dedicated embedding model.
        Here we provide a deterministic embedding suitable for local testing and
        unit tests where heavy inference is undesirable.
        """

        if not text:
            return [0.0] * 768
        # Simple deterministic embedding using character ordinals.
        values = np.frombuffer(text.encode("utf-8"), dtype=np.uint8)
        tiled = np.resize(values, 768)
        norm = np.linalg.norm(tiled)
        if norm == 0:
            return tiled.astype(float).tolist()
        return (tiled / norm).astype(float).tolist()


__all__ = ["MemoryStore", "MemoryRecord"]
