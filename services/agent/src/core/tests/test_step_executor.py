"""Tests for the StepExecutorAgent."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from core.agents.executor import TOOL_TIMEOUT_SECONDS, StepExecutorAgent
from core.runtime.memory import MemoryRecord, MemoryStore
from core.tests.mocks import MockLLMClient
from core.tools.base import Tool
from shared.models import AgentMessage, AgentRequest, PlanStep, StepResult


@pytest.fixture
def mock_memory() -> MagicMock:
    """Create a mock memory store."""
    memory = MagicMock(spec=MemoryStore)
    memory.search = AsyncMock(return_value=[])
    return memory


@pytest.fixture
def mock_litellm() -> MockLLMClient:
    """Create a mock LiteLLM client."""
    return MockLLMClient(responses=["Test response from LLM"])


@pytest.fixture
def mock_tool_registry() -> tuple[MagicMock, MagicMock]:
    """Create a mock tool registry with a mock tool.

    Returns:
        tuple: (registry, tool) for easy access in tests
    """
    registry = MagicMock()
    tool = MagicMock(spec=Tool)
    tool.name = "test_tool"
    tool.run = AsyncMock(return_value="Tool output here")
    registry.get.return_value = tool
    return registry, tool


@pytest.fixture
def executor(
    mock_memory: MagicMock,
    mock_litellm: MockLLMClient,
    mock_tool_registry: tuple[MagicMock, MagicMock],
) -> StepExecutorAgent:
    """Create a StepExecutorAgent with mocked dependencies."""
    registry, _ = mock_tool_registry
    return StepExecutorAgent(
        memory=mock_memory,
        litellm=mock_litellm,
        tool_registry=registry,
    )


class TestMemoryStepExecution:
    """Tests for memory step execution."""

    @pytest.mark.asyncio
    async def test_execute_memory_step_with_query(
        self,
        executor: StepExecutorAgent,
        mock_memory: MagicMock,
    ) -> None:
        """Test memory step with explicit query."""
        # Setup
        step = PlanStep(
            id="1",
            label="Memory search",
            executor="agent",
            action="memory",
            args={"query": "test query", "limit": 3},
        )
        request = AgentRequest(prompt="user prompt")
        conversation_id = "conv123"

        # Mock memory results
        mock_memory.search.return_value = [
            MemoryRecord(conversation_id="conv123", text="Result 1"),
            MemoryRecord(conversation_id="conv123", text="Result 2"),
        ]

        # Execute
        result = await executor.run(
            step,
            request=request,
            conversation_id=conversation_id,
            prompt_history=[],
        )

        # Assert
        assert result.status == "ok"
        assert result.result["count"] == 2
        assert len(result.messages) == 2
        assert result.messages[0].content is not None
        assert "Result 1" in result.messages[0].content
        assert result.messages[1].content is not None
        assert "Result 2" in result.messages[1].content
        mock_memory.search.assert_called_once_with(
            "test query", limit=3, conversation_id=conversation_id
        )

    @pytest.mark.asyncio
    async def test_execute_memory_step_uses_prompt_as_fallback(
        self,
        executor: StepExecutorAgent,
        mock_memory: MagicMock,
    ) -> None:
        """Test memory step uses request.prompt when no query specified."""
        step = PlanStep(
            id="1",
            label="Memory search",
            executor="agent",
            action="memory",
            args={},
        )
        request = AgentRequest(prompt="user prompt text")
        conversation_id = "conv123"

        result = await executor.run(
            step,
            request=request,
            conversation_id=conversation_id,
            prompt_history=[],
        )

        assert result.status == "ok"
        mock_memory.search.assert_called_once_with(
            "user prompt text", limit=5, conversation_id=conversation_id
        )

    @pytest.mark.asyncio
    async def test_execute_memory_step_empty_results(
        self,
        executor: StepExecutorAgent,
        mock_memory: MagicMock,
    ) -> None:
        """Test memory step with no results."""
        step = PlanStep(
            id="1",
            label="Memory search",
            executor="agent",
            action="memory",
            args={"query": "test"},
        )
        request = AgentRequest(prompt="user prompt")
        conversation_id = "conv123"

        mock_memory.search.return_value = []

        result = await executor.run(
            step,
            request=request,
            conversation_id=conversation_id,
            prompt_history=[],
        )

        assert result.status == "ok"
        assert result.result["count"] == 0
        assert len(result.messages) == 0


class TestToolStepDispatch:
    """Tests for tool step dispatch and execution."""

    @pytest.mark.asyncio
    async def test_native_tool_found_and_executed(
        self,
        executor: StepExecutorAgent,
        mock_tool_registry: tuple[MagicMock, MagicMock],
    ) -> None:
        """Test that native tool is found and run() is called with correct args."""
        registry, tool = mock_tool_registry
        tool.run = AsyncMock(return_value="Native tool output")

        step = PlanStep(
            id="1",
            label="Run tool",
            executor="agent",
            action="tool",
            tool="test_tool",
            args={"param1": "value1"},
        )
        request = AgentRequest(prompt="test")

        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
        ):
            result = await executor.run(
                step,
                request=request,
                conversation_id="conv123",
                prompt_history=[],
            )

        assert result.status == "ok"
        assert result.result["output"] == "Native tool output"
        tool.run.assert_called_once_with(param1="value1")

    @pytest.mark.asyncio
    async def test_skill_dispatch_when_tool_not_in_registry(
        self,
        executor: StepExecutorAgent,
        mock_tool_registry: tuple[MagicMock, MagicMock],
    ) -> None:
        """Test skill dispatch when tool not found in registry."""
        registry, _ = mock_tool_registry
        registry.get.return_value = None  # Tool not in registry

        step = PlanStep(
            id="1",
            label="Run skill",
            executor="agent",
            action="tool",
            tool="nonexistent_skill",
            args={"goal": "test goal"},
        )
        request = AgentRequest(prompt="test")

        # Mock load_command to simulate skill not found
        with (
            patch("core.agents.executor.load_command") as mock_load,
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
        ):
            mock_load.side_effect = FileNotFoundError("Skill not found")

            result = await executor.run(
                step,
                request=request,
                conversation_id="conv123",
                prompt_history=[],
            )

        assert result.status == "missing"
        assert result.result["name"] == "nonexistent_skill"

    @pytest.mark.asyncio
    async def test_skill_dispatch_executes_via_llm(
        self,
        mock_memory: MagicMock,
        mock_litellm: MockLLMClient,
        mock_tool_registry: tuple[MagicMock, MagicMock],
    ) -> None:
        """Test skill dispatch calls LLM stream_chat."""
        registry, _ = mock_tool_registry
        registry.get.return_value = None  # Tool not in registry

        executor = StepExecutorAgent(
            memory=mock_memory,
            litellm=mock_litellm,
            tool_registry=registry,
        )

        step = PlanStep(
            id="1",
            label="Run skill",
            executor="agent",
            action="tool",
            tool="researcher",
            args={"goal": "find information"},
        )
        request = AgentRequest(prompt="test")

        # Mock load_command to return skill metadata
        with (
            patch("core.agents.executor.load_command") as mock_load,
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
        ):
            mock_load.return_value = (
                {"model": "skillsrunner"},
                "Rendered skill prompt with goal: find information",
            )

            result = await executor.run(
                step,
                request=request,
                conversation_id="conv123",
                prompt_history=[],
            )

        assert result.status == "ok"
        # MockLLMClient returns first response
        assert "Test response from LLM" in result.result["output"]

    @pytest.mark.asyncio
    async def test_streaming_tool_collects_content_chunks(
        self,
        executor: StepExecutorAgent,
        mock_tool_registry: tuple[MagicMock, MagicMock],
    ) -> None:
        """Test streaming tool (async generator) collects content chunks."""
        registry, tool = mock_tool_registry

        # Make tool.run an async generator
        async def mock_streaming_tool(**kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
            yield {"type": "content", "content": "Chunk 1"}
            yield {"type": "content", "content": " Chunk 2"}
            yield {"type": "content", "content": " Chunk 3"}

        tool.run = mock_streaming_tool

        step = PlanStep(
            id="1",
            label="Stream tool",
            executor="agent",
            action="tool",
            tool="test_tool",
            args={},
        )
        request = AgentRequest(prompt="test")

        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
            patch.object(inspect, "isasyncgenfunction", return_value=True),
        ):
            result = await executor.run(
                step,
                request=request,
                conversation_id="conv123",
                prompt_history=[],
            )

        assert result.status == "ok"
        assert result.result["output"] == "Chunk 1 Chunk 2 Chunk 3"


class TestContextInjection:
    """Tests for context injection into tools."""

    @pytest.mark.asyncio
    async def test_context_id_injected_for_homey_tool(
        self,
        executor: StepExecutorAgent,
        mock_tool_registry: tuple[MagicMock, MagicMock],
    ) -> None:
        """Test context_id is injected for homey tool."""
        registry, tool = mock_tool_registry
        registry.get.return_value = tool

        # Setup tool signature to accept context_id
        def mock_signature(*args: Any, **kwargs: Any) -> MagicMock:
            sig = MagicMock()
            sig.parameters = {"context_id": MagicMock()}
            return sig

        step = PlanStep(
            id="1",
            label="Homey control",
            executor="agent",
            action="tool",
            tool="homey",
            args={"action": "turn_on"},
        )
        context_id = str(uuid4())
        request = AgentRequest(
            prompt="test",
            metadata={"context_id": context_id},
        )

        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
            patch.object(inspect, "signature", side_effect=mock_signature),
        ):
            await executor.run(
                step,
                request=request,
                conversation_id="conv123",
                prompt_history=[],
            )

        # Verify context_id was injected
        call_args = tool.run.call_args
        assert "context_id" in call_args.kwargs
        from uuid import UUID

        assert call_args.kwargs["context_id"] == UUID(context_id)

    @pytest.mark.asyncio
    async def test_context_id_injected_for_azure_devops_tool(
        self,
        executor: StepExecutorAgent,
        mock_tool_registry: tuple[MagicMock, MagicMock],
    ) -> None:
        """Test context_id and session are injected for azure_devops tool."""
        registry, tool = mock_tool_registry
        registry.get.return_value = tool

        def mock_signature(*args: Any, **kwargs: Any) -> MagicMock:
            sig = MagicMock()
            sig.parameters = {"context_id": MagicMock(), "session": MagicMock()}
            return sig

        step = PlanStep(
            id="1",
            label="Azure DevOps",
            executor="agent",
            action="tool",
            tool="azure_devops",
            args={"action": "list_items"},
        )
        context_id = str(uuid4())
        mock_session = MagicMock()
        request = AgentRequest(
            prompt="test",
            metadata={"context_id": context_id, "_db_session": mock_session},
        )

        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
            patch.object(inspect, "signature", side_effect=mock_signature),
        ):
            await executor.run(
                step,
                request=request,
                conversation_id="conv123",
                prompt_history=[],
            )

        call_args = tool.run.call_args
        assert "context_id" in call_args.kwargs
        assert "session" in call_args.kwargs
        from uuid import UUID

        assert call_args.kwargs["context_id"] == UUID(context_id)
        assert call_args.kwargs["session"] == mock_session

    @pytest.mark.asyncio
    async def test_cwd_injected_from_step_args(
        self,
        executor: StepExecutorAgent,
        mock_tool_registry: tuple[MagicMock, MagicMock],
    ) -> None:
        """Test cwd is injected from step.args."""
        registry, tool = mock_tool_registry
        registry.get.return_value = tool

        def mock_signature(*args: Any, **kwargs: Any) -> MagicMock:
            sig = MagicMock()
            sig.parameters = {"cwd": MagicMock()}
            return sig

        step = PlanStep(
            id="1",
            label="Run command",
            executor="agent",
            action="tool",
            tool="test_tool",
            args={"cwd": "/tmp/workspace", "command": "ls"},  # noqa: S108
        )
        request = AgentRequest(prompt="test")

        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
            patch.object(inspect, "signature", side_effect=mock_signature),
        ):
            await executor.run(
                step,
                request=request,
                conversation_id="conv123",
                prompt_history=[],
            )

        call_args = tool.run.call_args
        assert call_args.kwargs["cwd"] == "/tmp/workspace"  # noqa: S108

    @pytest.mark.asyncio
    async def test_cwd_injected_from_metadata_fallback(
        self,
        executor: StepExecutorAgent,
        mock_tool_registry: tuple[MagicMock, MagicMock],
    ) -> None:
        """Test cwd falls back to request.metadata when not in step.args."""
        registry, tool = mock_tool_registry
        registry.get.return_value = tool

        def mock_signature(*args: Any, **kwargs: Any) -> MagicMock:
            sig = MagicMock()
            sig.parameters = {"cwd": MagicMock()}
            return sig

        step = PlanStep(
            id="1",
            label="Run command",
            executor="agent",
            action="tool",
            tool="test_tool",
            args={"command": "ls"},
        )
        request = AgentRequest(
            prompt="test",
            metadata={"cwd": "/tmp/from-metadata"},  # noqa: S108
        )

        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
            patch.object(inspect, "signature", side_effect=mock_signature),
        ):
            await executor.run(
                step,
                request=request,
                conversation_id="conv123",
                prompt_history=[],
            )

        call_args = tool.run.call_args
        assert call_args.kwargs["cwd"] == "/tmp/from-metadata"  # noqa: S108


class TestTimeoutEnforcement:
    """Tests for tool execution timeout."""

    @pytest.mark.asyncio
    async def test_tool_timeout_returns_error(
        self,
        executor: StepExecutorAgent,
        mock_tool_registry: tuple[MagicMock, MagicMock],
    ) -> None:
        """Test tool that times out returns error status."""
        registry, tool = mock_tool_registry

        # Make tool.run hang longer than timeout
        async def slow_tool(**kwargs: Any) -> str:
            await asyncio.sleep(TOOL_TIMEOUT_SECONDS + 1)
            return "Should not reach here"

        tool.run = slow_tool

        step = PlanStep(
            id="1",
            label="Slow tool",
            executor="agent",
            action="tool",
            tool="test_tool",
            args={},
        )
        request = AgentRequest(prompt="test")

        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
            patch.object(inspect, "isasyncgenfunction", return_value=False),
        ):
            result = await executor.run(
                step,
                request=request,
                conversation_id="conv123",
                prompt_history=[],
            )

        assert result.status == "error"
        assert "timed out" in result.result["reason"].lower()
        assert str(TOOL_TIMEOUT_SECONDS) in result.result["reason"]


class TestErrorDetection:
    """Tests for error detection in tool output."""

    @pytest.mark.asyncio
    async def test_error_prefix_sets_span_status(
        self,
        executor: StepExecutorAgent,
        mock_tool_registry: tuple[MagicMock, MagicMock],
    ) -> None:
        """Test tool output starting with 'Error:' sets span status to error."""
        registry, tool = mock_tool_registry
        tool.run = AsyncMock(return_value="Error: Something went wrong")

        step = PlanStep(
            id="1",
            label="Failing tool",
            executor="agent",
            action="tool",
            tool="test_tool",
            args={},
        )
        request = AgentRequest(prompt="test")

        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
            patch("core.agents.executor.set_span_status") as mock_set_status,
        ):
            result = await executor.run(
                step,
                request=request,
                conversation_id="conv123",
                prompt_history=[],
            )

        # Status should be 'error' (trace_status from error detection)
        assert result.status == "error"
        # Verify set_span_status was called
        mock_set_status.assert_called_once()
        assert mock_set_status.call_args[0][0] == "ERROR"
        # Verify system hint is added to message
        assert result.messages[0].content is not None
        assert "SYSTEM HINT" in result.messages[0].content

    @pytest.mark.asyncio
    async def test_traceback_in_output_sets_span_status(
        self,
        executor: StepExecutorAgent,
        mock_tool_registry: tuple[MagicMock, MagicMock],
    ) -> None:
        """Test tool output containing traceback sets span status to error."""
        registry, tool = mock_tool_registry
        tool_output = """Command failed:
