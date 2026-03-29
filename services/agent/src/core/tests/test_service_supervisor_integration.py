"""Tests for AgentService integration between retrieval and supervisor."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from shared.models import PlanStep, StepResult

from core.runtime.service import AgentService


class TestShouldAutoReplan:
    """Test AgentService._should_auto_replan() method."""

    @pytest.fixture
    def agent_service(self) -> AgentService:
        """Create an AgentService instance with mocked dependencies."""
        mock_settings = MagicMock()
        mock_litellm = MagicMock()
        mock_memory = MagicMock()

        return AgentService(
            settings=mock_settings,
            litellm=mock_litellm,
            memory=mock_memory,
        )

    def test_should_auto_replan_triggers_on_retrieval_sufficient_false(
        self,
        agent_service: AgentService,
    ) -> None:
        """Test that auto-replan triggers when rag_search returns retrieval_sufficient=false."""
        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "test query"},
        )

        rag_output = json.dumps(
            {
                "results": [{"id": "1", "score": 0.45}],
                "result_count": 1,
                "min_score": 0.45,
                "max_score": 0.45,
                "avg_score": 0.45,
                "retrieval_sufficient": False,
                "threshold": 0.65,
            }
        )

        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        should_replan, reason = agent_service._should_auto_replan(step_result, step)

        assert should_replan is True
        assert "retrieval" in reason.lower() or "insufficient" in reason.lower()
        assert "0.45" in reason  # Score mentioned

    def test_should_auto_replan_skips_when_retrieval_sufficient_true(
        self,
        agent_service: AgentService,
    ) -> None:
        """Test that auto-replan does NOT trigger when retrieval_sufficient=true."""
        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "test query"},
        )

        rag_output = json.dumps(
            {
                "results": [{"id": "1", "score": 0.85}],
                "result_count": 1,
                "min_score": 0.85,
                "max_score": 0.85,
                "avg_score": 0.85,
                "retrieval_sufficient": True,
                "threshold": 0.65,
            }
        )

        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        should_replan, reason = agent_service._should_auto_replan(step_result, step)

        assert should_replan is False

    def test_should_auto_replan_distinguishes_empty_from_low_scoring(
        self,
        agent_service: AgentService,
    ) -> None:
        """Test that empty results (result_count=0) produce different reason than low scores."""
        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "obscure topic"},
        )

        # Empty results
        rag_output = json.dumps(
            {
                "results": [],
                "result_count": 0,
                "min_score": 0.0,
                "max_score": 0.0,
                "avg_score": 0.0,
                "retrieval_sufficient": False,
                "threshold": 0.65,
            }
        )

        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        should_replan, reason = agent_service._should_auto_replan(step_result, step)

        assert should_replan is True
        assert "no results" in reason.lower()
        assert "knowledge base" in reason.lower()

    def test_should_auto_replan_handles_low_scores_with_reformulation_hint(
        self,
        agent_service: AgentService,
    ) -> None:
        """Test that low-scoring results suggest query reformulation."""
        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "vague query"},
        )

        rag_output = json.dumps(
            {
                "results": [
                    {"id": "1", "score": 0.50},
                    {"id": "2", "score": 0.55},
                ],
                "result_count": 2,
                "min_score": 0.50,
                "max_score": 0.55,
                "avg_score": 0.525,
                "retrieval_sufficient": False,
                "threshold": 0.65,
            }
        )

        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        should_replan, reason = agent_service._should_auto_replan(step_result, step)

        assert should_replan is True
        # Should contain score information
        assert "0.525" in reason or "0.52" in reason
        assert "below" in reason.lower() or "threshold" in reason.lower()

    def test_should_auto_replan_skips_non_rag_search_tools(
        self,
        agent_service: AgentService,
    ) -> None:
        """Test that non-rag_search tools don't trigger RAG-specific auto-replan."""
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

        should_replan, reason = agent_service._should_auto_replan(step_result, step)

        # Should not auto-replan on success for non-rag tools
        assert should_replan is False

    def test_should_auto_replan_handles_invalid_json(
        self,
        agent_service: AgentService,
    ) -> None:
        """Test that invalid JSON in rag_search output doesn't crash auto-replan."""
        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "test"},
        )

        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": "not valid json"},
            messages=[],
        )

        should_replan, reason = agent_service._should_auto_replan(step_result, step)

        # Should gracefully handle invalid JSON and not replan
        assert should_replan is False

    def test_should_auto_replan_malformed_missing_retrieval_sufficient_field(
        self,
        agent_service: AgentService,
    ) -> None:
        """Test that missing retrieval_sufficient field doesn't trigger auto-replan."""
        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "test"},
        )

        # JSON without retrieval_sufficient field
        rag_output = json.dumps(
            {
                "results": [{"id": "1", "score": 0.85}],
                "result_count": 1,
            }
        )

        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        should_replan, reason = agent_service._should_auto_replan(step_result, step)

        assert should_replan is False

    def test_should_auto_replan_malformed_missing_result_count(
        self,
        agent_service: AgentService,
    ) -> None:
        """Test that missing result_count field defaults to 0 gracefully."""
        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "test"},
        )

        # JSON without result_count field
        rag_output = json.dumps(
            {
                "results": [],
                "min_score": 0.0,
                "max_score": 0.0,
                "avg_score": 0.0,
                "retrieval_sufficient": False,
                "threshold": 0.65,
            }
        )

        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        should_replan, reason = agent_service._should_auto_replan(step_result, step)

        # Should trigger replan (result_count defaults to 0 = empty results)
        assert should_replan is True
        assert "no results" in reason.lower() or "knowledge base" in reason.lower()

    def test_should_auto_replan_malformed_negative_scores(
        self,
        agent_service: AgentService,
    ) -> None:
        """Test that negative scores (corrupted data) are handled gracefully."""
        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "test"},
        )

        # Negative scores - corrupted data
        rag_output = json.dumps(
            {
                "results": [{"id": "1", "score": -0.15}],
                "result_count": 1,
                "min_score": -0.15,
                "max_score": -0.15,
                "avg_score": -0.15,
                "retrieval_sufficient": False,
                "threshold": 0.65,
            }
        )

        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        should_replan, reason = agent_service._should_auto_replan(step_result, step)

        # Should trigger replan (negative scores indicate issue)
        assert should_replan is True
        # Should mention scores are below threshold
        assert "-0.15" in reason

    def test_should_auto_replan_malformed_scores_above_one(
        self,
        agent_service: AgentService,
    ) -> None:
        """Test that scores above 1.0 (malformed) are still evaluated correctly."""
        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "test"},
        )

        # Scores above 1.0 - malformed but should still be evaluated
        rag_output = json.dumps(
            {
                "results": [{"id": "1", "score": 1.5}],
                "result_count": 1,
                "min_score": 1.5,
                "max_score": 1.5,
                "avg_score": 1.5,
                "retrieval_sufficient": False,
                "threshold": 0.65,
            }
        )

        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        should_replan, reason = agent_service._should_auto_replan(step_result, step)

        # Should trigger replan (above threshold indicates issue, not sufficient)
        assert should_replan is True
        assert "1.5" in reason

    def test_should_auto_replan_handles_empty_output(
        self,
        agent_service: AgentService,
    ) -> None:
        """Test that empty output doesn't crash auto-replan."""
        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "test"},
        )

        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": ""},
            messages=[],
        )

        should_replan, reason = agent_service._should_auto_replan(step_result, step)

        assert should_replan is False


