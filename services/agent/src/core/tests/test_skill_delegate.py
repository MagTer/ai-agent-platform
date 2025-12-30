"""Tests for SkillDelegateTool."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.tools.registry import ToolRegistry
from core.tools.skill_delegate import SkillDelegateTool


class MockStreamingLLMClient:
    """Mock LLM client that yields streaming chunks for testing skill delegation."""

    def __init__(self, stream_chunks: list[list[dict[str, Any]]]) -> None:
        """Initialize with a list of chunk sequences (one per call)."""
        self._stream_chunks = stream_chunks
        self._call_index = 0
        self.call_history: list[Any] = []

    async def stream_chat(
        self,
        messages: Any,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Yield chunks from the current call's sequence."""
        self.call_history.append(messages)

        if self._call_index >= len(self._stream_chunks):
            # Default: yield simple content
            yield {"type": "content", "content": "No more responses"}
            return

        chunks = self._stream_chunks[self._call_index]
        self._call_index += 1

        for chunk in chunks:
            yield chunk


@pytest.fixture
def mock_registry() -> tuple[ToolRegistry, AsyncMock]:
    """Create a tool registry with a simple mock tool.

    Returns:
        Tuple of (registry, mock_tool_run) for verification.
    """
    registry = ToolRegistry()

    # Create a simple mock tool
    mock_tool = MagicMock()
    mock_tool.name = "web_search"
    mock_tool.description = "Search the web"
    mock_run = AsyncMock(return_value="Search results for test query")
    mock_tool.run = mock_run

    registry.register(mock_tool)
    return registry, mock_run


@pytest.fixture
def simple_skill_metadata() -> tuple[dict[str, Any], str]:
    """Return simple skill metadata and system prompt."""
    metadata = {"tools": ["web_search"]}
    system_prompt = "You are a helpful researcher."
    return metadata, system_prompt


