from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Iterable
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from shared.models import AgentMessage, AgentRequest

from core.db import Context, Conversation
from core.runtime.config import Settings
from core.runtime.litellm_client import LiteLLMClient
from core.runtime.memory import MemoryRecord, MemoryStore
from core.runtime.service import AgentService
from core.skills.registry import Skill
from core.tools.base import Tool

# Reusable test context ID (valid UUID)
_CTX_ID = "00000000-0000-0000-0000-000000000001"


def _make_mock_skill(name: str = "mock_skill") -> Skill:
    """Create a minimal Skill object for testing."""
    return Skill(
        name=name,
        path=Path("/mock/skills") / f"{name}.md",
        description="Mock skill for testing",
        tools=[],
        model="agentchat",
        max_turns=3,
        body_template="Answer the user's question: $query",
    )


def _make_mock_skill_registry(skill_name: str = "mock_skill") -> MagicMock:
    """Create a mock SkillRegistry that returns a mock skill."""
    registry = MagicMock()
    skill = _make_mock_skill(skill_name)
    registry.get.return_value = skill
    registry.get_skill_names.return_value = [skill_name]
    return registry


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
                        "id": "skill-step",
                        "label": "Run mock skill",
                        "executor": "skill",
                        "action": "skill",
                        "tool": "mock_skill",
                        "args": {"query": "default"},
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
    skill_registry = _make_mock_skill_registry()
    service = AgentService(
        settings=settings,
        litellm=cast(LiteLLMClient, MockLiteLLMClient()),
        memory=cast(MemoryStore, DummyMemory()),
        skill_registry=skill_registry,
    )

    # Mock Session
    session = AsyncMock()
    mock_ctx = MagicMock(id=_CTX_ID, default_cwd="/tmp")  # noqa: S108

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
        id=_CTX_ID, default_cwd="/tmp"  # noqa: S108
    )
    mock_result.scalars.return_value.all.return_value = []
    session.execute.return_value = mock_result

    request = AgentRequest(prompt="Hello", metadata={"context_id": _CTX_ID})
    response = await service.handle_request(request, session=session)

    assert response.conversation_id
    assert any(step["type"] == "plan_step" for step in response.steps)
    assert response.metadata["plan"]["steps"][0]["executor"] == "skill"

    follow_up = AgentRequest(
        prompt="How are you?",
        conversation_id=response.conversation_id,
        messages=response.messages,
        metadata={"context_id": _CTX_ID},
    )
    follow_response = await service.handle_request(follow_up, session=session)

    assert follow_response.conversation_id == response.conversation_id
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
                "id": "skill-1",
                "label": "Use dummy skill",
                "executor": "skill",
                "action": "skill",
                "tool": "dummy_skill",
                "args": {"query": "Hello world"},
            },
        ],
    }
    settings = Settings()
    skill_registry = _make_mock_skill_registry("dummy_skill")
    service = AgentService(
        settings=settings,
        litellm=cast(LiteLLMClient, MockLiteLLMClient(plan_output=json.dumps(plan_definition))),
        memory=cast(MemoryStore, DummyMemory()),
        skill_registry=skill_registry,
    )

    # Mock Session
    session = AsyncMock()
    mock_result = MagicMock()
    # Context
    mock_result.scalar_one_or_none.return_value = MagicMock(
        id=_CTX_ID, default_cwd="/tmp"  # noqa: S108
    )
    # History
    mock_result.scalars.return_value.all.return_value = []
    session.execute.return_value = mock_result

    mock_ctx = MagicMock(id=_CTX_ID, default_cwd="/tmp")  # noqa: S108

    def get_side_effect(model: Any, id: Any) -> Any:
        if model == Conversation:
            return None
        if model == Context:
            return mock_ctx
        return None

    session.get.side_effect = get_side_effect

    request = AgentRequest(prompt="Hello world", metadata={"context_id": _CTX_ID})
    response = await service.handle_request(request, session=session)

    assert response.metadata["plan"]["description"] == "Test plan flow"
    assert any(step.get("executor") == "skill" for step in response.steps)


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


