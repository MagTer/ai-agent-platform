from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Iterable
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from core.core.config import Settings
from core.core.litellm_client import LiteLLMClient
from core.core.memory import MemoryStore
from core.core.service import AgentService
from core.db.engine import get_db
from core.db.models import Context, Conversation
from interfaces.http.app import create_app


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

    async def stream_chat(
        self,
        messages: Iterable[Any],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[Any, None]:
        sequence = list(messages)
        content = (
            sequence[-1].content if hasattr(sequence[-1], "content") else sequence[-1]["content"]
        )
        # Yield mock chunks
        yield {
            "type": "content",
            "content": "reply:" + str(content),
            "tool_call": None,
            "metadata": None,
        }

    async def aclose(self) -> None:
        pass


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
    settings = Settings(
        sqlite_state_path=tmp_path / "state.sqlite",
        environment="test",
        internal_api_key=None,
    )
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

    # Mock DB Dependency
    # Mock DB Dependency
    mock_session = AsyncMock()

    mock_context = MagicMock(id="default-ctx", default_cwd="/tmp")  # noqa: S108

    def get_side_effect(model: Any, id: Any) -> Any:
        if model == Conversation:
            return None
        if model == Context:
            return mock_context
        return None

    mock_session.get.side_effect = get_side_effect
    mock_result = MagicMock()
    # Context
    mock_result.scalar_one_or_none.return_value = MagicMock(
        id="default-ctx", default_cwd="/tmp"  # noqa: S108
    )
    # History
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_result

    async def mock_get_db() -> AsyncGenerator[Any, None]:
        yield mock_session

    app.dependency_overrides[get_db] = mock_get_db

    client = TestClient(app)

    payload = {
        "model": "agent-model",
        "messages": [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "Hello"},
        ],
        "metadata": {"tools": []},
    }

    response = client.post("/v1/agent/chat/completions", json=payload)
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

    second = client.post("/v1/agent/chat/completions", json=follow_payload)
    assert second.status_code == 200
    follow_data = second.json()
    assert follow_data["id"] == data["id"]
    assert follow_data["choices"][0]["message"]["content"].startswith("reply:")
    assert follow_data["metadata"]["steps"] == follow_data["steps"]
