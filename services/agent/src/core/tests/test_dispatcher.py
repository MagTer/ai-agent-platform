"""Tests for the Dispatcher routing component."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import Context, Conversation, Session
from core.routing.unified_orchestrator import OrchestrationResult
from core.runtime.litellm_client import LiteLLMClient
from core.runtime.service import AgentService
from core.skills.registry import Skill, SkillRegistry
from orchestrator.dispatcher import Dispatcher
from shared.models import AgentMessage, Plan, PlanStep, RoutingDecision


@pytest.fixture
def mock_litellm() -> MagicMock:
    """Create a mock LiteLLM client."""
    litellm = MagicMock(spec=LiteLLMClient)
    litellm._settings = MagicMock()
    litellm._settings.model_planner = "test-planner-model"
    litellm.generate = AsyncMock()
    return litellm


@pytest.fixture
def mock_skill_registry() -> MagicMock:
    """Create a mock SkillRegistry."""
    registry = MagicMock(spec=SkillRegistry)
    registry.get.return_value = None
    return registry


@pytest.fixture
def mock_agent_service() -> MagicMock:
    """Create a mock AgentService."""
    service = MagicMock(spec=AgentService)
    service.get_history = AsyncMock(return_value=[])

    # Mock execute_stream to yield a simple content chunk
    async def mock_execute_stream(*args, **kwargs):  # type: ignore[no-untyped-def]
        yield {"type": "content", "content": "Test response"}

    service.execute_stream = mock_execute_stream
    return service


@pytest.fixture
def mock_db_session() -> AsyncMock:
    """Create a mock database session."""
    session = AsyncMock(spec=AsyncSession)

    # Default mock result for execute queries
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_result

    return session


@pytest.fixture
def dispatcher(mock_skill_registry: MagicMock, mock_litellm: MagicMock) -> Dispatcher:
    """Create a Dispatcher instance with mocked dependencies."""
    return Dispatcher(skill_registry=mock_skill_registry, litellm=mock_litellm)


class TestConversationResolution:
    """Tests for conversation resolution logic."""

    @pytest.mark.asyncio
    async def test_resolve_conversation_without_db_session(self, dispatcher: Dispatcher) -> None:
        """Test that conversation_id equals session_id when no db_session."""
        result = await dispatcher._resolve_conversation(
            session_id="test-session",
            platform="web",
            platform_id=None,
            db_session=None,
        )

        assert result == "test-session"

    @pytest.mark.asyncio
    async def test_resolve_conversation_without_platform_id(
        self, dispatcher: Dispatcher, mock_db_session: AsyncMock
    ) -> None:
        """Test that conversation_id equals session_id when no platform_id."""
        result = await dispatcher._resolve_conversation(
            session_id="test-session",
            platform="web",
            platform_id=None,
            db_session=mock_db_session,
        )

        assert result == "test-session"

    @pytest.mark.asyncio
    async def test_resolve_conversation_existing_conversation(
        self, dispatcher: Dispatcher, mock_db_session: AsyncMock
    ) -> None:
        """Test resolving an existing conversation by platform_id."""
        existing_conv_id = uuid.uuid4()
        mock_conversation = MagicMock(spec=Conversation)
        mock_conversation.id = existing_conv_id

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_conversation
        mock_db_session.execute.return_value = mock_result

        result = await dispatcher._resolve_conversation(
            session_id="test-session",
            platform="telegram",
            platform_id="user123",
            db_session=mock_db_session,
        )

        assert result == str(existing_conv_id)

    @pytest.mark.asyncio
    async def test_resolve_conversation_creates_new_with_context(
        self, dispatcher: Dispatcher, mock_db_session: AsyncMock
    ) -> None:
        """Test creating a new conversation when platform_id doesn't exist."""
        # First query returns None (no existing conversation)
        # Second query returns a context
        mock_context = MagicMock(spec=Context)
        mock_context.id = uuid.uuid4()
        mock_context.default_cwd = "/home/user"

        # Setup multiple execute calls
        mock_result_conv = MagicMock()
        mock_result_conv.scalar_one_or_none.return_value = None
        mock_result_ctx = MagicMock()
        mock_result_ctx.scalar_one_or_none.return_value = mock_context

        mock_db_session.execute.side_effect = [mock_result_conv, mock_result_ctx]

        result = await dispatcher._resolve_conversation(
            session_id="test-session",
            platform="telegram",
            platform_id="user456",
            db_session=mock_db_session,
        )

        # Should be a valid UUID string
        assert uuid.UUID(result)
        # Should have added a new conversation
        assert mock_db_session.add.called
        assert mock_db_session.flush.called

    @pytest.mark.asyncio
    async def test_resolve_conversation_creates_new_without_context(
        self, dispatcher: Dispatcher, mock_db_session: AsyncMock
    ) -> None:
        """Test creating a new conversation when no default context exists."""
        # Both queries return None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute.return_value = mock_result

        result = await dispatcher._resolve_conversation(
            session_id="test-session",
            platform="web",
            platform_id="newuser",
            db_session=mock_db_session,
        )

        # Should be a valid UUID string
        assert uuid.UUID(result)
        # Should have added a new conversation without context
        assert mock_db_session.add.called