@pytest.mark.asyncio
async def test_should_auto_replan_rag_search_insufficient_retrieval(tmp_path: Path) -> None:
    """Test auto-replan detection for rag_search with insufficient retrieval."""
    from shared.models import PlanStep, StepResult

    settings = Settings()
    service = AgentService(
        settings=settings,
        litellm=cast(LiteLLMClient, MockLiteLLMClient()),
        memory=cast(MemoryStore, DummyMemory()),
    )

    # Create a rag_search plan step
    plan_step = PlanStep(
        id="rag-step",
        label="Search knowledge base",
        executor="skill",
        action="skill",
        tool="rag_search",
        args={"query": "some obscure topic"},
    )

    # Test: retrieval_sufficient=false triggers auto-replan with low scores
    rag_output = {
        "results": [{"id": "1", "score": 0.45, "content": "low relevance doc"}],
        "result_count": 1,
        "min_score": 0.45,
        "max_score": 0.45,
        "avg_score": 0.45,
        "threshold": 0.65,
        "retrieval_sufficient": False,
    }
    step_result = StepResult(
        step=plan_step,
        status="ok",  # Even with ok status, retrieval_sufficient=false should trigger replan
        result={"output": json.dumps(rag_output)},
        messages=[],
    )
    should_replan, reason = service._should_auto_replan(step_result, plan_step)
    assert should_replan is True
    assert "insufficient" in reason.lower() or "below threshold" in reason.lower()
    assert "0.45" in reason or "0.4500" in reason  # avg_score
    assert "0.65" in reason or "0.6500" in reason  # threshold

    # Test: empty results (result_count=0) triggers auto-replan with different message
    rag_output_empty = {
        "results": [],
        "result_count": 0,
        "min_score": 0.0,
        "max_score": 0.0,
        "avg_score": 0.0,
        "threshold": 0.65,
        "retrieval_sufficient": False,
    }
    step_result = StepResult(
        step=plan_step,
        status="ok",
        result={"output": json.dumps(rag_output_empty)},
        messages=[],
    )
    should_replan, reason = service._should_auto_replan(step_result, plan_step)
    assert should_replan is True
    assert "no results" in reason.lower() or "returned no results" in reason.lower()

    # Test: retrieval_sufficient=true does NOT trigger replan
    rag_output_sufficient = {
        "results": [{"id": "1", "score": 0.75, "content": "high relevance doc"}],
        "result_count": 1,
        "min_score": 0.75,
        "max_score": 0.75,
        "avg_score": 0.75,
        "threshold": 0.65,
        "retrieval_sufficient": True,
    }
    step_result = StepResult(
        step=plan_step,
        status="ok",
        result={"output": json.dumps(rag_output_sufficient)},
        messages=[],
    )
    should_replan, reason = service._should_auto_replan(step_result, plan_step)
    assert should_replan is False
    assert reason == ""

    # Test: non-rag_search tool still follows error status rule
    other_step = PlanStep(
        id="other-step",
        label="Other tool",
        executor="skill",
        action="skill",
        tool="other_tool",
        args={},
    )
    # Successful status should not trigger replan even if output mentions low scores
    step_result = StepResult(
        step=other_step,
        status="ok",
        result={"output": "Some output with retrieval_sufficient: false"},
        messages=[],
    )
    should_replan, reason = service._should_auto_replan(step_result, other_step)
    assert should_replan is False, "Non-rag_search tool should not auto-replan on ok status"
    assert reason == ""

    # Test: rag_search with invalid JSON output - no replan
    step_result = StepResult(
        step=plan_step,
        status="ok",
        result={"output": "invalid json"},
        messages=[],
    )
    should_replan, reason = service._should_auto_replan(step_result, plan_step)
    assert should_replan is False
    assert reason == ""

    # Test: rag_search with no output - no replan
    step_result = StepResult(
        step=plan_step,
        status="ok",
        result={},
        messages=[],
    )
    should_replan, reason = service._should_auto_replan(step_result, plan_step)
    assert should_replan is False
    assert reason == ""
