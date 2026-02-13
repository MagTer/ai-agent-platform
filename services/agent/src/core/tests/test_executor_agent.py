"""Tests for StepExecutorAgent at agent interface level.

Note: test_step_executor.py covers detailed execution modes.
This file focuses on agent-level orchestration and streaming.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shared.models import AgentMessage, AgentRequest, PlanStep, StepResult

from core.agents.executor import StepExecutorAgent
from core.core.memory import MemoryStore
from core.tests.mocks import MockLLMClient
from core.tools.base import Tool


@pytest.fixture
def mock_memory() -> MagicMock:
    """Create a mock memory store."""
    memory = MagicMock(spec=MemoryStore)
    memory.search = AsyncMock(return_value=[])
    return memory


@pytest.fixture
def mock_litellm() -> MockLLMClient:
    """Create a mock LiteLLM client."""
    return MockLLMClient(responses=["Test LLM response"])


@pytest.fixture
def mock_tool() -> MagicMock:
    """Create a mock tool."""
    tool = MagicMock(spec=Tool)
    tool.name = "test_tool"
    tool.run = AsyncMock(return_value="Tool output")
    return tool


@pytest.fixture
def mock_tool_registry(mock_tool: MagicMock) -> MagicMock:
    """Create a mock tool registry."""
    registry = MagicMock()
    registry.get.return_value = mock_tool
    return registry


@pytest.fixture
def executor(
    mock_memory: MagicMock,
    mock_litellm: MockLLMClient,
    mock_tool_registry: MagicMock,
) -> StepExecutorAgent:
    """Create a StepExecutorAgent with mocked dependencies."""
    return StepExecutorAgent(
        memory=mock_memory,
        litellm=mock_litellm,
        tool_registry=mock_tool_registry,
    )


class TestStepExecutorAgentRun:
    """Tests for StepExecutorAgent.run (non-streaming wrapper)."""

    @pytest.mark.asyncio
    async def test_run_returns_step_result(
        self,
        executor: StepExecutorAgent,
        mock_memory: MagicMock,
    ) -> None:
        """Run should return a StepResult object."""
        step = PlanStep(
            id="1",
            label="Memory search",
            executor="agent",
            action="memory",
            args={"query": "test"},
        )
        request = AgentRequest(prompt="user query")

        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
        ):
            result = await executor.run(
                step=step,
                request=request,
                conversation_id="conv-123",
                prompt_history=[],
            )

        assert isinstance(result, StepResult)
        assert result.step == step
        assert result.status in {"ok", "error", "skipped"}

    @pytest.mark.asyncio
    async def test_run_wraps_stream_correctly(
        self,
        executor: StepExecutorAgent,
        mock_memory: MagicMock,
    ) -> None:
        """Run should correctly extract result from stream events."""
        step = PlanStep(
            id="1",
            label="Memory search",
            executor="agent",
            action="memory",
            args={"query": "test"},
        )
        request = AgentRequest(prompt="user query")

        # Mock memory search
        mock_memory.search.return_value = []

        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
        ):
            result = await executor.run(
                step=step,
                request=request,
                conversation_id="conv-123",
                prompt_history=[],
            )

        # Should get the final result from stream
        assert result.status == "ok"
        assert result.result["count"] == 0


class TestStepExecutorAgentRunStream:
    """Tests for StepExecutorAgent.run_stream (async generator)."""

    @pytest.mark.asyncio
    async def test_run_stream_yields_result_event(
        self,
        executor: StepExecutorAgent,
        mock_memory: MagicMock,
    ) -> None:
        """Run stream should yield result event."""
        step = PlanStep(
            id="1",
            label="Memory search",
            executor="agent",
            action="memory",
            args={"query": "test"},
        )
        request = AgentRequest(prompt="user query")

        events: list[dict[str, Any]] = []
        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
        ):
            async for event in executor.run_stream(
                step=step,
                request=request,
                conversation_id="conv-123",
                prompt_history=[],
            ):
                events.append(event)

        # Should have at least one result event
        result_events = [e for e in events if e["type"] == "result"]
        assert len(result_events) == 1
        assert isinstance(result_events[0]["result"], StepResult)

    @pytest.mark.asyncio
    async def test_run_stream_sets_span_attributes(
        self,
        executor: StepExecutorAgent,
        mock_memory: MagicMock,
    ) -> None:
        """Run stream should set span attributes for observability."""
        step = PlanStep(
            id="step-1",
            label="Test step",
            executor="agent",
            action="memory",
            args={"query": "test"},
        )
        request = AgentRequest(prompt="user query")

        with (
            patch("core.agents.executor.start_span") as mock_span,
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
        ):
            async for _ in executor.run_stream(
                step=step,
                request=request,
                conversation_id="conv-123",
                prompt_history=[],
            ):
                pass

            # Verify span attributes were set
            span_instance = mock_span.return_value.__enter__.return_value
            span_instance.set_attribute.assert_any_call("action", "memory")
            span_instance.set_attribute.assert_any_call("executor", "agent")
            span_instance.set_attribute.assert_any_call("step", "step-1")

    @pytest.mark.asyncio
    async def test_run_stream_captures_step_args_in_span(
        self,
        executor: StepExecutorAgent,
        mock_memory: MagicMock,
    ) -> None:
        """Run stream should capture step.args in span attributes."""
        step = PlanStep(
            id="1",
            label="Memory search",
            executor="agent",
            action="memory",
            args={"query": "test query", "limit": 5},
        )
        request = AgentRequest(prompt="user query")

        with (
            patch("core.agents.executor.start_span") as mock_span,
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
        ):
            async for _ in executor.run_stream(
                step=step,
                request=request,
                conversation_id="conv-123",
                prompt_history=[],
            ):
                pass

            # Should capture args
            span_instance = mock_span.return_value.__enter__.return_value
            # Check that step.args was set (exact format may vary)
            args_calls = [
                call
                for call in span_instance.set_attribute.call_args_list
                if call[0][0] == "step.args"
            ]
            assert len(args_calls) > 0

    @pytest.mark.asyncio
    async def test_run_stream_logs_step_event(
        self,
        executor: StepExecutorAgent,
        mock_memory: MagicMock,
    ) -> None:
        """Run stream should log StepEvent with correct metadata."""
        step = PlanStep(
            id="step-1",
            label="Test step",
            executor="agent",
            action="memory",
            args={"query": "test"},
        )
        request = AgentRequest(prompt="user query")

        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids", return_value={"trace_id": "trace-123"}),
            patch("core.agents.executor.log_event") as mock_log,
        ):
            async for _ in executor.run_stream(
                step=step,
                request=request,
                conversation_id="conv-123",
                prompt_history=[],
            ):
                pass

            # Verify log_event was called with StepEvent
            assert mock_log.called
            from core.models.pydantic_schemas import StepEvent

            step_events = [
                call[0][0] for call in mock_log.call_args_list if isinstance(call[0][0], StepEvent)
            ]
            assert len(step_events) == 1
            assert step_events[0].step_id == "step-1"
            assert step_events[0].action == "memory"


class TestStepExecutorDispatch:
    """Tests for step executor dispatch logic."""

    @pytest.mark.asyncio
    async def test_dispatches_to_memory_step(
        self,
        executor: StepExecutorAgent,
        mock_memory: MagicMock,
    ) -> None:
        """Should dispatch agent/memory to _execute_memory_step."""
        step = PlanStep(
            id="1",
            label="Memory search",
            executor="agent",
            action="memory",
            args={"query": "test"},
        )
        request = AgentRequest(prompt="user query")

        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
        ):
            result = await executor.run(
                step=step,
                request=request,
                conversation_id="conv-123",
                prompt_history=[],
            )

        # Verify memory.search was called
        mock_memory.search.assert_called_once()
        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_dispatches_to_tool_step(
        self,
        executor: StepExecutorAgent,
        mock_tool_registry: MagicMock,
        mock_tool: MagicMock,
    ) -> None:
        """Should dispatch agent/tool to _execute_tool_step."""
        step = PlanStep(
            id="1",
            label="Run tool",
            executor="agent",
            action="tool",
            tool="test_tool",
            args={"param": "value"},
        )
        request = AgentRequest(prompt="user query")

        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
        ):
            result = await executor.run(
                step=step,
                request=request,
                conversation_id="conv-123",
                prompt_history=[],
            )

        # Verify tool.run was called
        mock_tool.run.assert_called_once()
        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_dispatches_to_completion_step(
        self,
        executor: StepExecutorAgent,
        mock_litellm: MockLLMClient,
    ) -> None:
        """Should dispatch litellm/completion to _execute_completion_step."""
        step = PlanStep(
            id="1",
            label="Final answer",
            executor="litellm",
            action="completion",
            args={},
        )
        request = AgentRequest(prompt="user query")
        prompt_history = [AgentMessage(role="user", content="user query")]

        with (patch("core.agents.executor.start_span"),):
            result = await executor.run(
                step=step,
                request=request,
                conversation_id="conv-123",
                prompt_history=prompt_history,
            )

        # Verify LLM was called
        assert len(mock_litellm.call_history) > 0
        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_unsupported_executor_action_returns_skipped(
        self,
        executor: StepExecutorAgent,
    ) -> None:
        """Should return skipped status for unsupported executor/action combos."""
        # Use valid executor but unsupported combination (remote+memory)
        step = PlanStep(
            id="1",
            label="Unsupported combo",
            executor="remote",
            action="memory",  # remote+memory is not handled
            args={},
        )
        request = AgentRequest(prompt="user query")

        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
        ):
            result = await executor.run(
                step=step,
                request=request,
                conversation_id="conv-123",
                prompt_history=[],
            )

        assert result.status == "skipped"
        assert "unsupported" in result.result["reason"]


class TestExceptionHandling:
    """Tests for exception handling in step execution."""

    @pytest.mark.asyncio
    async def test_run_stream_catches_generic_exceptions(
        self,
        executor: StepExecutorAgent,
        mock_memory: MagicMock,
    ) -> None:
        """Run stream should catch and yield error on exception."""
        step = PlanStep(
            id="1",
            label="Memory search",
            executor="agent",
            action="memory",
            args={"query": "test"},
        )
        request = AgentRequest(prompt="user query")

        # Make memory.search raise an exception
        mock_memory.search.side_effect = RuntimeError("Database failure")

        events: list[dict[str, Any]] = []
        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
        ):
            async for event in executor.run_stream(
                step=step,
                request=request,
                conversation_id="conv-123",
                prompt_history=[],
            ):
                events.append(event)

        # Should yield error result
        result_events = [e for e in events if e["type"] == "result"]
        assert len(result_events) == 1
        assert result_events[0]["result"].status == "error"
        assert "Database failure" in result_events[0]["result"].result["error"]

    @pytest.mark.asyncio
    async def test_run_stream_reraises_tool_confirmation_error(
        self,
        executor: StepExecutorAgent,
        mock_memory: MagicMock,
    ) -> None:
        """Run stream should re-raise ToolConfirmationError."""
        from core.tools.base import ToolConfirmationError

        step = PlanStep(
            id="1",
            label="Memory search",
            executor="agent",
            action="memory",
            args={"query": "test"},
        )
        request = AgentRequest(prompt="user query")

        # Make memory.search raise ToolConfirmationError (with required args)
        mock_memory.search.side_effect = ToolConfirmationError(
            tool_name="memory_search", tool_args={"query": "test"}
        )

        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
            pytest.raises(ToolConfirmationError, match="requires confirmation"),
        ):
            async for _ in executor.run_stream(
                step=step,
                request=request,
                conversation_id="conv-123",
                prompt_history=[],
            ):
                pass


class TestSpanNaming:
    """Tests for span naming in observability."""

    @pytest.mark.asyncio
    async def test_span_name_uses_label_if_available(
        self,
        executor: StepExecutorAgent,
        mock_memory: MagicMock,
    ) -> None:
        """Span name should use step.label if available."""
        step = PlanStep(
            id="1",
            label="Search context memory",
            executor="agent",
            action="memory",
            args={"query": "test"},
        )
        request = AgentRequest(prompt="user query")

        with (
            patch("core.agents.executor.start_span") as mock_span,
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
        ):
            async for _ in executor.run_stream(
                step=step,
                request=request,
                conversation_id="conv-123",
                prompt_history=[],
            ):
                pass

            # Verify span was started with label
            mock_span.assert_called()
            span_name = mock_span.call_args[0][0]
            assert "Search context memory" in span_name

    @pytest.mark.asyncio
    async def test_span_name_falls_back_to_id(
        self,
        executor: StepExecutorAgent,
        mock_memory: MagicMock,
    ) -> None:
        """Span name should use step.id if no label."""
        step = PlanStep(
            id="step-123",
            label="",  # Empty label
            executor="agent",
            action="memory",
            args={"query": "test"},
        )
        request = AgentRequest(prompt="user query")

        with (
            patch("core.agents.executor.start_span") as mock_span,
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
        ):
            async for _ in executor.run_stream(
                step=step,
                request=request,
                conversation_id="conv-123",
                prompt_history=[],
            ):
                pass

            # Verify span was started with ID
            span_name = mock_span.call_args[0][0]
            assert "step-123" in span_name


class TestDurationTracking:
    """Tests for duration tracking and latency measurement."""

    @pytest.mark.asyncio
    async def test_run_stream_tracks_latency(
        self,
        executor: StepExecutorAgent,
        mock_memory: MagicMock,
    ) -> None:
        """Run stream should track and report latency_ms."""
        step = PlanStep(
            id="1",
            label="Memory search",
            executor="agent",
            action="memory",
            args={"query": "test"},
        )
        request = AgentRequest(prompt="user query")

        with (
            patch("core.agents.executor.start_span") as mock_span,
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
        ):
            async for _ in executor.run_stream(
                step=step,
                request=request,
                conversation_id="conv-123",
                prompt_history=[],
            ):
                pass

            # Verify latency_ms was set
            span_instance = mock_span.return_value.__enter__.return_value
            latency_calls = [
                call
                for call in span_instance.set_attribute.call_args_list
                if call[0][0] == "latency_ms"
            ]
            assert len(latency_calls) == 1
            # Latency should be a positive number
            assert latency_calls[0][0][1] > 0


class TestArgCoercion:
    """Tests for argument coercion and validation."""

    @pytest.mark.asyncio
    async def test_memory_step_coerces_limit_to_int(
        self,
        executor: StepExecutorAgent,
        mock_memory: MagicMock,
    ) -> None:
        """Memory step should coerce limit to int."""
        step = PlanStep(
            id="1",
            label="Memory search",
            executor="agent",
            action="memory",
            args={"query": "test", "limit": "10"},  # String instead of int
        )
        request = AgentRequest(prompt="user query")

        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
        ):
            await executor.run(
                step=step,
                request=request,
                conversation_id="conv-123",
                prompt_history=[],
            )

        # Verify memory.search was called with int limit
        mock_memory.search.assert_called_once()
        call_kwargs = mock_memory.search.call_args.kwargs
        assert call_kwargs["limit"] == 10
        assert isinstance(call_kwargs["limit"], int)

    @pytest.mark.asyncio
    async def test_memory_step_defaults_limit_to_5(
        self,
        executor: StepExecutorAgent,
        mock_memory: MagicMock,
    ) -> None:
        """Memory step should default limit to 5."""
        step = PlanStep(
            id="1",
            label="Memory search",
            executor="agent",
            action="memory",
            args={"query": "test"},  # No limit specified
        )
        request = AgentRequest(prompt="user query")

        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
        ):
            await executor.run(
                step=step,
                request=request,
                conversation_id="conv-123",
                prompt_history=[],
            )

        # Verify default limit
        call_kwargs = mock_memory.search.call_args.kwargs
        assert call_kwargs["limit"] == 5

    @pytest.mark.asyncio
    async def test_memory_step_handles_invalid_limit_gracefully(
        self,
        executor: StepExecutorAgent,
        mock_memory: MagicMock,
    ) -> None:
        """Memory step should handle invalid limit values gracefully."""
        step = PlanStep(
            id="1",
            label="Memory search",
            executor="agent",
            action="memory",
            args={"query": "test", "limit": "not-a-number"},
        )
        request = AgentRequest(prompt="user query")

        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
        ):
            await executor.run(
                step=step,
                request=request,
                conversation_id="conv-123",
                prompt_history=[],
            )

        # Should fall back to default limit
        call_kwargs = mock_memory.search.call_args.kwargs
        assert call_kwargs["limit"] == 5


class TestCompletionStepModel:
    """Tests for completion step model selection."""

    @pytest.mark.asyncio
    async def test_completion_uses_composer_model_by_default(
        self,
        executor: StepExecutorAgent,
        mock_litellm: MockLLMClient,
    ) -> None:
        """Completion step should use 'composer' model by default."""
        step = PlanStep(
            id="1",
            label="Final answer",
            executor="litellm",
            action="completion",
            args={},  # No model specified
        )
        request = AgentRequest(prompt="user query")

        with patch("core.agents.executor.start_span"):
            result = await executor.run(
                step=step,
                request=request,
                conversation_id="conv-123",
                prompt_history=[],
            )

        assert result.result["model"] == "composer"

    @pytest.mark.asyncio
    async def test_completion_respects_model_override(
        self,
        executor: StepExecutorAgent,
        mock_litellm: MockLLMClient,
    ) -> None:
        """Completion step should respect model override in args."""
        step = PlanStep(
            id="1",
            label="Final answer",
            executor="litellm",
            action="completion",
            args={"model": "custom-model"},
        )
        request = AgentRequest(prompt="user query")

        with patch("core.agents.executor.start_span"):
            result = await executor.run(
                step=step,
                request=request,
                conversation_id="conv-123",
                prompt_history=[],
            )

        assert result.result["model"] == "custom-model"

    @pytest.mark.asyncio
    async def test_completion_includes_composer_system_prompt(
        self,
        executor: StepExecutorAgent,
        mock_litellm: MockLLMClient,
    ) -> None:
        """Completion step should include composer system prompt."""
        step = PlanStep(
            id="1",
            label="Final answer",
            executor="litellm",
            action="completion",
            args={},
        )
        request = AgentRequest(prompt="user query")
        prompt_history = [AgentMessage(role="user", content="user query")]

        with patch("core.agents.executor.start_span"):
            await executor.run(
                step=step,
                request=request,
                conversation_id="conv-123",
                prompt_history=prompt_history,
            )

        # Verify system prompt was included
        assert len(mock_litellm.call_history) > 0
        messages = mock_litellm.call_history[0]
        system_messages = [m for m in messages if m.role == "system"]
        assert len(system_messages) > 0
        # Check for composer-specific instructions
        assert any(
            m.content is not None and "synthesizing" in m.content.lower() for m in system_messages
        )
