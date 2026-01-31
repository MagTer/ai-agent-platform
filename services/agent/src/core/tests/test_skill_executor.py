"""Tests for the SkillExecutor."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from shared.models import AgentRequest, PlanStep

from core.skills.executor import SkillExecutor
from core.skills.registry import SkillRegistry


@pytest.fixture
def skill_registry(tmp_path: Path) -> SkillRegistry:
    """Create a test skill registry with a simple skill."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    skill_file = skills_dir / "test_skill.md"
    skill_file.write_text(
        """---
name: test_skill
description: A test skill for unit testing
tools:
  - web_search
model: agentchat
max_turns: 3
---
You are a test skill. Answer questions briefly.
"""
    )
    return SkillRegistry(skills_dir=skills_dir)


@pytest.fixture
def mock_tool_registry() -> MagicMock:
    """Create a mock tool registry."""
    registry = MagicMock()

    # Create a mock tool
    mock_tool = MagicMock()
    mock_tool.name = "web_search"
    mock_tool.description = "Search the web"
    mock_tool.parameters = {"type": "object", "properties": {"query": {"type": "string"}}}
    mock_tool.run = AsyncMock(return_value="Search results for: test query")

    registry.get.return_value = mock_tool
    return registry


@pytest.fixture
def mock_litellm() -> MagicMock:
    """Create a mock LiteLLM client."""
    from collections.abc import AsyncGenerator
    from typing import Any

    litellm = MagicMock()

    # Make stream_chat return an async generator
    async def mock_stream(*args: Any, **kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
        # Simulate LLM response with no tool calls (direct answer)
        yield {"type": "content", "content": "This is a test response."}

    litellm.stream_chat = mock_stream
    return litellm


class TestSkillExecutor:
    """Tests for SkillExecutor."""

    @pytest.mark.asyncio
    async def test_executor_skill_not_found(
        self,
        mock_tool_registry: MagicMock,
        mock_litellm: MagicMock,
    ) -> None:
        """Test executor returns error for missing skill."""
        # Create empty registry
        with tempfile.TemporaryDirectory() as tmpdir:
            empty_registry = SkillRegistry(skills_dir=Path(tmpdir))
            executor = SkillExecutor(
                skill_registry=empty_registry,
                tool_registry=mock_tool_registry,
                litellm=mock_litellm,
            )

            step = PlanStep(
                id="1",
                label="Test",
                executor="skill",
                action="skill",
                tool="nonexistent_skill",
                args={"goal": "test"},
            )
            request = AgentRequest(prompt="test")

            events = []
            async for event in executor.execute_stream(step, request):
                events.append(event)

            # Should get error result
            assert len(events) == 1
            assert events[0]["type"] == "result"
            assert events[0]["result"].status == "error"
            assert "not found" in events[0]["result"].result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_executor_no_tool_specified(
        self,
        skill_registry: SkillRegistry,
        mock_tool_registry: MagicMock,
        mock_litellm: MagicMock,
    ) -> None:
        """Test executor returns error when no skill specified."""
        executor = SkillExecutor(
            skill_registry=skill_registry,
            tool_registry=mock_tool_registry,
            litellm=mock_litellm,
        )

        step = PlanStep(
            id="1",
            label="Test",
            executor="skill",
            action="skill",
            tool=None,  # No skill specified
            args={"goal": "test"},
        )
        request = AgentRequest(prompt="test")

        events = []
        async for event in executor.execute_stream(step, request):
            events.append(event)

        # Should get error result
        assert len(events) == 1
        assert events[0]["type"] == "result"
        assert events[0]["result"].status == "error"

    @pytest.mark.asyncio
    async def test_executor_streams_content(
        self,
        skill_registry: SkillRegistry,
        mock_tool_registry: MagicMock,
        mock_litellm: MagicMock,
    ) -> None:
        """Test executor streams content from LLM."""
        executor = SkillExecutor(
            skill_registry=skill_registry,
            tool_registry=mock_tool_registry,
            litellm=mock_litellm,
        )

        step = PlanStep(
            id="1",
            label="Research",
            executor="skill",
            action="skill",
            tool="test_skill",
            args={"goal": "find information"},
        )
        request = AgentRequest(prompt="test request")

        events = []
        async for event in executor.execute_stream(step, request):
            events.append(event)

        # Should get thinking, content, and result events
        event_types = [e["type"] for e in events]
        assert "thinking" in event_types
        assert "result" in event_types

        # Result should be successful
        result_events = [e for e in events if e["type"] == "result"]
        assert len(result_events) == 1
        assert result_events[0]["result"].status == "ok"

    @pytest.mark.asyncio
    async def test_executor_with_retry_feedback(
        self,
        skill_registry: SkillRegistry,
        mock_tool_registry: MagicMock,
        mock_litellm: MagicMock,
    ) -> None:
        """Test executor includes retry feedback in messages."""
        executor = SkillExecutor(
            skill_registry=skill_registry,
            tool_registry=mock_tool_registry,
            litellm=mock_litellm,
        )

        step = PlanStep(
            id="1",
            label="Research",
            executor="skill",
            action="skill",
            tool="test_skill",
            args={"goal": "find information"},
        )
        request = AgentRequest(prompt="test request")

        events = []
        async for event in executor.execute_stream(
            step, request, retry_feedback="Previous attempt failed due to timeout"
        ):
            events.append(event)

        # Should complete successfully
        result_events = [e for e in events if e["type"] == "result"]
        assert len(result_events) == 1
        assert result_events[0]["result"].status == "ok"


class TestActivityMessage:
    """Tests for activity message building."""

    def test_build_activity_message_with_query(self) -> None:
        """Test activity message for search query."""
        from core.tools.activity_hints import build_activity_message

        msg = build_activity_message(None, "web_search", {"query": "Python 3.12"})
        assert "Searching" in msg
        assert "Python 3.12" in msg

    def test_build_activity_message_with_url(self) -> None:
        """Test activity message for URL fetch."""
        from core.tools.activity_hints import build_activity_message

        msg = build_activity_message(None, "fetch_url", {"url": "https://example.com/page"})
        assert "Fetching" in msg
        assert "example.com" in msg

    def test_build_activity_message_truncates_long_query(self) -> None:
        """Test that long queries are truncated."""
        from core.tools.activity_hints import build_activity_message

        long_query = "a" * 100
        msg = build_activity_message(None, "web_search", {"query": long_query})
        assert "..." in msg
        # "Searching: " (11 chars) + 47 chars + "..." (3 chars) = 61 chars
        assert len(msg) == 61

    def test_build_activity_message_fallback(self) -> None:
        """Test fallback message for unknown args."""
        from core.tools.activity_hints import build_activity_message

        msg = build_activity_message(None, "some_tool", {"custom_arg": "value"})
        assert "Running some_tool" in msg
