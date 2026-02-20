"""Tests for SkillExecutor context ownership validation and tool scoping.

This module tests security-critical paths in the SkillExecutor:
1. Context ownership validation (_validate_context_ownership)
2. Caching of validated contexts
3. Tool scoping enforcement (skills can only access declared tools)
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from shared.models import AgentRequest, PlanStep

from core.db.models import UserContext
from core.skills.executor import SkillExecutor

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path
    from typing import Any

    from core.skills.registry import SkillRegistry


@pytest.fixture
def user_id() -> UUID:
    """Generate a test user ID."""
    return uuid4()


@pytest.fixture
def context_id() -> UUID:
    """Generate a test context ID."""
    return uuid4()


@pytest.fixture
def other_user_id() -> UUID:
    """Generate a different user ID for negative tests."""
    return uuid4()


@pytest.fixture
def other_context_id() -> UUID:
    """Generate a different context ID."""
    return uuid4()


@pytest.fixture
def mock_session() -> AsyncMock:
    """Create a mock database session."""
    session = AsyncMock()
    return session


@pytest.fixture
def mock_skill_registry(tmp_path: Path) -> SkillRegistry:
    """Create a mock skill registry with test skills."""
    from core.skills.registry import SkillRegistry

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    # Create a skill with specific tools
    skill_file = skills_dir / "test_skill.md"
    skill_file.write_text(
        """---
name: test_skill
description: Test skill with specific tools
tools:
  - allowed_tool
model: agentchat
max_turns: 3
---
Test skill content.
"""
    )

    return SkillRegistry(skills_dir=skills_dir)


@pytest.fixture
def mock_tool_registry() -> MagicMock:
    """Create a mock tool registry with allowed and disallowed tools."""
    registry = MagicMock()

    # Create allowed tool
    allowed_tool = MagicMock()
    allowed_tool.name = "allowed_tool"
    allowed_tool.description = "An allowed tool"
    allowed_tool.parameters = {"type": "object", "properties": {}}
    allowed_tool.run = AsyncMock(return_value="Success")

    # Create disallowed tool
    disallowed_tool = MagicMock()
    disallowed_tool.name = "disallowed_tool"
    disallowed_tool.description = "A disallowed tool"
    disallowed_tool.parameters = {"type": "object", "properties": {}}
    disallowed_tool.run = AsyncMock(return_value="Should not run")

    def get_tool(name: str) -> MagicMock | None:
        if name == "allowed_tool":
            return allowed_tool
        if name == "disallowed_tool":
            return disallowed_tool
        return None

    registry.get.side_effect = get_tool
    return registry


@pytest.fixture
def mock_litellm() -> MagicMock:
    """Create a mock LiteLLM client."""
    litellm = MagicMock()

    async def mock_stream_no_tools(
        *args: Any, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Mock stream that returns content without tool calls."""
        yield {"type": "content", "content": "Test response"}

    litellm.stream_chat = mock_stream_no_tools
    return litellm


@pytest.fixture
def executor(
    mock_skill_registry: MagicMock,
    mock_tool_registry: MagicMock,
    mock_litellm: MagicMock,
) -> SkillExecutor:
    """Create a SkillExecutor instance for testing."""
    return SkillExecutor(
        skill_registry=mock_skill_registry,
        tool_registry=mock_tool_registry,
        litellm=mock_litellm,
    )


