"""Tests for AgentService integration between retrieval and supervisor."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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

        rag_output = json.dumps({
            "results": [{"id": "1", "score": 0.45}],
            "result_count": 1,
            "min_score": 0.45,
            "max_score": 0.45,
            "avg_score": 0.45,
            "retrieval_sufficient": False,
            "threshold": 0.65,
        })

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

        rag_output = json.dumps({
            "results": [{"id": "1", "score": 0.85}],
            "result_count": 1,
            "min_score": 0.85,
            "max_score": 0.85,
            "avg_score": 0.85,
            "retrieval_sufficient": True,
            "threshold": 0.65,
        })

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
        rag_output = json.dumps({
            "results": [],
            "result_count": 0,
            "min_score": 0.0,
            "max_score": 0.0,
            "avg_score": 0.0,
            "retrieval_sufficient": False,
            "threshold": 0.65,
        })

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

        rag_output = json.dumps({
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
        })

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

    def test_should_auto_replan_handles_missing_retrieval_sufficient_field(
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
        rag_output = json.dumps({
            "results": [{"id": "1", "score": 0.85}],
            "result_count": 1,
        })

        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        should_replan, reason = agent_service._should_auto_replan(step_result, step)

        assert should_replan is False

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

        rag_output = json.dumps({
            "results": [],
            "result_count": 0,
            "min_score": 0.0,
            "max_score": 0.0,
            "avg_score": 0.0,
            "retrieval_sufficient": False,
            "threshold": 0.65,
        })

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
        rag_output = json.dumps({
            "results": [{"id": "1", "score": 0.30}],
            "result_count": 1,
            "min_score": 0.30,
            "max_score": 0.30,
            "avg_score": 0.30,
            "retrieval_sufficient": False,
            "threshold": 0.65,
        })

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
        rag_output = json.dumps({
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
        })

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

        rag_output = json.dumps({
            "results": [{"id": "1", "score": 0.85}],
            "result_count": 1,
            "min_score": 0.85,
            "max_score": 0.85,
            "avg_score": 0.85,
            "retrieval_sufficient": True,
            "threshold": 0.65,
        })

        step_result = StepResult(
            step=step,
            status="ok",
            result={"output": rag_output},
            messages=[],
        )

        should_replan, reason = agent_service._check_rag_retrieval_sufficiency(step_result, step)

        assert should_replan is False
        assert reason == ""
