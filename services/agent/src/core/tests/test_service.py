from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Iterable
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.db import Context, Conversation
from core.runtime.config import Settings
from core.runtime.litellm_client import LiteLLMClient
from core.runtime.memory import MemoryRecord, MemoryStore
from core.runtime.service import AgentService
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
        return "response: " + (sequence[-1].content or "")

    async def stream_chat(
        self,
        messages: Iterable[AgentMessage],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[Any, None]:
        # Detect if this is a planner call (check for planner prompt markers)
        system_msg = next((m for m in messages if m.role == "system"), None)
        system_content = system_msg.content or "" if system_msg else ""

        if "PLANNER AGENT" in system_content or "You are the Planner Agent" in system_content:
            content = self._plan_output
        else:
            sequence = list(messages)
            content = "response: " + (sequence[-1].content or "")

        yield {"type": "content", "content": content}


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
    settings = Settings()
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
    assert len(response.messages) == 3
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
    settings = Settings()
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

    expected_resp = "response: Tool dummy_tool output:\ndummy result for alpha"
    assert response.response == expected_resp
    assert response.metadata["plan"]["description"] == "Test plan flow"
    assert any(step.get("tool") == "dummy_tool" for step in response.steps)
    assert any(result["name"] == "dummy_tool" for result in response.metadata["tool_results"])


@pytest.mark.asyncio
async def test_should_auto_replan_patterns(tmp_path: Path) -> None:
    """Test the auto-replan detection logic for various failure patterns."""
    from shared.models import PlanStep, StepResult

    settings = Settings()
    service = AgentService(
        settings=settings,
        litellm=cast(LiteLLMClient, MockLiteLLMClient()),
        memory=cast(MemoryStore, DummyMemory()),
    )

    # Create a sample plan step
    plan_step = PlanStep(
        id="test-step",
        label="Test step",
        executor="agent",
        action="tool",
        tool="test_tool",
        args={},
    )

    # Test: error status with auth failure pattern
    auth_patterns = [
        "401 Unauthorized",
        "403 Forbidden",
        "Authentication failed",
        "Invalid credentials",
        "Token expired",
        "Access denied",
    ]
    for pattern in auth_patterns:
        step_result = StepResult(
            step=plan_step,
            status="error",
            result={"error": f"Error: {pattern}"},
            messages=[],
        )
        should_replan, reason = service._should_auto_replan(step_result, plan_step)
        assert should_replan is True
        assert "authentication" in reason.lower() or "authorization" in reason.lower()

    # Test: error status with resource not found
    step_result = StepResult(
        step=plan_step,
        status="error",
        result={"error": "404 Not Found: Resource doesn't exist"},
        messages=[],
    )
    should_replan, reason = service._should_auto_replan(step_result, plan_step)
    assert should_replan is True
    assert "not found" in reason.lower()

    # Test: error status with timeout
    step_result = StepResult(
        step=plan_step,
        status="error",
        result={"error": "Request timed out after 30 seconds"},
        messages=[],
    )
    should_replan, reason = service._should_auto_replan(step_result, plan_step)
    assert should_replan is True
    assert "timed" in reason.lower()

    # Test: generic error falls back to error message
    step_result = StepResult(
        step=plan_step,
        status="error",
        result={"error": "Something unexpected happened"},
        messages=[],
    )
    should_replan, reason = service._should_auto_replan(step_result, plan_step)
    assert should_replan is True
    assert "error" in reason.lower()

    # Test: success case - no replan needed
    step_result = StepResult(
        step=plan_step,
        status="ok",
        result={"output": "Operation completed successfully"},
        messages=[],
    )
    should_replan, reason = service._should_auto_replan(step_result, plan_step)
    assert should_replan is False
    assert reason == ""

    # Test: successful output containing error-like text (false positive guard)
    step_result = StepResult(
        step=plan_step,
        status="ok",
        result={
            "output": (
                "### Found 20 Work Items\n"
                "- #12345 Fix timeout on build pipeline\n"
                "- #12346 Handle 401 Unauthorized in API gateway\n"
                "- #12347 Access denied error for storage account"
            )
        },
        messages=[],
    )
    should_replan, reason = service._should_auto_replan(step_result, plan_step)
    assert should_replan is False, "Should not replan on successful output with error-like keywords"
    assert reason == ""