class TestSlashCommandRouting:
    """Tests for slash command routing."""

    @pytest.mark.asyncio
    async def test_slash_command_activates_skill(
        self,
        dispatcher: Dispatcher,
        mock_skill_registry: MagicMock,
        mock_agent_service: MagicMock,
        mock_db_session: AsyncMock,
    ) -> None:
        """Test that /skillname activates the corresponding skill."""
        from pathlib import Path

        mock_skill = Skill(
            name="researcher",
            path=Path("/app/skills/general/researcher.md"),
            description="Web research",
            body_template="Research: $ARGUMENTS",
            tools=["web_search"],
            model="agentchat",
            max_turns=5,
        )
        mock_skill_registry.get.return_value = mock_skill

        chunks = []
        async for chunk in dispatcher.stream_message(
            session_id="test-session",
            message="/researcher AI news",
            platform="web",
            db_session=mock_db_session,
            agent_service=mock_agent_service,
        ):
            chunks.append(chunk)

        # Should yield a "thinking" chunk indicating skill activation
        assert any(
            chunk["type"] == "thinking" and chunk["content"] and "researcher" in chunk["content"]
            for chunk in chunks
        )
        # Should yield the agent response
        assert any(chunk["type"] == "content" for chunk in chunks)

    @pytest.mark.asyncio
    async def test_slash_command_with_quoted_arguments(
        self,
        dispatcher: Dispatcher,
        mock_skill_registry: MagicMock,
        mock_agent_service: MagicMock,
        mock_db_session: AsyncMock,
    ) -> None:
        """Test that shlex handles quoted arguments correctly."""
        from pathlib import Path

        mock_skill = Skill(
            name="researcher",
            path=Path("/app/skills/general/researcher.md"),
            description="Web research",
            body_template="Research: $ARGUMENTS",
            tools=["web_search"],
            model="agentchat",
            max_turns=5,
        )
        mock_skill_registry.get.return_value = mock_skill

        chunks = []
        async for chunk in dispatcher.stream_message(
            session_id="test-session",
            message='/researcher "latest AI news" technology',
            platform="web",
            db_session=mock_db_session,
            agent_service=mock_agent_service,
        ):
            chunks.append(chunk)

        # Should successfully parse and execute
        assert any(chunk["type"] == "thinking" for chunk in chunks)
        mock_skill_registry.get.assert_called_with("researcher")

    @pytest.mark.asyncio
    async def test_slash_command_not_found(
        self,
        dispatcher: Dispatcher,
        mock_skill_registry: MagicMock,
        mock_db_session: AsyncMock,
        mock_agent_service: MagicMock,
    ) -> None:
        """Test that unknown slash command falls through to orchestration."""
        mock_skill_registry.get.return_value = None

        with patch.object(
            dispatcher._unified_orchestrator,
            "process",
            new=AsyncMock(
                return_value=OrchestrationResult(direct_answer="I don't recognize that command.")
            ),
        ):
            chunks = []
            async for chunk in dispatcher.stream_message(
                session_id="test-session",
                message="/unknown test",
                platform="web",
                db_session=mock_db_session,
                agent_service=mock_agent_service,
            ):
                chunks.append(chunk)

            # Should fall through to unified orchestrator and return direct answer
            assert any(chunk["type"] == "thinking" for chunk in chunks)
            assert any(chunk["type"] == "content" for chunk in chunks)

    @pytest.mark.asyncio
    async def test_slash_command_parsing_error(
        self, dispatcher: Dispatcher, mock_skill_registry: MagicMock, mock_db_session: AsyncMock
    ) -> None:
        """Test error handling for malformed slash commands."""
        # Simulate shlex.split raising ValueError
        with patch("shlex.split", side_effect=ValueError("Unmatched quote")):
            chunks = []
            async for chunk in dispatcher.stream_message(
                session_id="test-session",
                message='/researcher "unmatched',
                platform="web",
                db_session=mock_db_session,
            ):
                chunks.append(chunk)

            # Should yield an error chunk
            assert any(chunk["type"] == "error" for chunk in chunks)
            assert any("usage error" in str(chunk.get("content", "")) for chunk in chunks)

    @pytest.mark.asyncio
    async def test_slash_command_execution_exception(
        self,
        dispatcher: Dispatcher,
        mock_skill_registry: MagicMock,
        mock_db_session: AsyncMock,
    ) -> None:
        """Test error handling when skill execution fails unexpectedly."""
        mock_skill_registry.get.side_effect = Exception("Skill registry error")

        chunks = []
        async for chunk in dispatcher.stream_message(
            session_id="test-session",
            message="/researcher test",
            platform="web",
            db_session=mock_db_session,
        ):
            chunks.append(chunk)

        # Should yield an error chunk
        assert any(chunk["type"] == "error" for chunk in chunks)


