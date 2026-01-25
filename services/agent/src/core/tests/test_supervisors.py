"""Tests for supervisor agents (PlanSupervisor and StepSupervisor)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from shared.models import Plan, PlanStep, StepOutcome, StepResult

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

        # Use new skills-native format
        plan = Plan(
            description="Valid plan",
            steps=[
                PlanStep(
                    id="1",
                    label="Research",
                    executor="skill",
                    action="skill",
                    tool="researcher",
                    args={"goal": "Find info"},
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
        # Plan should be returned unchanged (no migration needed)
        assert result.steps[0].executor == "skill"
        assert result.steps[0].tool == "researcher"

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
                    executor="skill",
                    action="skill",
                    tool="researcher",
                    args={"goal": "Find info"},
                ),
            ],
        )

        result = await supervisor.review(plan)
        # Still returns plan with warning (no migration needed)
        assert result.steps[0].tool == "researcher"

    @pytest.mark.asyncio
    async def test_consult_expert_migration(
        self, mock_registry: ToolRegistry, skill_names: set[str]
    ) -> None:
        """Test that consult_expert steps are migrated to skills-native format."""
        supervisor = PlanSupervisorAgent(tool_registry=mock_registry, skill_names=skill_names)

        # Old format with consult_expert
        plan = Plan(
            description="Old format plan",
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
        # Should be migrated to skills-native format
        assert result.steps[0].executor == "skill"
        assert result.steps[0].action == "skill"
        assert result.steps[0].tool == "researcher"
        assert result.steps[0].args.get("goal") == "Find info"
        assert "skill" not in result.steps[0].args

    @pytest.mark.asyncio
    async def test_missing_skill_arg_no_migration(
        self, mock_registry: ToolRegistry, skill_names: set[str]
    ) -> None:
        """Test that consult_expert without skill arg is not migrated."""
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
        # Without skill arg, consult_expert is not migrated
        assert result.steps[0].tool == "consult_expert"

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
                    tool="some_tool",  # Non-consult_expert tool
                    args={"query": "test"},
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
        # Returns plan with warning (no migration for non-consult_expert tools)
        assert result.steps[0].executor == "litellm"


class TestStepSupervisorAgent:
    """Tests for StepSupervisorAgent with StepOutcome returns."""

    @pytest.mark.asyncio
    async def test_returns_retry_on_llm_error(self) -> None:
        """Test that supervisor returns RETRY when LLM call fails (first attempt)."""
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

        outcome, reason, suggested_fix = await supervisor.review(step, step_result)

        # First failure should return RETRY
        assert outcome == StepOutcome.RETRY
        assert "unavailable" in reason.lower() or "retry" in reason.lower()
        assert suggested_fix is not None

    @pytest.mark.asyncio
    async def test_returns_replan_after_retry(self) -> None:
        """Test that supervisor returns REPLAN when LLM fails after retry."""
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

        # With retry_count=1, should escalate to REPLAN
        outcome, reason, suggested_fix = await supervisor.review(step, step_result, retry_count=1)

        assert outcome == StepOutcome.REPLAN

    @pytest.mark.asyncio
    async def test_parses_success_response(self) -> None:
        """Test that supervisor correctly parses 'ok' response as SUCCESS."""
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

        outcome, reason, suggested_fix = await supervisor.review(step, step_result)

        assert outcome == StepOutcome.SUCCESS
        assert "success" in reason.lower()

    @pytest.mark.asyncio
    async def test_parses_replan_response_with_fix(self) -> None:
        """Test that supervisor correctly parses 'replan' response with suggested fix."""
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(
            return_value=(
                '{"outcome": "replan", "reason": "Output incomplete", '
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

        outcome, reason, suggested_fix = await supervisor.review(step, step_result)

        assert outcome == StepOutcome.REPLAN
        assert "incomplete" in reason.lower()
        assert suggested_fix is not None
        assert "query" in suggested_fix.lower()
