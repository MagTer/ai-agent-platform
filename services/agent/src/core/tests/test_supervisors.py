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
        mock_tool.name = "web_search"
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

    @pytest.mark.asyncio
    async def test_rag_search_empty_results_fast_path_replan(self) -> None:
        """Test fast-path REPLAN for rag_search with empty results (result_count=0)."""
        mock_llm = MagicMock()
        # LLM should NOT be called for fast-path detection
        mock_llm.generate = AsyncMock(return_value='{"outcome": "success"}')

        supervisor = StepSupervisorAgent(mock_llm, model_name="test-model")

        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "obscure topic"},
        )
        rag_output = (
            '{"results": [], "result_count": 0, "min_score": 0.0, "max_score": 0.0, '
            '"avg_score": 0.0, "retrieval_sufficient": false}'
        )
        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        outcome, reason, suggested_fix = await supervisor.review(step, step_result)

        # Should return REPLAN without calling LLM
        assert outcome == StepOutcome.REPLAN
        assert "no documents" in reason.lower() or "empty" in reason.lower()
        assert suggested_fix is not None
        assert "knowledge base" in suggested_fix.lower()
        mock_llm.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_rag_search_low_scores_fast_path_replan(self) -> None:
        """Test fast-path REPLAN for rag_search with very low scores (corpus lacks info)."""
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value='{"outcome": "success"}')

        supervisor = StepSupervisorAgent(mock_llm, model_name="test-model")

        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "technical documentation"},
        )
        # Low scores well below threshold - corpus likely lacks relevant info
        rag_output = (
            '{"results": [{"id": "1", "score": 0.15}], "result_count": 1, '
            '"min_score": 0.15, "max_score": 0.15, "avg_score": 0.15, '
            '"retrieval_sufficient": false}'
        )
        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        outcome, reason, suggested_fix = await supervisor.review(step, step_result)

        assert outcome == StepOutcome.REPLAN
        # Should indicate corpus lacks relevant info (avg_score < 0.5 * threshold)
        assert "well below" in reason.lower() or "lacks" in reason.lower()
        assert suggested_fix is not None
        mock_llm.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_rag_search_moderate_scores_suggests_reformulation(self) -> None:
        """Test REPLAN for rag_search with moderate scores (query reformulation may help)."""
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value='{"outcome": "success"}')

        supervisor = StepSupervisorAgent(mock_llm, model_name="test-model")

        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "vague query"},
        )
        # Moderate scores below threshold but not terrible - query reformulation may help
        rag_output = (
            '{"results": [{"id": "1", "score": 0.45}, {"id": "2", "score": 0.55}], '
            '"result_count": 2, "min_score": 0.45, "max_score": 0.55, "avg_score": 0.50, '
            '"retrieval_sufficient": false}'
        )
        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        outcome, reason, suggested_fix = await supervisor.review(step, step_result)

        assert outcome == StepOutcome.REPLAN
        # Should suggest query reformulation
        assert "reformulation" in reason.lower() or "below threshold" in reason.lower()
        assert suggested_fix is not None
        assert "reformulating" in suggested_fix.lower() or "technical" in suggested_fix.lower()
        mock_llm.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_rag_search_sufficient_retrieval_skips_fast_path(self) -> None:
        """Test that rag_search with sufficient retrieval uses normal LLM evaluation."""
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(
            return_value='{"outcome": "success", "reason": "Retrieval successful"}'
        )

        supervisor = StepSupervisorAgent(mock_llm, model_name="test-model")

        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "relevant topic"},
        )
        # Sufficient retrieval - should NOT trigger fast-path
        rag_output = (
            '{"results": [{"id": "1", "score": 0.85}], "result_count": 1, '
            '"min_score": 0.85, "max_score": 0.85, "avg_score": 0.85, '
            '"retrieval_sufficient": true}'
        )
        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        outcome, reason, suggested_fix = await supervisor.review(step, step_result)

        # Should call LLM for normal evaluation since retrieval was sufficient
        assert outcome == StepOutcome.SUCCESS
        mock_llm.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_rag_search_tool_uses_normal_evaluation(self) -> None:
        """Test that non-rag_search tools use normal LLM evaluation."""
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(
            return_value='{"outcome": "success", "reason": "Step completed"}'
        )

        supervisor = StepSupervisorAgent(mock_llm, model_name="test-model")

        step = PlanStep(
            id="1",
            label="Web Search",
            executor="agent",
            action="tool",
            tool="web_search",
            args={"query": "test"},
        )
        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": "some results"},
            messages=[],
        )

        outcome, reason, suggested_fix = await supervisor.review(step, step_result)

        # Should use normal LLM evaluation
        assert outcome == StepOutcome.SUCCESS
        mock_llm.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_rag_search_malformed_json_uses_normal_evaluation(self) -> None:
        """Test that invalid JSON output falls back to LLM evaluation."""
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(
            return_value='{"outcome": "success", "reason": "Step completed"}'
        )

        supervisor = StepSupervisorAgent(mock_llm, model_name="test-model")

        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "test"},
        )
        # Invalid JSON - should fall back to LLM
        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": "not valid json"},
            messages=[],
        )

        outcome, reason, suggested_fix = await supervisor.review(step, step_result)

        # Should fall back to LLM for invalid JSON
        assert outcome == StepOutcome.SUCCESS
        mock_llm.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_rag_search_malformed_missing_retrieval_sufficient_uses_normal_evaluation(
        self,
    ) -> None:
        """Test that missing retrieval_sufficient field uses normal LLM evaluation."""
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(
            return_value='{"outcome": "success", "reason": "Step completed"}'
        )

        supervisor = StepSupervisorAgent(mock_llm, model_name="test-model")

        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "test"},
        )
        # Valid JSON but missing retrieval_sufficient field
        rag_output = (
            '{"results": [{"id": "1", "score": 0.85}], "result_count": 1, '
            '"min_score": 0.85, "max_score": 0.85, "avg_score": 0.85}'
        )
        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        outcome, reason, suggested_fix = await supervisor.review(step, step_result)

        # Should fall back to LLM since retrieval_sufficient is missing
        assert outcome == StepOutcome.SUCCESS
        mock_llm.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_rag_search_malformed_missing_result_count_defaults_handled(self) -> None:
        """Test that missing result_count field defaults to 0 gracefully."""
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(
            return_value='{"outcome": "success", "reason": "Step completed"}'
        )

        supervisor = StepSupervisorAgent(mock_llm, model_name="test-model")

        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "test"},
        )
        # Valid JSON missing result_count field
        rag_output = (
            '{"results": [], "min_score": 0.0, "max_score": 0.0, "avg_score": 0.0, '
            '"retrieval_sufficient": false, "threshold": 0.65}'
        )
        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        outcome, reason, suggested_fix = await supervisor.review(step, step_result)

        # Should return REPLAN with empty results (result_count defaults to 0)
        assert outcome == StepOutcome.REPLAN
        assert "no documents" in reason.lower() or "knowledge base" in reason.lower()
        mock_llm.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_rag_search_malformed_negative_scores_handled_gracefully(self) -> None:
        """Test that negative scores (corrupted data) are handled gracefully."""
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(
            return_value='{"outcome": "success", "reason": "Step completed"}'
        )

        supervisor = StepSupervisorAgent(mock_llm, model_name="test-model")

        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "test"},
        )
        # Negative scores - corrupted data
        rag_output = (
            '{"results": [{"id": "1", "score": -0.15}], "result_count": 1, '
            '"min_score": -0.15, "max_score": -0.15, "avg_score": -0.15, '
            '"retrieval_sufficient": false, "threshold": 0.65}'
        )
        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        outcome, reason, suggested_fix = await supervisor.review(step, step_result)

        # Should return REPLAN (negative scores indicate corrupted data)
        assert outcome == StepOutcome.REPLAN
        # Should indicate low scores (negative is below threshold)
        assert "below" in reason.lower() or "threshold" in reason.lower()
        mock_llm.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_rag_search_malformed_scores_above_one_handled_correctly(self) -> None:
        """Test that scores above 1.0 (malformed) are still evaluated correctly."""
        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(
            return_value='{"outcome": "success", "reason": "Step completed"}'
        )

        supervisor = StepSupervisorAgent(mock_llm, model_name="test-model")

        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "test"},
        )
        # Scores above 1.0 - malformed but should still be evaluated
        rag_output = (
            '{"results": [{"id": "1", "score": 1.5}], "result_count": 1, '
            '"min_score": 1.5, "max_score": 1.5, "avg_score": 1.5, '
            '"retrieval_sufficient": false, "threshold": 0.65}'
        )
        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        outcome, reason, suggested_fix = await supervisor.review(step, step_result)

        # Should still trigger REPLAN (above threshold suggests issue, but not sufficient)
        assert outcome == StepOutcome.REPLAN
        assert "below" in reason.lower() or "threshold" in reason.lower()
        mock_llm.generate.assert_not_called()