class TestFastPathRouting:
    """Tests for regex fast path routing."""

    @pytest.mark.asyncio
    async def test_fast_path_match(
        self,
        dispatcher: Dispatcher,
        mock_agent_service: MagicMock,
        mock_db_session: AsyncMock,
    ) -> None:
        """Test that regex patterns trigger fast path routing."""
        mock_path = {
            "description": "Test fast path",
            "tool": "test_tool",
            "args": {"param": "value"},
        }

        with patch("core.runtime.routing.registry.get_match", return_value=(mock_path, None)):
            chunks = []
            async for chunk in dispatcher.stream_message(
                session_id="test-session",
                message="trigger fast path",
                platform="web",
                db_session=mock_db_session,
                agent_service=mock_agent_service,
            ):
                chunks.append(chunk)

            # Should yield a fast path thinking chunk
            assert any(
                chunk["type"] == "thinking" and chunk["content"] and "Fast Path" in chunk["content"]
                for chunk in chunks
            )
            # Should yield content
            assert any(chunk["type"] == "content" for chunk in chunks)

    @pytest.mark.asyncio
    async def test_fast_path_with_arg_mapper(
        self,
        dispatcher: Dispatcher,
        mock_agent_service: MagicMock,
        mock_db_session: AsyncMock,
    ) -> None:
        """Test fast path with argument mapper function."""
        mock_match = MagicMock()

        def arg_mapper(match):  # type: ignore[no-untyped-def]
            return {"extracted": "value"}

        mock_path = {
            "description": "Test with mapper",
            "tool": "test_tool",
            "args": {},
            "arg_mapper": arg_mapper,
        }

        with patch("core.runtime.routing.registry.get_match", return_value=(mock_path, mock_match)):
            chunks = []
            async for chunk in dispatcher.stream_message(
                session_id="test-session",
                message="test message",
                platform="web",
                db_session=mock_db_session,
                agent_service=mock_agent_service,
            ):
                chunks.append(chunk)

            # Should execute successfully
            assert any(chunk["type"] == "thinking" for chunk in chunks)