class TestCheckRagRetrievalSufficiency:
    """Test AgentService._check_rag_retrieval_sufficiency() method."""

    @pytest.fixture
    def agent_service(self) -> AgentService:
        """Create an AgentService instance with mocked dependencies."""
        mock_settings = MagicMock()
        mock_litellm = MagicMock()
        mock_memory = MagicMock()
        return AgentService(
            settings=mock_settings,
            litellm=mock_litellm,
            memory=mock_memory,
        )

    def test_check_sufficiency_empty_results(
        self,
        agent_service: AgentService,
    ) -> None:
        """Test that empty results (result_count=0) trigger replan with specific reason."""
        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "niche topic"},
        )

        rag_output = json.dumps(
            {
                "results": [],
                "result_count": 0,
                "min_score": 0.0,
                "max_score": 0.0,
                "avg_score": 0.0,
                "retrieval_sufficient": False,
                "threshold": 0.65,
            }
        )

        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        should_replan, reason = agent_service._check_rag_retrieval_sufficiency(step_result, step)

        assert should_replan is True
        assert "no results" in reason.lower()
        assert "broadening" in reason.lower() or "relevant" in reason.lower()

    def test_check_sufficiency_low_scores_below_half_threshold(
        self,
        agent_service: AgentService,
    ) -> None:
        """Test very low scores (avg < 0.5 * threshold) indicate corpus lacks info."""
        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "technical docs"},
        )

        # Scores well below threshold (0.30 avg vs 0.65 threshold)
        rag_output = json.dumps(
            {
                "results": [{"id": "1", "score": 0.30}],
                "result_count": 1,
                "min_score": 0.30,
                "max_score": 0.30,
                "avg_score": 0.30,
                "retrieval_sufficient": False,
                "threshold": 0.65,
            }
        )

        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        should_replan, reason = agent_service._check_rag_retrieval_sufficiency(step_result, step)

        assert should_replan is True
        assert "0.30" in reason

    def test_check_sufficiency_moderate_scores_reformulation_hint(
        self,
        agent_service: AgentService,
    ) -> None:
        """Test moderate scores (0.5*threshold < avg < threshold) suggest reformulation."""
        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "somewhat related"},
        )

        # Moderate scores - might improve with reformulation
        rag_output = json.dumps(
            {
                "results": [
                    {"id": "1", "score": 0.50},
                    {"id": "2", "score": 0.55},
                ],
                "result_count": 2,
                "min_score": 0.50,
                "max_score": 0.55,
                "avg_score": 0.525,
                "retrieval_sufficient": False,
                "threshold": 0.65,
            }
        )

        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        should_replan, reason = agent_service._check_rag_retrieval_sufficiency(step_result, step)

        assert should_replan is True
        # Should suggest refining or reformulating
        assert "0.525" in reason or "0.52" in reason

    def test_check_sufficiency_sufficient_returns_false(
        self,
        agent_service: AgentService,
    ) -> None:
        """Test that sufficient retrieval (retrieval_sufficient=true) returns False."""
        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "common topic"},
        )

        rag_output = json.dumps(
            {
                "results": [{"id": "1", "score": 0.85}],
                "result_count": 1,
                "min_score": 0.85,
                "max_score": 0.85,
                "avg_score": 0.85,
                "retrieval_sufficient": True,
                "threshold": 0.65,
            }
        )

        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        should_replan, reason = agent_service._check_rag_retrieval_sufficiency(step_result, step)

        assert should_replan is False
        assert reason == ""


