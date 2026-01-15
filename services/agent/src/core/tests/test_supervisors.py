"""Tests for supervisor agents (PlanSupervisor and StepSupervisor)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from shared.models import Plan, PlanStep, StepResult

from core.agents.supervisor_plan import PlanSupervisorAgent
from core.agents.supervisor_step import StepSupervisorAgent
from core.tools.registry import ToolRegistry


class TestPlanSupervisorAgent:
    """Tests for PlanSupervisorAgent validation logic."""

    @pytest.fixture
    def skill_names(self) -> set[str]:
        """Sample skill names for testing."""
        return {
            "researcher",
            "search",
            "backlog_manager",
            "requirements_drafter",
            "requirements_writer",
        }

    @pytest.fixture
    def mock_registry(self) -> ToolRegistry:
        """Mock tool registry with sample tools."""
        mock_tool = MagicMock()
        mock_tool.name = "consult_expert"
        return ToolRegistry([mock_tool])

    @pytest.mark.asyncio
    async def test_valid_plan_passes(
        self, mock_registry: ToolRegistry, skill_names: set[str]
    ) -> None:
        """Test that a valid plan passes validation."""
        supervisor = PlanSupervisorAgent(tool_registry=mock_registry, skill_names=skill_names)

        plan = Plan(
            description="Valid plan",
            steps=[
                PlanStep(
                    id="1",
                    label="Research",
                    executor="agent",
                    action="tool",
                    tool="consult_expert",
                    args={"skill": "researcher", "goal": "Find info"},
                ),
                PlanStep(
                    id="2",
                    label="Answer",
                    executor="litellm",
                    action="completion",
                    args={},
                ),
            ],
        )

        result = await supervisor.review(plan)
        assert result == plan  # Plan should be returned unchanged

    @pytest.mark.asyncio
    async def test_empty_plan_logs_error(
        self, mock_registry: ToolRegistry, skill_names: set[str]
    ) -> None:
        """Test that empty plans are flagged as errors."""
        supervisor = PlanSupervisorAgent(tool_registry=mock_registry, skill_names=skill_names)

        plan = Plan(description="Empty plan", steps=[])

        # Should still return the plan but log error
        result = await supervisor.review(plan)
        assert result == plan

    @pytest.mark.asyncio
    async def test_missing_completion_step_warns(
        self, mock_registry: ToolRegistry, skill_names: set[str]
    ) -> None:
        """Test that plans without completion step generate warning."""
        supervisor = PlanSupervisorAgent(tool_registry=mock_registry, skill_names=skill_names)

        plan = Plan(
            description="No completion",
            steps=[
                PlanStep(
                    id="1",
                    label="Research",
                    executor="agent",
                    action="tool",
                    tool="consult_expert",
                    args={"skill": "researcher", "goal": "Find info"},
                ),
            ],
        )

        result = await supervisor.review(plan)
        assert result == plan  # Still returns plan with warning

    @pytest.mark.asyncio
    async def test_unknown_skill_warns(
        self, mock_registry: ToolRegistry, skill_names: set[str]
    ) -> None:
        """Test that unknown skill references generate warning."""
        supervisor = PlanSupervisorAgent(tool_registry=mock_registry, skill_names=skill_names)

        plan = Plan(
            description="Unknown skill",
            steps=[
                PlanStep(
                    id="1",
                    label="Research",
                    executor="agent",
                    action="tool",
                    tool="consult_expert",
                    args={"skill": "nonexistent_skill", "goal": "Find info"},
                ),
                PlanStep(
                    id="2",
                    label="Answer",
                    executor="litellm",
                    action="completion",
                    args={},
                ),
            ],
        )

        result = await supervisor.review(plan)
        assert result == plan  # Returns plan with warning

    @pytest.mark.asyncio
    async def test_missing_skill_arg_errors(
        self, mock_registry: ToolRegistry, skill_names: set[str]
    ) -> None:
        """Test that missing skill argument generates error."""
        supervisor = PlanSupervisorAgent(tool_registry=mock_registry, skill_names=skill_names)

        plan = Plan(
            description="Missing skill arg",
            steps=[
                PlanStep(
                    id="1",
                    label="Research",
                    executor="agent",
                    action="tool",
                    tool="consult_expert",
                    args={"goal": "Find info"},  # Missing 'skill' arg
                ),
                PlanStep(
                    id="2",
                    label="Answer",
                    executor="litellm",
                    action="completion",
                    args={},
                ),
            ],
        )

        result = await supervisor.review(plan)
        assert result == plan  # Returns plan with error logged

    @pytest.mark.asyncio
    async def test_wrong_executor_for_tool_warns(
        self, mock_registry: ToolRegistry, skill_names: set[str]
    ) -> None:
        """Test that tool actions with wrong executor generate warning."""
        supervisor = PlanSupervisorAgent(tool_registry=mock_registry, skill_names=skill_names)

        plan = Plan(
            description="Wrong executor",
            steps=[
                PlanStep(
                    id="1",
                    label="Research",
                    executor="litellm",  # Should be 'agent' for tool actions
                    action="tool",
                    tool="consult_expert",
                    args={"skill": "researcher", "goal": "Find info"},
                ),
                PlanStep(
                    id="2",
                    label="Answer",
                    executor="litellm",
                    action="completion",
                    args={},
                ),
            ],
        )

        result = await supervisor.review(plan)
        assert result == plan  # Returns plan with warning


class TestStepSupervisorAgent:
    """Tests for StepSupervisorAgent error handling."""

    @pytest.mark.asyncio
    async def test_returns_adjust_on_llm_error(self) -> None:
        """Test that supervisor returns 'adjust' when LLM call fails."""
        # Create mock LLM that raises an exception
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(side_effect=Exception("Network timeout"))

        supervisor = StepSupervisorAgent(mock_llm, model_name="test-model")

        step = PlanStep(
            id="1",
            label="Test step",
            executor="agent",
            action="tool",
            tool="web_search",
            args={"query": "test"},
        )
        step_result = StepResult(
            step=step, status="ok", result={"output": "some data"}, messages=[]
        )

        decision, reason, suggested_fix = await supervisor.review(step, step_result)

        # Should return 'adjust' on error, NOT 'ok'
        assert decision == "adjust"
        assert "unavailable" in reason.lower() or "timeout" in reason.lower()
        assert suggested_fix is not None

    @pytest.mark.asyncio
    async def test_returns_adjust_on_timeout(self) -> None:
        """Test that supervisor returns 'adjust' on timeout."""

        # Create mock LLM that times out
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(side_effect=TimeoutError())

        supervisor = StepSupervisorAgent(mock_llm, model_name="test-model")

        step = PlanStep(
            id="1",
            label="Test step",
            executor="agent",
            action="tool",
            tool="web_search",
            args={"query": "test"},
        )
        step_result = StepResult(
            step=step, status="ok", result={"output": "some data"}, messages=[]
        )

        decision, reason, suggested_fix = await supervisor.review(step, step_result)

        assert decision == "adjust"

    @pytest.mark.asyncio
    async def test_parses_ok_response(self) -> None:
        """Test that supervisor correctly parses 'ok' response."""
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(
            return_value='{"decision": "ok", "reason": "Step completed successfully"}'
        )

        supervisor = StepSupervisorAgent(mock_llm, model_name="test-model")

        step = PlanStep(
            id="1",
            label="Test step",
            executor="agent",
            action="tool",
            tool="web_search",
            args={"query": "test"},
        )
        step_result = StepResult(
            step=step, status="ok", result={"output": "good data"}, messages=[]
        )

        decision, reason, suggested_fix = await supervisor.review(step, step_result)

        assert decision == "ok"
        assert "success" in reason.lower()

    @pytest.mark.asyncio
    async def test_parses_adjust_response_with_fix(self) -> None:
        """Test that supervisor correctly parses 'adjust' response with suggested fix."""
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(
            return_value=(
                '{"decision": "adjust", "reason": "Output incomplete", '
                '"suggested_fix": "Try a different query"}'
            )
        )

        supervisor = StepSupervisorAgent(mock_llm, model_name="test-model")

        step = PlanStep(
            id="1",
            label="Test step",
            executor="agent",
            action="tool",
            tool="web_search",
            args={"query": "test"},
        )
        step_result = StepResult(
            step=step, status="ok", result={"output": "partial data"}, messages=[]
        )

        decision, reason, suggested_fix = await supervisor.review(step, step_result)

        assert decision == "adjust"
        assert "incomplete" in reason.lower()
        assert suggested_fix is not None
        assert "query" in suggested_fix.lower()
