from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import cast

import pytest

from agent.core.config import Settings
from agent.core.litellm_client import LiteLLMClient
from agent.core.memory import MemoryRecord, MemoryStore
from agent.core.models import AgentMessage, AgentRequest
from agent.core.service import AgentService


class MockLiteLLMClient(LiteLLMClient):
    def __init__(self) -> None:  # pragma: no cover - behaviour mocked
        pass

    async def generate(self, messages: Iterable[AgentMessage]) -> str:  # type: ignore[override]
        sequence = list(messages)
        return "response: " + sequence[-1].content


class DummyMemory:
    def __init__(self) -> None:
        self.records: list[str] = []

    def search(self, query: str, limit: int = 5) -> list[MemoryRecord]:  # noqa: D401
        return []

    def add_records(self, records: Iterable[MemoryRecord]) -> None:
        for record in records:
            self.records.append(record.text)


@pytest.mark.asyncio
async def test_agent_service_roundtrip(tmp_path: Path) -> None:
    settings = Settings(sqlite_state_path=tmp_path / "state.sqlite")
    service = AgentService(
        settings=settings,
        litellm=cast(LiteLLMClient, MockLiteLLMClient()),
        memory=cast(MemoryStore, DummyMemory()),
    )

    request = AgentRequest(prompt="Hello")
    response = await service.handle_request(request)

    assert response.response.startswith("response:")
    assert response.conversation_id
    assert len(response.messages) == 2
    assert any(step["type"] == "completion" for step in response.steps)

    follow_up = AgentRequest(
        prompt="How are you?",
        conversation_id=response.conversation_id,
        messages=response.messages,
    )
    follow_response = await service.handle_request(follow_up)

    assert follow_response.conversation_id == response.conversation_id
    # prompt history should now contain previous assistant reply
    assert any(message.role == "assistant" for message in follow_response.messages)
    assert any(step["type"] == "completion" for step in follow_response.steps)