class TestRetrievalBoundaryConditions:
    """Test exact boundary conditions for retrieval sufficiency tiers.

    Three-tier feedback system:
    - avg_score < 0.325 (half threshold): "corpus lacks info" tier
    - 0.325 <= avg_score < 0.65: "reformulation might help" tier
    - avg_score >= 0.65: sufficient (no replan)
    """

    @pytest.fixture
    def agent_service(self) -> AgentService:
        """Create an AgentService instance with mocked dependencies."""
        mock_settings = MagicMock()
        mock_litellm = MagicMock()
        mock_memory = MagicMock()
        return AgentService(
            settings=mock_settings,
            litellm=mock_litellm,
            memory=mock_memory,
        )

    def test_boundary_exactly_at_threshold_is_sufficient(
        self,
        agent_service: AgentService,
    ) -> None:
        """Test that exactly 0.6500 equals threshold (>= comparison) for sufficiency."""
        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "test query"},
        )

        rag_output = json.dumps(
            {
                "results": [{"id": "1", "score": 0.6500}],
                "result_count": 1,
                "min_score": 0.6500,
                "max_score": 0.6500,
                "avg_score": 0.6500,
                "retrieval_sufficient": True,  # 0.65 >= 0.65
                "threshold": 0.65,
            }
        )

        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        should_replan, reason = agent_service._should_auto_replan(step_result, step)

        assert should_replan is False  # Sufficient at boundary
        assert reason == ""

    def test_boundary_just_below_threshold_triggers_replan(
        self,
        agent_service: AgentService,
    ) -> None:
        """Test that 0.6499 (just below threshold) triggers insufficient/replan."""
        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "test query"},
        )

        rag_output = json.dumps(
            {
                "results": [{"id": "1", "score": 0.6499}],
                "result_count": 1,
                "min_score": 0.6499,
                "max_score": 0.6499,
                "avg_score": 0.6499,
                "retrieval_sufficient": False,  # 0.6499 < 0.65
                "threshold": 0.65,
            }
        )

        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        should_replan, reason = agent_service._should_auto_replan(step_result, step)

        assert should_replan is True
        assert "below" in reason.lower() or "insufficient" in reason.lower()
        assert "0.6499" in reason or "0.6500" in reason or "0.65" in reason

    def test_boundary_exactly_half_threshold_corpus_lacks_info(
        self,
        agent_service: AgentService,
    ) -> None:
        """Test exactly 0.325 (half of 0.65) - distinguishes 'corpus lacks info' tier.

        This tests the boundary where avg_score equals exactly half the threshold.
        Scores at or below this level indicate the corpus likely lacks the requested info.
        """
        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "test query"},
        )

        rag_output = json.dumps(
            {
                "results": [{"id": "1", "score": 0.3250}],
                "result_count": 1,
                "min_score": 0.3250,
                "max_score": 0.3250,
                "avg_score": 0.3250,
                "retrieval_sufficient": False,  # 0.325 < 0.65
                "threshold": 0.65,
            }
        )

        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        should_replan, reason = agent_service._should_auto_replan(step_result, step)

        assert should_replan is True
        # Verify precision to 4 decimal places
        assert "0.3250" in reason or "0.33" in reason

    def test_boundary_between_half_and_full_reformulation_tier(
        self,
        agent_service: AgentService,
    ) -> None:
        """Test 0.3251 (just above half threshold) - 'reformulation might help' tier.

        This tests the boundary between 'corpus lacks info' (< 0.325) and
        'reformulation might help' (>= 0.325 but < 0.65) tiers.
        """
        step = PlanStep(
            id="1",
            label="RAG Search",
            executor="agent",
            action="tool",
            tool="rag_search",
            args={"query": "test query"},
        )

        rag_output = json.dumps(
            {
                "results": [{"id": "1", "score": 0.3251}],
                "result_count": 1,
                "min_score": 0.3251,
                "max_score": 0.3251,
                "avg_score": 0.3251,
                "retrieval_sufficient": False,  # 0.3251 < 0.65
                "threshold": 0.65,
            }
        )

        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        should_replan, reason = agent_service._should_auto_replan(step_result, step)

        assert should_replan is True
        # Verify 4 decimal precision
        assert "0.3251" in reason or "0.33" in reason