class TestContextOwnershipValidation:
    """Tests for _validate_context_ownership method."""

    @pytest.mark.asyncio
    async def test_validate_context_valid_user_allowed(
        self,
        executor: SkillExecutor,
        user_id: UUID,
        context_id: UUID,
        mock_session: AsyncMock,
    ) -> None:
        """Valid context_id with matching user should return True."""
        # Setup: Mock DB to return a valid user_context
        mock_result = MagicMock()
        mock_user_context = UserContext(
            user_id=user_id,
            context_id=context_id,
        )
        mock_result.scalar_one_or_none.return_value = mock_user_context

        # Make execute async
        async def mock_execute(*args: Any, **kwargs: Any) -> MagicMock:
            return mock_result

        mock_session.execute = mock_execute

        # Act
        is_valid = await executor._validate_context_ownership(context_id, user_id, mock_session)

        # Assert
        assert is_valid is True

    @pytest.mark.asyncio
    async def test_validate_context_wrong_user_denied(
        self,
        executor: SkillExecutor,
        user_id: UUID,
        other_user_id: UUID,
        context_id: UUID,
        mock_session: AsyncMock,
    ) -> None:
        """Valid context_id with non-matching user should return False."""
        # Setup: Mock DB to return None (no matching user_context)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        # Make execute async
        async def mock_execute(*args: Any, **kwargs: Any) -> MagicMock:
            return mock_result

        mock_session.execute = mock_execute

        # Act
        is_valid = await executor._validate_context_ownership(
            context_id, other_user_id, mock_session
        )

        # Assert
        assert is_valid is False

    @pytest.mark.asyncio
    async def test_validate_context_caching(
        self,
        executor: SkillExecutor,
        user_id: UUID,
        context_id: UUID,
        mock_session: AsyncMock,
    ) -> None:
        """Second call with same context_id should use cache (no DB query)."""
        # Setup: Mock DB to return valid user_context
        mock_result = MagicMock()
        mock_user_context = UserContext(
            user_id=user_id,
            context_id=context_id,
        )
        mock_result.scalar_one_or_none.return_value = mock_user_context

        # Track call count manually
        call_count = 0

        async def mock_execute(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            return mock_result

        mock_session.execute = mock_execute

        # Act: First call (populates cache)
        is_valid_first = await executor._validate_context_ownership(
            context_id, user_id, mock_session
        )

        # Act: Second call (should use cache)
        is_valid_second = await executor._validate_context_ownership(
            context_id, user_id, mock_session
        )

        # Assert
        assert is_valid_first is True
        assert is_valid_second is True
        # DB should only be queried once (cache hit on second call)
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_validate_context_cache_miss_different_context(
        self,
        executor: SkillExecutor,
        user_id: UUID,
        context_id: UUID,
        other_context_id: UUID,
        mock_session: AsyncMock,
    ) -> None:
        """Different context_id should not use cache (cache miss)."""
        # Setup: Mock DB to return valid user_contexts for both
        mock_user_context_1 = UserContext(user_id=user_id, context_id=context_id)
        mock_user_context_2 = UserContext(user_id=user_id, context_id=other_context_id)

        # Track call count manually
        call_count = 0

        async def mock_execute(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return self._mock_result(mock_user_context_1)
            else:
                return self._mock_result(mock_user_context_2)

        mock_session.execute = mock_execute

        # Act
        is_valid_first = await executor._validate_context_ownership(
            context_id, user_id, mock_session
        )
        is_valid_second = await executor._validate_context_ownership(
            other_context_id, user_id, mock_session
        )

        # Assert
        assert is_valid_first is True
        assert is_valid_second is True
        # Both should query DB (different contexts)
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_validate_context_db_failure(
        self,
        executor: SkillExecutor,
        user_id: UUID,
        context_id: UUID,
        mock_session: AsyncMock,
    ) -> None:
        """Database query raising exception should propagate error."""

        # Setup: Mock DB to raise exception
        async def mock_execute(*args: Any, **kwargs: Any) -> MagicMock:
            raise Exception("Database connection failed")

        mock_session.execute = mock_execute

        # Act & Assert: Should raise the exception
        with pytest.raises(Exception, match="Database connection failed"):
            await executor._validate_context_ownership(context_id, user_id, mock_session)

    @pytest.mark.asyncio
    async def test_validate_context_cache_miss_with_different_user(
        self,
        executor: SkillExecutor,
        user_id: UUID,
        other_user_id: UUID,
        context_id: UUID,
        mock_session: AsyncMock,
    ) -> None:
        """Different user_id results in cache miss (cache key is tuple)."""
        # Setup: Mock DB responses
        # First call: return valid context for user_id
        # Second call: return None for other_user_id (no access)
        mock_result_valid = MagicMock()
        mock_user_context = UserContext(user_id=user_id, context_id=context_id)
        mock_result_valid.scalar_one_or_none.return_value = mock_user_context

        mock_result_invalid = MagicMock()
        mock_result_invalid.scalar_one_or_none.return_value = None

        # Track call count manually
        call_count = 0

        async def mock_execute(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_result_valid
            else:
                return mock_result_invalid

        mock_session.execute = mock_execute

        # Act: First call with user_id (populates cache for (context_id, user_id))
        is_valid_first = await executor._validate_context_ownership(
            context_id, user_id, mock_session
        )

        # Act: Second call with other_user_id (cache miss - different tuple key)
        is_valid_second = await executor._validate_context_ownership(
            context_id, other_user_id, mock_session
        )

        # Assert
        assert is_valid_first is True
        assert is_valid_second is False
        # DB should be queried twice (cache miss on second call)
        assert call_count == 2

    def _mock_result(self, return_value: Any) -> MagicMock:
        """Helper to create mock DB result."""
        mock = MagicMock()
        mock.scalar_one_or_none.return_value = return_value
        return mock


class TestContextValidationInExecution:
    """Tests for context validation during skill execution."""

    @pytest.mark.asyncio
    async def test_execute_with_valid_context(
        self,
        executor: SkillExecutor,
        user_id: UUID,
        context_id: UUID,
        mock_session: AsyncMock,
        mock_litellm: MagicMock,
    ) -> None:
        """Execution with valid context should proceed normally."""
        # Setup: Mock DB to return valid user_context
        mock_result = MagicMock()
        mock_user_context = UserContext(user_id=user_id, context_id=context_id)
        mock_result.scalar_one_or_none.return_value = mock_user_context

        # Make execute async
        async def mock_execute(*args: Any, **kwargs: Any) -> MagicMock:
            return mock_result

        mock_session.execute = mock_execute

        # Build request with context metadata
        step = PlanStep(
            id="1",
            label="Test",
            executor="skill",
            action="skill",
            tool="test_skill",
            args={"goal": "test"},
        )
        request = AgentRequest(
            prompt="test",
            metadata={
                "context_id": str(context_id),
                "user_id": str(user_id),
                "_db_session": mock_session,
            },
        )

        # Act
        events = []
        async for event in executor.execute_stream(step, request):
            events.append(event)

        # Assert: Should execute successfully
        result_events = [e for e in events if e["type"] == "result"]
        assert len(result_events) == 1
        assert result_events[0]["result"].status == "ok"

    @pytest.mark.asyncio
    async def test_execute_with_invalid_context(
        self,
        executor: SkillExecutor,
        user_id: UUID,
        other_user_id: UUID,
        context_id: UUID,
        mock_session: AsyncMock,
    ) -> None:
        """Execution with invalid context should return error."""
        # Setup: Mock DB to return None (no matching user_context)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        # Make execute async
        async def mock_execute(*args: Any, **kwargs: Any) -> MagicMock:
            return mock_result

        mock_session.execute = mock_execute

        # Build request with mismatched user_id
        step = PlanStep(
            id="1",
            label="Test",
            executor="skill",
            action="skill",
            tool="test_skill",
            args={"goal": "test"},
        )
        request = AgentRequest(
            prompt="test",
            metadata={
                "context_id": str(context_id),
                "user_id": str(other_user_id),  # Wrong user
                "_db_session": mock_session,
            },
        )

        # Act
        events = []
        async for event in executor.execute_stream(step, request):
            events.append(event)

        # Assert: Should get error result
        assert len(events) == 1
        assert events[0]["type"] == "result"
        assert events[0]["result"].status == "error"
        assert "Access denied" in events[0]["result"].result.get("error", "")
        assert "ownership validation failed" in events[0]["result"].result.get("error", "")

    @pytest.mark.asyncio
    async def test_execute_without_context_metadata(
        self,
        executor: SkillExecutor,
        mock_litellm: MagicMock,
    ) -> None:
        """Execution without context metadata should skip validation."""
        # Build request without context metadata
        step = PlanStep(
            id="1",
            label="Test",
            executor="skill",
            action="skill",
            tool="test_skill",
            args={"goal": "test"},
        )
        request = AgentRequest(prompt="test", metadata=None)

        # Act
        events = []
        async for event in executor.execute_stream(step, request):
            events.append(event)

        # Assert: Should execute successfully (no validation performed)
        result_events = [e for e in events if e["type"] == "result"]
        assert len(result_events) == 1
        assert result_events[0]["result"].status == "ok"

    @pytest.mark.asyncio
    async def test_execute_with_partial_context_metadata(
        self,
        executor: SkillExecutor,
        context_id: UUID,
        mock_litellm: MagicMock,
    ) -> None:
        """Execution with partial context metadata should skip validation."""
        # Build request with only context_id (missing user_id)
        step = PlanStep(
            id="1",
            label="Test",
            executor="skill",
            action="skill",
            tool="test_skill",
            args={"goal": "test"},
        )
        request = AgentRequest(
            prompt="test",
            metadata={
                "context_id": str(context_id),
                # Missing user_id and _db_session
            },
        )

        # Act
        events = []
        async for event in executor.execute_stream(step, request):
            events.append(event)

        # Assert: Should execute successfully (validation skipped)
        result_events = [e for e in events if e["type"] == "result"]
        assert len(result_events) == 1
        assert result_events[0]["result"].status == "ok"


class TestToolScoping:
    """Tests for tool scoping enforcement."""

    @pytest.mark.asyncio
    async def test_skill_can_access_allowed_tool(
        self,
        executor: SkillExecutor,
        mock_tool_registry: MagicMock,
    ) -> None:
        """Skill should be able to call tools listed in its frontmatter."""

        # Setup: Mock LLM to return tool call for allowed tool
        async def mock_stream_with_tool(
            *args: Any, **kwargs: Any
        ) -> AsyncGenerator[dict[str, Any], None]:
            # Yield tool call
            yield {
                "type": "tool_start",
                "tool_call": {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "allowed_tool", "arguments": "{}"},
                    "index": 0,
                },
            }
            # Yield content (final response after tool)
            yield {"type": "content", "content": "Tool executed successfully"}

        executor._litellm.stream_chat = mock_stream_with_tool  # type: ignore[assignment]

        # Build request
        step = PlanStep(
            id="1",
            label="Test",
            executor="skill",
            action="skill",
            tool="test_skill",
            args={"goal": "test"},
        )
        request = AgentRequest(prompt="test")

        # Act
        events = []
        async for event in executor.execute_stream(step, request):
            events.append(event)

        # Assert: Tool should be executed
        allowed_tool = mock_tool_registry.get("allowed_tool")
        allowed_tool.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_skill_cannot_access_disallowed_tool(
        self,
        executor: SkillExecutor,
        mock_tool_registry: MagicMock,
    ) -> None:
        """Skill should be blocked from calling tools not in its frontmatter."""

        # Setup: Mock LLM to return tool call for disallowed tool
        async def mock_stream_with_disallowed_tool(
            *args: Any, **kwargs: Any
        ) -> AsyncGenerator[dict[str, Any], None]:
            # Yield tool call for disallowed tool
            yield {
                "type": "tool_start",
                "tool_call": {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "disallowed_tool", "arguments": "{}"},
                    "index": 0,
                },
            }
            # Yield content (final response after tool)
            yield {"type": "content", "content": "Attempted to use disallowed tool"}

        executor._litellm.stream_chat = mock_stream_with_disallowed_tool  # type: ignore[assignment]

        # Build request
        step = PlanStep(
            id="1",
            label="Test",
            executor="skill",
            action="skill",
            tool="test_skill",
            args={"goal": "test"},
        )
        request = AgentRequest(prompt="test")

        # Act
        events = []
        async for event in executor.execute_stream(step, request):
            events.append(event)

        # Assert: Tool should NOT be executed
        disallowed_tool = mock_tool_registry.get("disallowed_tool")
        disallowed_tool.run.assert_not_called()

        # Verify execution completed (tool was rejected but skill continued)
        assert len(events) > 0  # Should have completed execution

    @pytest.mark.asyncio
    async def test_tool_scoping_with_missing_tool(
        self,
        executor: SkillExecutor,
        mock_tool_registry: MagicMock,
    ) -> None:
        """Skill referencing a missing tool should skip that tool gracefully."""
        # The skill references "allowed_tool" but we'll make it return None
        original_get = mock_tool_registry.get

        def get_tool_none(name: str) -> None:
            # Return None for the allowed tool (simulate missing tool)
            if name == "allowed_tool":
                return None
            return original_get(name)

        mock_tool_registry.get.side_effect = get_tool_none

        # Build request
        step = PlanStep(
            id="1",
            label="Test",
            executor="skill",
            action="skill",
            tool="test_skill",
            args={"goal": "test"},
        )
        request = AgentRequest(prompt="test")

        # Act - should not raise exception
        events = []
        async for event in executor.execute_stream(step, request):
            events.append(event)

        # Assert: Should complete (skill has no available tools, will just return content)
        result_events = [e for e in events if e["type"] == "result"]
        assert len(result_events) == 1
        # Should succeed even without tools
        assert result_events[0]["result"].status == "ok"