Traceback (most recent call last):
  File "script.py", line 10, in main
    raise ValueError("Bad value")
ValueError: Bad value"""
        tool.run = AsyncMock(return_value=tool_output)

        step = PlanStep(
            id="1",
            label="Failing tool",
            executor="agent",
            action="tool",
            tool="test_tool",
            args={},
        )
        request = AgentRequest(prompt="test")

        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
            patch("core.agents.executor.set_span_status") as mock_set_status,
        ):
            result = await executor.run(
                step,
                request=request,
                conversation_id="conv123",
                prompt_history=[],
            )

        assert result.status == "error"
        mock_set_status.assert_called_once()
        assert result.messages[0].content is not None
        assert "SYSTEM HINT" in result.messages[0].content


class TestUnsupportedExecutorAction:
    """Tests for unsupported executor/action combinations."""

    @pytest.mark.asyncio
    async def test_unsupported_executor_returns_skipped(
        self,
        executor: StepExecutorAgent,
    ) -> None:
        """Test step with unknown executor is skipped gracefully."""
        step = PlanStep(
            id="1",
            label="Unknown step",
            executor="remote",
            action="memory",  # remote+memory is not a handled combination
            args={},
        )
        request = AgentRequest(prompt="test")

        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
        ):
            result = await executor.run(
                step,
                request=request,
                conversation_id="conv123",
                prompt_history=[],
            )

        assert result.status == "skipped"
        assert "unsupported" in result.result["reason"]


class TestCompletionStep:
    """Tests for completion step execution."""

    @pytest.mark.asyncio
    async def test_completion_step_calls_llm_stream_chat(
        self,
        executor: StepExecutorAgent,
        mock_litellm: MockLLMClient,
    ) -> None:
        """Test completion step calls LLM stream_chat."""
        step = PlanStep(
            id="1",
            label="Final answer",
            executor="litellm",
            action="completion",
            args={"model": "composer"},
        )
        request = AgentRequest(prompt="user question")
        prompt_history = [
            AgentMessage(role="user", content="user question"),
            AgentMessage(role="system", content="Tool output: some data"),
        ]

        with patch("core.agents.executor.start_span"):
            result = await executor.run(
                step,
                request=request,
                conversation_id="conv123",
                prompt_history=prompt_history,
            )

        assert result.status == "ok"
        assert "completion" in result.result
        assert result.result["model"] == "composer"
        # MockLLMClient should have been called
        assert len(mock_litellm.call_history) > 0

    @pytest.mark.asyncio
    async def test_completion_step_includes_composer_system_prompt(
        self,
        executor: StepExecutorAgent,
        mock_litellm: MockLLMClient,
    ) -> None:
        """Test completion step includes composer system prompt."""
        step = PlanStep(
            id="1",
            label="Final answer",
            executor="litellm",
            action="completion",
            args={},
        )
        request = AgentRequest(prompt="user question")
        prompt_history = [AgentMessage(role="user", content="user question")]

        with patch("core.agents.executor.start_span"):
            await executor.run(
                step,
                request=request,
                conversation_id="conv123",
                prompt_history=prompt_history,
            )

        # Check that call history includes composer system prompt
        assert len(mock_litellm.call_history) > 0
        messages_sent = mock_litellm.call_history[0]
        system_messages = [m for m in messages_sent if m.role == "system"]
        assert len(system_messages) > 0
        # Verify composer system prompt content
        assert any(
            m.content is not None and "synthesizing" in m.content.lower() for m in system_messages
        )


class TestRunStreamGenerator:
    """Tests for run_stream async generator."""

    @pytest.mark.asyncio
    async def test_run_stream_yields_result_event(
        self,
        executor: StepExecutorAgent,
        mock_memory: MagicMock,
    ) -> None:
        """Test run_stream yields result events."""
        step = PlanStep(
            id="1",
            label="Memory",
            executor="agent",
            action="memory",
            args={"query": "test"},
        )
        request = AgentRequest(prompt="test")

        mock_memory.search.return_value = []

        events: list[dict[str, Any]] = []
        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
        ):
            async for event in executor.run_stream(
                step,
                request=request,
                conversation_id="conv123",
                prompt_history=[],
            ):
                events.append(event)

        # Should yield at least one result event
        result_events = [e for e in events if e["type"] == "result"]
        assert len(result_events) == 1
        assert isinstance(result_events[0]["result"], StepResult)

    @pytest.mark.asyncio
    async def test_run_stream_catches_exceptions(
        self,
        executor: StepExecutorAgent,
        mock_memory: MagicMock,
    ) -> None:
        """Test run_stream catches and yields error on exception."""
        step = PlanStep(
            id="1",
            label="Memory",
            executor="agent",
            action="memory",
            args={"query": "test"},
        )
        request = AgentRequest(prompt="test")

        # Make memory.search raise an exception
        mock_memory.search.side_effect = RuntimeError("Database error")

        events: list[dict[str, Any]] = []
        with (
            patch("core.agents.executor.start_span"),
            patch("core.agents.executor.current_trace_ids"),
            patch("core.agents.executor.log_event"),
        ):
            async for event in executor.run_stream(
                step,
                request=request,
                conversation_id="conv123",
                prompt_history=[],
            ):
                events.append(event)

        # Should yield error result
        result_events = [e for e in events if e["type"] == "result"]
        assert len(result_events) == 1
        assert result_events[0]["result"].status == "error"
        assert "Database error" in result_events[0]["result"].result["error"]
