from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Iterable
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from core.db.engine import get_db
from core.db.models import Context, Conversation
from core.runtime.config import Settings
from core.runtime.litellm_client import LiteLLMClient
from core.runtime.memory import MemoryStore
from core.runtime.service import AgentService
from core.skills.registry import Skill
from interfaces.http.app import create_app


def _make_mock_skill(name: str = "mock_skill") -> Skill:
    """Create a minimal Skill object for testing."""
    return Skill(
        name=name,
        path=Path("/mock/skills") / f"{name}.md",
        description="Mock skill for testing",
        tools=[],
        model="agentchat",
        max_turns=3,
        body_template="Answer the user's question.",
    )


def _make_mock_skill_registry(skill_name: str = "mock_skill") -> MagicMock:
    """Create a mock SkillRegistry that returns a mock skill."""
    registry = MagicMock()
    skill = _make_mock_skill(skill_name)
    registry.get.return_value = skill
    registry.get_skill_names.return_value = [skill_name]
    return registry


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
                        "id": "skill-step",
                        "label": "Run mock skill",
                        "executor": "skill",
                        "action": "skill",
                        "tool": "mock_skill",
                        "args": {},
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
        # Check if this is a planner call
        system_content = ""
        for m in sequence:
            if hasattr(m, "role") and m.role == "system":
                system_content = m.content or ""
            elif isinstance(m, dict) and m.get("role") == "system":
                system_content = m.get("content", "")
        if "PLANNER AGENT" in system_content or "You are the Planner Agent" in system_content:
            yield {
                "type": "content",
                "content": json.dumps(
                    {
                        "steps": [
                            {
                                "id": "skill-step",
                                "label": "Run mock skill",
                                "executor": "skill",
                                "action": "skill",
                                "tool": "mock_skill",
                                "args": {},
                            }
                        ]
                    }
                ),
            }
            return
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
        environment="test",
        internal_api_key=None,
    )
    memory = cast(MemoryStore, DummyMemory())
    await memory.ainit()
    service = AgentService(
        settings=settings,
        litellm=cast(LiteLLMClient, MockLiteLLMClient()),
        memory=memory,
        skill_registry=_make_mock_skill_registry(),
    )
    return service


@pytest.mark.asyncio
async def test_chat_completions_roundtrip(tmp_path: Path) -> None:
    service = await build_service(tmp_path)
    app = create_app(service._settings, service=service)

    # Mock DB Dependency
    # Mock DB Dependency
    mock_session = AsyncMock()

    mock_context = MagicMock(
        id="00000000-0000-0000-0000-000000000001", default_cwd="/tmp"  # noqa: S108
    )

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
        id="00000000-0000-0000-0000-000000000001", default_cwd="/tmp"  # noqa: S108
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
        "metadata": {"tools": [], "context_id": "00000000-0000-0000-0000-000000000001"},
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
        "metadata": {"context_id": "00000000-0000-0000-0000-000000000001"},
    }

    second = client.post("/v1/agent/chat/completions", json=follow_payload)
    assert second.status_code == 200
    follow_data = second.json()
    assert follow_data["id"] == data["id"]
    assert follow_data["choices"][0]["message"]["content"].startswith("reply:")
    assert follow_data["metadata"]["steps"] == follow_data["steps"]