class TestUnifiedOrchestration:
    """Tests for unified orchestration routing."""

    @pytest.mark.asyncio
    async def test_direct_answer_route(
        self,
        dispatcher: Dispatcher,
        mock_db_session: AsyncMock,
        mock_agent_service: MagicMock,
    ) -> None:
        """Test that direct answers are streamed correctly."""
        with patch.object(
            dispatcher._unified_orchestrator,
            "process",
            new=AsyncMock(return_value=OrchestrationResult(direct_answer="The answer is 42.")),
        ):
            chunks = []
            async for chunk in dispatcher.stream_message(
                session_id="test-session",
                message="What is the answer?",
                platform="web",
                db_session=mock_db_session,
                agent_service=mock_agent_service,
            ):
                chunks.append(chunk)

            # Should yield a direct answer thinking chunk
            assert any(
                chunk["type"] == "thinking"
                and chunk["content"]
                and "Direct answer" in chunk["content"]
                for chunk in chunks
            )
            # Should yield the content
            assert any(
                chunk["type"] == "content"
                and chunk["content"]
                and "The answer is 42" in chunk["content"]
                for chunk in chunks
            )

    @pytest.mark.asyncio
    async def test_direct_answer_persists_to_db(
        self, dispatcher: Dispatcher, mock_db_session: AsyncMock, mock_agent_service: MagicMock
    ) -> None:
        """Test that direct answers are persisted to database."""
        conversation_id = str(uuid.uuid4())
        session_obj = MagicMock(spec=Session)
        session_obj.id = uuid.uuid4()

        # Mock conversation lookup
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = session_obj
        mock_db_session.execute.return_value = mock_result

        with patch.object(
            dispatcher._unified_orchestrator,
            "process",
            new=AsyncMock(return_value=OrchestrationResult(direct_answer="Test answer")),
        ):
            chunks = []
            async for chunk in dispatcher.stream_message(
                session_id=conversation_id,
                message="Test question",
                platform="web",
                db_session=mock_db_session,
                agent_service=mock_agent_service,
            ):
                chunks.append(chunk)

            # Should have added messages
            assert mock_db_session.add.called
            # Should have committed
            assert mock_db_session.commit.called

    @pytest.mark.asyncio
    async def test_direct_answer_db_persist_failure(
        self, dispatcher: Dispatcher, mock_db_session: AsyncMock, mock_agent_service: MagicMock
    ) -> None:
        """Test that DB persist failures don't crash the response."""
        mock_db_session.commit.side_effect = Exception("DB error")

        with patch.object(
            dispatcher._unified_orchestrator,
            "process",
            new=AsyncMock(return_value=OrchestrationResult(direct_answer="Test answer")),
        ):
            chunks = []
            async for chunk in dispatcher.stream_message(
                session_id="test-session",
                message="Test question",
                platform="web",
                db_session=mock_db_session,
                agent_service=mock_agent_service,
            ):
                chunks.append(chunk)

            # Should still yield content despite DB error
            assert any(chunk["type"] == "content" for chunk in chunks)

    @pytest.mark.asyncio
    async def test_plan_route(
        self,
        dispatcher: Dispatcher,
        mock_db_session: AsyncMock,
        mock_agent_service: MagicMock,
    ) -> None:
        """Test that plans are executed correctly."""
        test_plan = Plan(
            description="Test plan",
            steps=[
                PlanStep(
                    id="1",
                    label="Step 1",
                    executor="skill",
                    action="skill",
                    tool="researcher",
                    args={"goal": "test"},
                )
            ],
        )

        with patch.object(
            dispatcher._unified_orchestrator,
            "process",
            new=AsyncMock(return_value=OrchestrationResult(plan=test_plan)),
        ):
            chunks = []
            async for chunk in dispatcher.stream_message(
                session_id="test-session",
                message="Execute a plan",
                platform="web",
                db_session=mock_db_session,
                agent_service=mock_agent_service,
            ):
                chunks.append(chunk)

            # Should yield a plan thinking chunk
            assert any(
                chunk["type"] == "thinking" and chunk["content"] and "Plan:" in chunk["content"]
                for chunk in chunks
            )
            # Should yield agent execution chunks
            assert any(chunk["type"] == "content" for chunk in chunks)

    @pytest.mark.asyncio
    async def test_history_passed_to_orchestrator(
        self, dispatcher: Dispatcher, mock_db_session: AsyncMock, mock_agent_service: MagicMock
    ) -> None:
        """Test that conversation history is passed to unified orchestrator."""
        history = [
            AgentMessage(role="user", content="Previous question"),
            AgentMessage(role="assistant", content="Previous answer"),
        ]

        with patch.object(
            dispatcher._unified_orchestrator,
            "process",
            new=AsyncMock(return_value=OrchestrationResult(direct_answer="Response")),
        ) as mock_process:
            chunks = []
            async for chunk in dispatcher.stream_message(
                session_id="test-session",
                message="Current question",
                platform="web",
                db_session=mock_db_session,
                agent_service=mock_agent_service,
                history=history,
            ):
                chunks.append(chunk)

            # Verify process was called with history
            call_args = mock_process.call_args
            assert call_args[1]["history"] == history

    @pytest.mark.asyncio
    async def test_history_fetched_from_service_when_not_provided(
        self, dispatcher: Dispatcher, mock_db_session: AsyncMock, mock_agent_service: MagicMock
    ) -> None:
        """Test that history is fetched from agent service if not provided."""
        mock_agent_service.get_history.return_value = [
            AgentMessage(role="user", content="Fetched message")
        ]

        with patch.object(
            dispatcher._unified_orchestrator,
            "process",
            new=AsyncMock(return_value=OrchestrationResult(direct_answer="Response")),
        ):
            chunks = []
            async for chunk in dispatcher.stream_message(
                session_id="test-session",
                message="Current question",
                platform="web",
                db_session=mock_db_session,
                agent_service=mock_agent_service,
            ):
                chunks.append(chunk)

            # Should have called get_history
            assert mock_agent_service.get_history.called


