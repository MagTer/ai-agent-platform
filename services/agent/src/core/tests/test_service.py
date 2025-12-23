from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from core.core.config import Settings
from core.core.litellm_client import LiteLLMClient
from core.core.memory import MemoryRecord, MemoryStore
from core.core.service import AgentService
from core.db import Context, Conversation
from core.tools import ToolRegistry
from core.tools.base import Tool
from shared.models import AgentMessage, AgentRequest


class MockLiteLLMClient(LiteLLMClient):
    def __init__(
        self,
        *,
        plan_output: str | None = None,
    ) -> None:  # pragma: no cover - behaviour mocked
        self._plan_output = plan_output or json.dumps(
            {
                "steps": [
                    {
                        "id": "memory-step",
                        "label": "Retrieve memories",
                        "executor": "agent",
                        "action": "memory",
                        "args": {"query": "default"},
                    },
                    {
                        "id": "completion-step",
                        "label": "Return answer",
                        "executor": "litellm",
                        "action": "completion",
                    },
                ]
            }
        )

    async def plan(self, messages: Iterable[AgentMessage], *, model: str | None = None) -> str:
        return self._plan_output

    async def generate(self, messages: Iterable[AgentMessage], *, model: str | None = None) -> str:
        sequence = list(messages)
        return "response: " + sequence[-1].content


class DummyMemory:
    def __init__(self) -> None:
        self.records: list[str] = []

    async def ainit(self) -> None:
        pass

    async def search(
        self, query: str, limit: int = 5, conversation_id: str | None = None
    ) -> list[MemoryRecord]:  # noqa: D401
        return []

    async def add_records(self, records: Iterable[MemoryRecord]) -> None:
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

    # Mock Session
    session = AsyncMock()
    mock_ctx = MagicMock(id="default-ctx", default_cwd="/tmp")  # noqa: S108

    def get_side_effect(model: Any, id: Any) -> Any:
        if model == Conversation:
            return None
        if model == Context:
            return mock_ctx
        return None

    session.get.side_effect = get_side_effect
    # Mock execute(Context) -> context
    # Mock execute(Session) -> None (create new)
    # Mock execute(Message) -> []

    # We need to structure the mock to handle chained calls like result.scalar_one_or_none()
    # safe defaults:
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = MagicMock(
        id="default-ctx", default_cwd="/tmp"  # noqa: S108
    )
    mock_result.scalars.return_value.all.return_value = []
    session.execute.return_value = mock_result

    request = AgentRequest(prompt="Hello")
    response = await service.handle_request(request, session=session)

    assert response.response.startswith("response:")
    assert response.conversation_id
    assert len(response.messages) == 2
    assert any(step["type"] == "plan_step" for step in response.steps)
    assert response.metadata["plan"]["steps"][-1]["action"] == "completion"

    follow_up = AgentRequest(
        prompt="How are you?",
        conversation_id=response.conversation_id,
        messages=response.messages,
    )
    follow_response = await service.handle_request(follow_up, session=session)

    assert follow_response.conversation_id == response.conversation_id
    # prompt history should now contain previous assistant reply
    assert any(message.role == "assistant" for message in follow_response.messages)
    assert any(step["type"] == "plan_step" for step in follow_response.steps)


class DummyTool(Tool):
    name = "dummy_tool"
    description = "Dummy helper used in tests."

    async def run(self, *, target: str) -> str:
        return f"dummy result for {target}"


@pytest.mark.asyncio
async def test_plan_driven_flow(tmp_path: Path) -> None:
    plan_definition = {
        "description": "Test plan flow",
        "steps": [
            {
                "id": "memory-1",
                "label": "Fetch context",
                "executor": "agent",
                "action": "memory",
                "args": {"query": "Hello world"},
            },
            {
                "id": "tool-1",
                "label": "Use dummy helper",
                "executor": "agent",
                "action": "tool",
                "tool": "dummy_tool",
                "args": {"target": "alpha"},
            },
            {
                "id": "completion-1",
                "label": "Compose answer",
                "executor": "litellm",
                "action": "completion",
            },
        ],
    }
    settings = Settings(sqlite_state_path=tmp_path / "state.sqlite")
    registry = ToolRegistry([DummyTool()])
    service = AgentService(
        settings=settings,
        litellm=cast(LiteLLMClient, MockLiteLLMClient(plan_output=json.dumps(plan_definition))),
        memory=cast(MemoryStore, DummyMemory()),
        tool_registry=registry,
    )

    # Mock Session
    session = AsyncMock()
    mock_result = MagicMock()
    # Context
    mock_result.scalar_one_or_none.return_value = MagicMock(
        id="default-ctx", default_cwd="/tmp"  # noqa: S108
    )
    # History
    mock_result.scalars.return_value.all.return_value = []
    session.execute.return_value = mock_result
    session.execute.return_value = mock_result

    mock_ctx = MagicMock(id="default-ctx", default_cwd="/tmp")  # noqa: S108

    def get_side_effect(model: Any, id: Any) -> Any:
        if model == Conversation:
            return None
        if model == Context:
            return mock_ctx
        return None

    session.get.side_effect = get_side_effect

    request = AgentRequest(prompt="Hello world")
    response = await service.handle_request(request, session=session)

    assert response.response == "response: Hello world"
    assert response.metadata["plan"]["description"] == "Test plan flow"
    assert any(step.get("tool") == "dummy_tool" for step in response.steps)
    assert any(result["name"] == "dummy_tool" for result in response.metadata["tool_results"])
