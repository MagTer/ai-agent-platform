from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import count
from types import SimpleNamespace
from typing import Any

import pytest

from agent.core.config import Settings
from agent.core.memory import MemoryRecord, MemoryStore


@dataclass
class _StubSearchResult:
    payload: dict[str, Any]


class _StubQdrantClient:
    def __init__(self) -> None:
        self.upsert_calls: list[list[Any]] = []
        self.results: list[_StubSearchResult] = []

    def upsert(self, *, collection_name: str, points: Iterable[Any]) -> None:  # noqa: D401
        self.upsert_calls.append(list(points))

    def search(
        self,
        *,
        collection_name: str,
        query_vector: Iterable[float],
        limit: int,
    ) -> list[_StubSearchResult]:  # noqa: D401
        return self.results


@pytest.fixture
def memory_store(monkeypatch: pytest.MonkeyPatch) -> tuple[MemoryStore, _StubQdrantClient]:
    monkeypatch.setattr(MemoryStore, "_ensure_client", lambda self: None)
    store = MemoryStore(settings=Settings())
    stub_client = _StubQdrantClient()
    store._client = stub_client  # type: ignore[attr-defined]
    return store, stub_client


def test_add_records_generates_unique_point_ids(
    monkeypatch: pytest.MonkeyPatch, memory_store: tuple[MemoryStore, _StubQdrantClient]
) -> None:
    store, stub_client = memory_store

    counter = count(1)

    def _uuid_factory() -> SimpleNamespace:
        return SimpleNamespace(hex=f"uuid-{next(counter)}")

    monkeypatch.setattr("agent.core.memory.uuid4", _uuid_factory)

    store.add_records(
        [
            MemoryRecord(conversation_id="conv-1", text="hello"),
        ]
    )
    store.add_records(
        [
            MemoryRecord(conversation_id="conv-1", text="world"),
        ]
    )

    assert len(stub_client.upsert_calls) == 2
    first_call, second_call = stub_client.upsert_calls
    assert first_call[0].id == "uuid-1"
    assert second_call[0].id == "uuid-2"
    assert first_call[0].payload["conversation_id"] == "conv-1"
    assert second_call[0].payload["conversation_id"] == "conv-1"


def test_search_returns_all_payload_matches(memory_store: tuple[MemoryStore, _StubQdrantClient]) -> None:
    store, stub_client = memory_store
    stub_client.results = [
        _StubSearchResult(payload={"conversation_id": "conv-1", "text": "hello"}),
        _StubSearchResult(payload={"conversation_id": "conv-1", "text": "world"}),
    ]

    matches = store.search("hello", limit=5)

    assert [record.text for record in matches] == ["hello", "world"]
    assert all(record.conversation_id == "conv-1" for record in matches)
    assert len(matches) > 1