class TestSkillDelegateTool:
    """Test cases for SkillDelegateTool."""

    @pytest.mark.asyncio
    async def test_skill_not_found(self, mock_registry: tuple[ToolRegistry, AsyncMock]) -> None:
        """Test error handling when skill file doesn't exist."""
        registry, _ = mock_registry
        mock_llm = MockStreamingLLMClient([])
        tool = SkillDelegateTool(mock_llm, registry)  # type: ignore[arg-type]

        chunks = []
        async for chunk in tool.run(skill="nonexistent_skill", goal="Do something"):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0]["type"] == "result"
        assert "not found" in chunks[0]["output"].lower()

    @pytest.mark.asyncio
    async def test_streaming_content_yield(
        self,
        mock_registry: tuple[ToolRegistry, AsyncMock],
        simple_skill_metadata: tuple[dict[str, Any], str],
    ) -> None:
        """Test that content chunks are yielded during streaming."""
        registry, _ = mock_registry
        # Mock streaming: content tokens then empty (no tool calls)
        stream_chunks = [
            [
                {"type": "content", "content": "Hello "},
                {"type": "content", "content": "World!"},
            ]
        ]

        mock_llm = MockStreamingLLMClient(stream_chunks)
        tool = SkillDelegateTool(mock_llm, registry)  # type: ignore[arg-type]

        with patch("core.tools.skill_delegate.load_command", return_value=simple_skill_metadata):
            chunks = []
            async for chunk in tool.run(skill="researcher", goal="Say hello"):
                chunks.append(chunk)

        # Should have: thinking (turn start), content x2, result (finished), result (content)
        content_chunks = [c for c in chunks if c["type"] == "content"]
        assert len(content_chunks) == 2
        assert content_chunks[0]["content"] == "Hello "
        assert content_chunks[1]["content"] == "World!"

        # Should have result chunks
        result_chunks = [c for c in chunks if c["type"] == "result"]
        assert len(result_chunks) >= 1
        # Final result should contain the content
        assert any("Hello World!" in c.get("output", "") for c in result_chunks)

    @pytest.mark.asyncio
    async def test_tool_invocation(
        self,
        mock_registry: tuple[ToolRegistry, AsyncMock],
        simple_skill_metadata: tuple[dict[str, Any], str],
    ) -> None:
        """Test that tool calls are properly executed."""
        registry, mock_run = mock_registry
        # First call: LLM requests a tool
        tool_call = {
            "index": 0,
            "id": "call_123",
            "function": {"name": "web_search", "arguments": json.dumps({"query": "test"})},
        }

        # Second call: LLM produces final response
        stream_chunks: list[list[dict[str, Any]]] = [
            [{"type": "tool_start", "content": None, "tool_call": tool_call}],
            [{"type": "content", "content": "Based on search results..."}],
        ]

        mock_llm = MockStreamingLLMClient(stream_chunks)
        tool = SkillDelegateTool(mock_llm, registry)  # type: ignore[arg-type]

        with patch("core.tools.skill_delegate.load_command", return_value=simple_skill_metadata):
            chunks = []
            async for chunk in tool.run(skill="researcher", goal="Search for test"):
                chunks.append(chunk)

        # Verify the mock tool was called
        mock_run.assert_called_once()

        # Should have thinking chunks about tool invocation
        thinking_chunks = [c for c in chunks if c["type"] == "thinking"]
        assert any("web_search" in c.get("content", "") for c in thinking_chunks)

    @pytest.mark.asyncio
    async def test_error_chunk_handling(
        self,
        mock_registry: tuple[ToolRegistry, AsyncMock],
        simple_skill_metadata: tuple[dict[str, Any], str],
    ) -> None:
        """Test that error chunks terminate the worker properly."""
        registry, _ = mock_registry
        stream_chunks = [
            [
                {"type": "content", "content": "Starting..."},
                {"type": "error", "content": "LLM error occurred"},
            ]
        ]

        mock_llm = MockStreamingLLMClient(stream_chunks)
        tool = SkillDelegateTool(mock_llm, registry)  # type: ignore[arg-type]

        with patch("core.tools.skill_delegate.load_command", return_value=simple_skill_metadata):
            chunks = []
            async for chunk in tool.run(skill="researcher", goal="Do something"):
                chunks.append(chunk)

        # Should have result chunk with error
        result_chunks = [c for c in chunks if c["type"] == "result"]
        assert len(result_chunks) >= 1
        assert any("error" in c.get("output", "").lower() for c in result_chunks)

    @pytest.mark.asyncio
    async def test_empty_response_handling(
        self,
        mock_registry: tuple[ToolRegistry, AsyncMock],
        simple_skill_metadata: tuple[dict[str, Any], str],
    ) -> None:
        """Test handling when worker produces empty response."""
        registry, _ = mock_registry
        # No content, no tool calls
        stream_chunks: list[list[dict[str, Any]]] = [[]]

        mock_llm = MockStreamingLLMClient(stream_chunks)
        tool = SkillDelegateTool(mock_llm, registry)  # type: ignore[arg-type]

        with patch("core.tools.skill_delegate.load_command", return_value=simple_skill_metadata):
            chunks = []
            async for chunk in tool.run(skill="researcher", goal="Do something"):
                chunks.append(chunk)

        # Should have result indicating empty response
        result_chunks = [c for c in chunks if c["type"] == "result"]
        assert len(result_chunks) >= 1
        assert any("empty" in c.get("output", "").lower() for c in result_chunks)

    @pytest.mark.asyncio
    async def test_max_turns_reached(
        self,
        mock_registry: tuple[ToolRegistry, AsyncMock],
        simple_skill_metadata: tuple[dict[str, Any], str],
    ) -> None:
        """Test that worker terminates after max turns."""
        registry, _ = mock_registry
        # Create tool calls that keep the loop going
        tool_call = {
            "index": 0,
            "id": "call_123",
            "function": {"name": "web_search", "arguments": json.dumps({"query": "test"})},
        }

        # 11 iterations (more than max_turns=10)
        stream_chunks: list[list[dict[str, Any]]] = [
            [{"type": "tool_start", "content": None, "tool_call": tool_call}]
        ] * 11

        mock_llm = MockStreamingLLMClient(stream_chunks)
        tool = SkillDelegateTool(mock_llm, registry)  # type: ignore[arg-type]

        with patch("core.tools.skill_delegate.load_command", return_value=simple_skill_metadata):
            chunks = []
            async for chunk in tool.run(skill="researcher", goal="Keep searching"):
                chunks.append(chunk)

        # Should have result indicating timeout
        result_chunks = [c for c in chunks if c["type"] == "result"]
        assert len(result_chunks) >= 1
        assert any("timed out" in c.get("output", "").lower() for c in result_chunks)

    @pytest.mark.asyncio
    async def test_skill_load_error(self, mock_registry: tuple[ToolRegistry, AsyncMock]) -> None:
        """Test error handling when skill loading fails."""
        registry, _ = mock_registry
        mock_llm = MockStreamingLLMClient([])
        tool = SkillDelegateTool(mock_llm, registry)  # type: ignore[arg-type]

        with patch(
            "core.tools.skill_delegate.load_command",
            side_effect=Exception("Failed to parse skill"),
        ):
            chunks = []
            async for chunk in tool.run(skill="broken_skill", goal="Do something"):
                chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0]["type"] == "result"
        assert "error" in chunks[0]["output"].lower()
        assert "broken_skill" in chunks[0]["output"]
