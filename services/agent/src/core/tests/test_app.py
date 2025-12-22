from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from core.core.app import create_app
from core.core.config import Settings
from core.core.litellm_client import LiteLLMClient
from core.core.memory import MemoryStore
from core.core.service import AgentService


class MockLiteLLMClient:
    async def generate(self, messages: Iterable[Any], model: str | None = None) -> str:
        sequence = list(messages)
        content = (
            sequence[-1].content if hasattr(sequence[-1], "content") else sequence[-1]["content"]
        )
        return "reply:" + str(content)

    async def plan(self, messages: Iterable[Any], model: str | None = None) -> str:
        return json.dumps(
            {
                "steps": [
                    {
                        "id": "memory",
                        "label": "Fetch memories",
                        "executor": "agent",
                        "action": "memory",
                    },
                    {
                        "id": "completion",
                        "label": "Compose assistant reply",
                        "executor": "litellm",
                        "action": "completion",
                    },
                ]
            }
        )


class DummyMemory:
    def __init__(self) -> None:
        self.persisted: list[str] = []

    async def ainit(self) -> None:
        pass

    async def search(
        self, query: str, limit: int = 5, conversation_id: str | None = None
    ) -> list[Any]:
        return []

    async def add_records(self, records: Iterable[Any]) -> None:
        for record in records:
            if hasattr(record, "text"):
                self.persisted.append(record.text)


async def build_service(tmp_path: Path) -> AgentService:
    settings = Settings(sqlite_state_path=tmp_path / "state.sqlite")
    memory = cast(MemoryStore, DummyMemory())
    await memory.ainit()
    service = AgentService(
        settings=settings,
        litellm=cast(LiteLLMClient, MockLiteLLMClient()),
        memory=memory,
    )
    return service


@pytest.mark.asyncio
async def test_chat_completions_roundtrip(tmp_path: Path) -> None:
    service = await build_service(tmp_path)
    app = create_app(service._settings, service=service)
    client = TestClient(app)

    payload = {
        "model": "agent-model",
        "messages": [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "Hello"},
        ],
        "metadata": {"tools": []},
    }

    response = client.post("/v1/chat/completions", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["choices"][0]["message"]["content"].startswith("reply:")
    assert data["metadata"]["tools"] == []
    assert isinstance(data["steps"], list)
    assert data["metadata"]["steps"] == data["steps"]
    assert data["choices"][0]["message"]["metadata"]["steps"] == data["steps"]

    follow_payload = {
        "model": "agent-model",
        "conversation_id": data["id"],
        "messages": [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "Hello"},
            {
                "role": "assistant",
                "content": data["choices"][0]["message"]["content"],
            },
            {"role": "user", "content": "How are you?"},
        ],
    }

    second = client.post("/v1/chat/completions", json=follow_payload)
    assert second.status_code == 200
    follow_data = second.json()
    assert follow_data["id"] == data["id"]
    assert follow_data["choices"][0]["message"]["content"].startswith("reply:")
    assert follow_data["metadata"]["steps"] == follow_data["steps"]
