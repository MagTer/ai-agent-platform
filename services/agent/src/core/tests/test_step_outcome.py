"""Tests for the StepOutcome enum and supervisor integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from shared.models import StepOutcome

if TYPE_CHECKING:
    from core.agents.supervisor_step import StepSupervisorAgent


class TestStepOutcome:
    """Tests for the StepOutcome enum."""

    def test_outcome_values(self) -> None:
        """Test that StepOutcome has correct values."""
        assert StepOutcome.SUCCESS.value == "success"
        assert StepOutcome.RETRY.value == "retry"
        assert StepOutcome.REPLAN.value == "replan"
        assert StepOutcome.ABORT.value == "abort"

    def test_outcome_is_string_enum(self) -> None:
        """Test that StepOutcome is a string enum."""
        # StepOutcome.value is the string value
        assert StepOutcome.SUCCESS.value == "success"
        # String enum comparison works
        assert StepOutcome.SUCCESS == "success"

    def test_outcome_all_variants(self) -> None:
        """Test that all expected variants exist."""
        variants = [StepOutcome.SUCCESS, StepOutcome.RETRY, StepOutcome.REPLAN, StepOutcome.ABORT]
        assert len(variants) == 4

    def test_outcome_comparison(self) -> None:
        """Test StepOutcome comparison."""
        assert StepOutcome.SUCCESS != StepOutcome.RETRY
        assert StepOutcome.SUCCESS == StepOutcome.SUCCESS


class TestSupervisorParseResponse:
    """Tests for supervisor response parsing with StepOutcome."""

    @pytest.fixture
    def supervisor(self) -> StepSupervisorAgent:
        """Create a StepSupervisorAgent instance."""
        from unittest.mock import MagicMock

        from core.agents.supervisor_step import StepSupervisorAgent

        mock_litellm = MagicMock()
        return StepSupervisorAgent(litellm=mock_litellm)

    def test_parse_success(self, supervisor: StepSupervisorAgent) -> None:
        """Test parsing success outcome."""
        response = '{"outcome": "success", "reason": "Step completed"}'
        outcome, reason, fix = supervisor._parse_response(response)

        assert outcome == StepOutcome.SUCCESS
        assert reason == "Step completed"
        assert fix is None

    def test_parse_retry(self, supervisor: StepSupervisorAgent) -> None:
        """Test parsing retry outcome."""
        response = '{"outcome": "retry", "reason": "Timeout", "suggested_fix": "Retry request"}'
        outcome, reason, fix = supervisor._parse_response(response)

        assert outcome == StepOutcome.RETRY
        assert reason == "Timeout"
        assert fix == "Retry request"

    def test_parse_replan(self, supervisor: StepSupervisorAgent) -> None:
        """Test parsing replan outcome."""
        response = '{"outcome": "replan", "reason": "Auth failed"}'
        outcome, reason, fix = supervisor._parse_response(response)

        assert outcome == StepOutcome.REPLAN
        assert reason == "Auth failed"

    def test_parse_abort(self, supervisor: StepSupervisorAgent) -> None:
        """Test parsing abort outcome."""
        response = '{"outcome": "abort", "reason": "Critical error"}'
        outcome, reason, fix = supervisor._parse_response(response)

        assert outcome == StepOutcome.ABORT
        assert reason == "Critical error"

    def test_parse_legacy_ok(self, supervisor: StepSupervisorAgent) -> None:
        """Test parsing legacy 'ok' decision."""
        response = '{"decision": "ok", "reason": "OK"}'
        outcome, reason, fix = supervisor._parse_response(response)

        assert outcome == StepOutcome.SUCCESS

    def test_parse_legacy_adjust(self, supervisor: StepSupervisorAgent) -> None:
        """Test parsing legacy 'adjust' decision."""
        response = '{"decision": "adjust", "reason": "Needs adjustment"}'
        outcome, reason, fix = supervisor._parse_response(response)

        assert outcome == StepOutcome.REPLAN

    def test_parse_retry_with_max_retries(self, supervisor: StepSupervisorAgent) -> None:
        """Test that RETRY escalates to REPLAN when retry_count >= 1."""
        response = '{"outcome": "retry", "reason": "Timeout"}'

        # First retry allowed
        outcome, _, _ = supervisor._parse_response(response, retry_count=0)
        assert outcome == StepOutcome.RETRY

        # Second retry escalates to REPLAN
        outcome, _, _ = supervisor._parse_response(response, retry_count=1)
        assert outcome == StepOutcome.REPLAN

    def test_parse_invalid_json(self, supervisor: StepSupervisorAgent) -> None:
        """Test parsing invalid JSON defaults to SUCCESS."""
        response = "not json"
        outcome, reason, fix = supervisor._parse_response(response)

        assert outcome == StepOutcome.SUCCESS
        assert "parse" in reason.lower() or "json" in reason.lower()

    def test_parse_json_in_text(self, supervisor: StepSupervisorAgent) -> None:
        """Test extracting JSON from surrounding text."""
        response = 'Here is my response: {"outcome": "success", "reason": "Done"} That is all.'
        outcome, reason, fix = supervisor._parse_response(response)

        assert outcome == StepOutcome.SUCCESS
        assert reason == "Done"

    def test_parse_unknown_outcome(self, supervisor: StepSupervisorAgent) -> None:
        """Test that unknown outcome defaults to SUCCESS."""
        response = '{"outcome": "unknown_value", "reason": "test"}'
        outcome, reason, fix = supervisor._parse_response(response)

        assert outcome == StepOutcome.SUCCESS