class TestAgentExecution:
    """Tests for agent execution streaming."""

    @pytest.mark.asyncio
    async def test_stream_agent_execution_without_service(
        self, dispatcher: Dispatcher, mock_db_session: AsyncMock
    ) -> None:
        """Test that missing agent service yields an error."""
        chunks = []
        async for chunk in dispatcher._stream_agent_execution(
            prompt="test",
            conversation_id="test-conv",
            db_session=mock_db_session,
            agent_service=None,
            metadata={},
        ):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0]["type"] == "error"
        content = chunks[0]["content"]
        assert content is not None
        assert "not available" in content.lower()

    @pytest.mark.asyncio
    async def test_stream_agent_execution_without_db_session(
        self, dispatcher: Dispatcher, mock_agent_service: MagicMock
    ) -> None:
        """Test that missing db_session yields an error."""
        chunks = []
        async for chunk in dispatcher._stream_agent_execution(
            prompt="test",
            conversation_id="test-conv",
            db_session=None,
            agent_service=mock_agent_service,
            metadata={},
        ):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0]["type"] == "error"

    @pytest.mark.asyncio
    async def test_stream_agent_execution_chunk_types(
        self, dispatcher: Dispatcher, mock_db_session: AsyncMock
    ) -> None:
        """Test that different chunk types are normalized correctly."""

        async def mock_execute_stream(  # type: ignore[no-untyped-def]
            *args, **kwargs  # noqa: ANN002, ANN003
        ):
            yield {"type": "plan", "description": "Test plan"}
            yield {"type": "step_start", "content": "Starting step"}
            yield {"type": "tool_start", "tool_call": {"tool": "test"}}
            yield {"type": "tool_output", "content": "Tool output", "tool_call": None}
            yield {"type": "skill_activity", "content": "Skill working"}
            yield {"type": "content", "content": "Final answer"}
            yield {"type": "thinking", "content": "Processing"}
            yield {"type": "history_snapshot", "snapshot": []}
            yield {"type": "trace_info", "trace_id": "abc123"}
            yield {"type": "awaiting_input", "content": "Need input"}
            yield {"type": "unknown_type", "content": "Unknown"}

        mock_service = MagicMock()
        mock_service.execute_stream = mock_execute_stream

        chunks = []
        async for chunk in dispatcher._stream_agent_execution(
            prompt="test",
            conversation_id="test-conv",
            db_session=mock_db_session,
            agent_service=mock_service,
            metadata={},
        ):
            chunks.append(chunk)

        # Verify all chunk types are handled
        chunk_types = [c["type"] for c in chunks]
        assert "thinking" in chunk_types  # plan converted to thinking
        assert "step_start" in chunk_types
        assert "tool_start" in chunk_types
        assert "tool_output" in chunk_types
        assert "skill_activity" in chunk_types
        assert "content" in chunk_types
        assert "history_snapshot" in chunk_types
        assert "trace_info" in chunk_types
        assert "awaiting_input" in chunk_types

    @pytest.mark.asyncio
    async def test_stream_agent_execution_error_handling(
        self, dispatcher: Dispatcher, mock_db_session: AsyncMock
    ) -> None:
        """Test that agent execution errors are caught and yielded."""

        async def mock_execute_stream(  # type: ignore[no-untyped-def]
            *args, **kwargs  # noqa: ANN002, ANN003
        ):
            yield {"type": "content", "content": "Start"}
            raise RuntimeError("Agent execution failed")

        mock_service = MagicMock()
        mock_service.execute_stream = mock_execute_stream

        chunks = []
        async for chunk in dispatcher._stream_agent_execution(
            prompt="test",
            conversation_id="test-conv",
            db_session=mock_db_session,
            agent_service=mock_service,
            metadata={},
        ):
            chunks.append(chunk)

        # Should have content chunk + error chunk
        assert len(chunks) == 2
        assert chunks[0]["type"] == "content"
        assert chunks[1]["type"] == "error"
        error_content = chunks[1]["content"]
        assert error_content and "Agent execution failed" in error_content


class TestMetadataHandling:
    """Tests for metadata merging and propagation."""

    @pytest.mark.asyncio
    async def test_slash_command_merges_metadata(
        self,
        dispatcher: Dispatcher,
        mock_skill_registry: MagicMock,
        mock_agent_service: MagicMock,
        mock_db_session: AsyncMock,
    ) -> None:
        """Test that metadata is merged when executing slash commands."""
        from pathlib import Path

        mock_skill = Skill(
            name="researcher",
            path=Path("/app/skills/general/researcher.md"),
            description="Web research",
            body_template="Research: $ARGUMENTS",
            tools=["web_search"],
            model="agentchat",
            max_turns=5,
        )
        mock_skill_registry.get.return_value = mock_skill

        input_metadata = {"custom_field": "custom_value"}

        # Capture the AgentRequest passed to execute_stream
        captured_requests = []

        async def capture_execute_stream(request, **kwargs):  # type: ignore[no-untyped-def]
            captured_requests.append(request)
            yield {"type": "content", "content": "Response"}

        mock_agent_service.execute_stream = capture_execute_stream

        chunks = []
        async for chunk in dispatcher.stream_message(
            session_id="test-session",
            message="/researcher test",
            platform="web",
            db_session=mock_db_session,
            agent_service=mock_agent_service,
            metadata=input_metadata,
        ):
            chunks.append(chunk)

        # Verify metadata was merged
        assert len(captured_requests) == 1
        request_metadata = captured_requests[0].metadata
        assert request_metadata["skill"] == "researcher"
        assert request_metadata["tools"] == ["web_search"]
        assert request_metadata["custom_field"] == "custom_value"

    @pytest.mark.asyncio
    async def test_plan_route_merges_metadata(
        self,
        dispatcher: Dispatcher,
        mock_agent_service: MagicMock,
        mock_db_session: AsyncMock,
    ) -> None:
        """Test that metadata is merged when executing plans."""
        test_plan = Plan(
            description="Test plan",
            steps=[
                PlanStep(
                    id="1",
                    label="Step 1",
                    executor="skill",
                    action="skill",
                    tool="researcher",
                    args={},
                )
            ],
        )

        input_metadata = {"trace_id": "abc123"}

        captured_requests = []

        async def capture_execute_stream(request, **kwargs):  # type: ignore[no-untyped-def]
            captured_requests.append(request)
            yield {"type": "content", "content": "Response"}

        mock_agent_service.execute_stream = capture_execute_stream

        with patch.object(
            dispatcher._unified_orchestrator,
            "process",
            new=AsyncMock(return_value=OrchestrationResult(plan=test_plan)),
        ):
            chunks = []
            async for chunk in dispatcher.stream_message(
                session_id="test-session",
                message="Execute plan",
                platform="web",
                db_session=mock_db_session,
                agent_service=mock_agent_service,
                metadata=input_metadata,
            ):
                chunks.append(chunk)

            # Verify metadata was merged
            assert len(captured_requests) == 1
            request_metadata = captured_requests[0].metadata
            assert request_metadata["routing_decision"] == RoutingDecision.AGENTIC
            assert request_metadata["plan"] is not None
            assert request_metadata["trace_id"] == "abc123"


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_empty_message(
        self,
        dispatcher: Dispatcher,
        mock_db_session: AsyncMock,
        mock_agent_service: MagicMock,
    ) -> None:
        """Test handling of empty message."""
        with patch.object(
            dispatcher._unified_orchestrator,
            "process",
            new=AsyncMock(
                return_value=OrchestrationResult(direct_answer="Please provide a message.")
            ),
        ):
            chunks = []
            async for chunk in dispatcher.stream_message(
                session_id="test-session",
                message="",
                platform="web",
                db_session=mock_db_session,
                agent_service=mock_agent_service,
            ):
                chunks.append(chunk)

            # Should handle gracefully
            assert any(chunk["type"] in ("thinking", "content") for chunk in chunks)

    @pytest.mark.asyncio
    async def test_whitespace_only_message(
        self,
        dispatcher: Dispatcher,
        mock_db_session: AsyncMock,
        mock_agent_service: MagicMock,
    ) -> None:
        """Test handling of whitespace-only message."""
        with patch.object(
            dispatcher._unified_orchestrator,
            "process",
            new=AsyncMock(
                return_value=OrchestrationResult(direct_answer="Please provide a message.")
            ),
        ):
            chunks = []
            async for chunk in dispatcher.stream_message(
                session_id="test-session",
                message="   \n\n   ",
                platform="web",
                db_session=mock_db_session,
                agent_service=mock_agent_service,
            ):
                chunks.append(chunk)

            # Should strip and process
            assert len(chunks) > 0

    @pytest.mark.asyncio
    async def test_slash_only_message(
        self,
        dispatcher: Dispatcher,
        mock_db_session: AsyncMock,
        mock_agent_service: MagicMock,
    ) -> None:
        """Test handling of bare slash without command."""
        with patch.object(
            dispatcher._unified_orchestrator,
            "process",
            new=AsyncMock(return_value=OrchestrationResult(direct_answer="Invalid command.")),
        ):
            chunks = []
            async for chunk in dispatcher.stream_message(
                session_id="test-session",
                message="/",
                platform="web",
                db_session=mock_db_session,
                agent_service=mock_agent_service,
            ):
                chunks.append(chunk)

            # Should handle gracefully
            assert len(chunks) > 0

    @pytest.mark.asyncio
    async def test_invalid_conversation_id_in_direct_answer_persist(
        self, dispatcher: Dispatcher, mock_db_session: AsyncMock, mock_agent_service: MagicMock
    ) -> None:
        """Test that invalid UUID conversation_id doesn't crash persistence."""
        with patch.object(
            dispatcher._unified_orchestrator,
            "process",
            new=AsyncMock(return_value=OrchestrationResult(direct_answer="Test answer")),
        ):
            chunks = []
            async for chunk in dispatcher.stream_message(
                session_id="not-a-uuid",
                message="Test question",
                platform="web",
                db_session=mock_db_session,
                agent_service=mock_agent_service,
            ):
                chunks.append(chunk)

            # Should still yield content without crashing
            assert any(chunk["type"] == "content" for chunk in chunks)
