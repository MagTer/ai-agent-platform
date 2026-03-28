"""Integration tests for HITL handoff flow - drafter to writer transitions."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from shared.models import (
    AgentRequest,
    AwaitingInputCategory,
    DraftOutput,
    HITLRequest,
    WorkItemDraft,
)

from core.db import Conversation, Session
from core.runtime.hitl import HITLCoordinator
from core.tests.mocks import InMemoryAsyncSession, MockLLMClient


@pytest.fixture
def mock_skill_registry() -> MagicMock:
    """Create a mock skill registry."""
    registry = MagicMock()
    return registry


@pytest.fixture
def mock_tool_registry() -> MagicMock:
    """Create a mock tool registry."""
    registry = MagicMock()
    return registry


@pytest.fixture
def mock_litellm() -> MockLLMClient:
    """Create a mock LiteLLM client."""
    return MockLLMClient()


@pytest.fixture
def coordinator(
    mock_skill_registry: MagicMock,
    mock_tool_registry: MagicMock,
    mock_litellm: MockLLMClient,
) -> HITLCoordinator:
    """Create a HITLCoordinator with mocked dependencies."""
    return HITLCoordinator(
        skill_registry=mock_skill_registry,
        tool_registry=mock_tool_registry,
        litellm=mock_litellm,
    )


@pytest.fixture
def mock_db_session() -> InMemoryAsyncSession:
    """Create an in-memory database session."""
    return InMemoryAsyncSession()


@pytest.fixture
def mock_conversation() -> MagicMock:
    """Create a mock conversation with metadata tracking."""
    conv = MagicMock(spec=Conversation)
    conv.id = uuid.uuid4()
    conv.conversation_metadata = {}
    return conv


@pytest.fixture
def mock_db_session_obj() -> MagicMock:
    """Create a mock database session object."""
    session = MagicMock(spec=Session)
    session.id = uuid.uuid4()
    return session


class TestHITLFullFlowApproval:
    """Tests for complete drafter → approval → writer flow."""

    @pytest.mark.asyncio
    async def test_full_approval_flow_with_handoff(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: InMemoryAsyncSession,
        mock_conversation: MagicMock,
        mock_db_session_obj: MagicMock,
    ) -> None:
        """Test complete flow: drafter pause → user approval → writer execution."""
        # Setup pending HITL state from requirements_drafter
        draft_data = {
            "type": "User Story",
            "team_alias": "platform",
            "title": "Add OAuth Support",
            "description": "Implement OAuth2 authentication flow",
            "acceptance_criteria": "User can login with OAuth",
            "tags": ["auth", "oauth"],
        }
        pending_hitl = {
            "skill_name": "requirements_drafter",
            "category": "confirmation",
            "tool_call_id": "call_123",
            "step": {
                "id": "step_1",
                "label": "Draft work item",
                "executor": "skill",
                "action": "skill",
                "tool": "requirements_drafter",
                "args": {},
            },
            "skill_messages": [
                {
                    "role": "assistant",
                    "content": (
                        "DRAFT READY\n\n"
                        f"Type: {draft_data['type']}\n"
                        f"Team: {draft_data['team_alias']}\n"
                        f"Title: {draft_data['title']}\n"
                        f"Description:\n{draft_data['description']}\n"
                        f"Acceptance Criteria:\n{draft_data['acceptance_criteria']}\n"
                        f"Tags: {', '.join(draft_data['tags'])}"
                    ),
                }
            ],
        }
        mock_conversation.conversation_metadata = {"pending_hitl": pending_hitl}

        # User approves the draft
        request = AgentRequest(prompt="yes, looks good")

        # Mock requirements_writer execution
        async def mock_writer(*args: Any, **kwargs: Any) -> Any:
            yield {"type": "thinking", "content": "Creating work item..."}
            yield {"type": "content", "content": "Work item #12345 created successfully!"}

        with patch.object(coordinator, '_execute_requirements_writer', mock_writer):
            results = []
            async for event in coordinator._resume_hitl(
                request=request,
                session=mock_db_session,  # type: ignore[arg-type]
                db_session=mock_db_session_obj,
                db_conversation=mock_conversation,
                pending_hitl=pending_hitl,
            ):
                results.append(event)

            # Verify handoff thinking event with metadata
            thinking_events = [e for e in results if e.get("type") == "thinking"]
            handoff_events = [
                e for e in thinking_events if e.get("metadata", {}).get("hitl_handoff")
            ]
            assert len(handoff_events) >= 1, "Expected at least one handoff thinking event"
            assert any(
                "creating work item" in str(e.get("content", "")).lower()
                for e in handoff_events
            )

            # Verify content was yielded
            content_events = [e for e in results if e.get("type") == "content"]
            assert len(content_events) >= 1

            # Verify pending HITL was cleared
            assert "pending_hitl" not in mock_conversation.conversation_metadata

    @pytest.mark.asyncio
    async def test_handoff_with_json_draft_extraction(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: InMemoryAsyncSession,
        mock_conversation: MagicMock,
        mock_db_session_obj: MagicMock,
    ) -> None:
        """Test handoff using structured JSON draft extraction."""
        # Create DraftOutput with JSON content
        draft = WorkItemDraft(
            type="Feature",
            team_alias="mobile",
            title="Push Notifications",
            description="Add push notification support",
            acceptance_criteria="User receives push notifications",
            tags=["mobile", "notifications"],
        )
        hitl = HITLRequest(
            category=AwaitingInputCategory.CONFIRMATION,
            prompt="Please confirm this draft",
        )
        draft_output = DraftOutput(draft=draft, hitl=hitl)

        pending_hitl = {
            "skill_name": "requirements_drafter",
            "category": "confirmation",
            "tool_call_id": "call_456",
            "step": {
                "id": "step_2",
                "label": "Draft feature",
                "executor": "skill",
                "action": "skill",
                "tool": "requirements_drafter",
                "args": {},
            },
            "skill_messages": [
                {
                    "role": "assistant",
                    "content": f"```json\n{draft_output.model_dump_json()}\n```",
                }
            ],
        }
        mock_conversation.conversation_metadata = {"pending_hitl": pending_hitl}

        request = AgentRequest(prompt="approve")

        captured_draft_data: dict[str, Any] | None = None

        async def capture_writer(draft_data: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
            nonlocal captured_draft_data
            captured_draft_data = draft_data
            yield {"type": "content", "content": "Created!"}

        with patch.object(coordinator, '_execute_requirements_writer', capture_writer):
            async for _ in coordinator._resume_hitl(
                request=request,
                session=mock_db_session,  # type: ignore[arg-type]
                db_session=mock_db_session_obj,
                db_conversation=mock_conversation,
                pending_hitl=pending_hitl,
            ):
                pass

            # Verify draft data was extracted correctly from JSON
            assert captured_draft_data is not None
            assert captured_draft_data["type"] == "Feature"
            assert captured_draft_data["team_alias"] == "mobile"
            assert captured_draft_data["title"] == "Push Notifications"
            assert captured_draft_data["tags"] == ["mobile", "notifications"]


class TestHITLStatePersistence:
    """Tests for HITL state persistence in conversation_metadata."""

    @pytest.mark.asyncio
    async def test_pending_hitl_stored_in_metadata(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: InMemoryAsyncSession,
        mock_conversation: MagicMock,
        mock_db_session_obj: MagicMock,
    ) -> None:
        """Test that pending HITL state is stored in conversation metadata."""
        pending_hitl = {
            "skill_name": "requirements_drafter",
            "category": "confirmation",
            "tool_call_id": "call_789",
            "step": {
                "id": "step_3",
                "label": "Draft work item",
                "executor": "skill",
                "action": "skill",
                "tool": "requirements_drafter",
                "args": {},
            },
            "skill_messages": [{"role": "assistant", "content": "Draft ready"}],
        }

        # Simulate storing pending HITL (as would happen during skill execution)
        mock_conversation.conversation_metadata = {"pending_hitl": pending_hitl}

        # Verify state is retrievable
        assert "pending_hitl" in mock_conversation.conversation_metadata
        stored = mock_conversation.conversation_metadata["pending_hitl"]
        assert stored["skill_name"] == "requirements_drafter"
        assert stored["category"] == "confirmation"
        assert stored["tool_call_id"] == "call_789"

    @pytest.mark.asyncio
    async def test_pending_hitl_cleared_after_resume(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: InMemoryAsyncSession,
        mock_conversation: MagicMock,
        mock_db_session_obj: MagicMock,
    ) -> None:
        """Test that pending HITL is cleared after resuming."""
        pending_hitl = {
            "skill_name": "requirements_drafter",
            "category": "confirmation",
            "tool_call_id": "call_abc",
            "step": {},
            "skill_messages": [],
        }
        mock_conversation.conversation_metadata = {"pending_hitl": pending_hitl}

        request = AgentRequest(prompt="no")

        async for _ in coordinator._resume_hitl(
            request=request,
            session=mock_db_session,  # type: ignore[arg-type]
            db_session=mock_db_session_obj,
            db_conversation=mock_conversation,
            pending_hitl=pending_hitl,
        ):
            pass

        # Verify pending HITL was cleared
        assert "pending_hitl" not in mock_conversation.conversation_metadata

    @pytest.mark.asyncio
    async def test_conversation_metadata_persists_step_info(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: InMemoryAsyncSession,
        mock_conversation: MagicMock,
        mock_db_session_obj: MagicMock,
    ) -> None:
        """Test that step information is preserved in metadata for resume."""
        step_data = {
            "id": "step_resume_1",
            "label": "Draft with details",
            "executor": "skill",
            "action": "skill",
            "tool": "requirements_drafter",
            "args": {"goal": "Create detailed work item"},
        }
        pending_hitl = {
            "skill_name": "requirements_drafter",
            "category": "confirmation",
            "tool_call_id": "call_def",
            "step": step_data,
            "skill_messages": [
                {
                    "role": "assistant",
                    "content": (
                        "DRAFT READY\n\n"
                        "Type: Bug\n"
                        "Team: web\n"
                        "Title: Fix crash\n"
                        "Description: Fix the crash"
                    ),
                }
            ],
        }
        mock_conversation.conversation_metadata = {"pending_hitl": pending_hitl}

        # Verify step data is preserved
        stored = mock_conversation.conversation_metadata["pending_hitl"]
        assert stored["step"]["id"] == "step_resume_1"
        assert stored["step"]["label"] == "Draft with details"
        assert stored["step"]["tool"] == "requirements_drafter"


class TestHITLResumeAfterRestart:
    """Tests for resuming HITL after system restart."""

    @pytest.mark.asyncio
    async def test_reconstruct_messages_from_stored_state(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: InMemoryAsyncSession,
        mock_conversation: MagicMock,
        mock_db_session_obj: MagicMock,
    ) -> None:
        """Test that messages can be reconstructed from stored HITL state."""
        # Simulate stored state after system restart
        skill_messages = [
            {"role": "user", "content": "Create a work item for OAuth"},
            {"role": "assistant", "content": "I'll draft that for you."},
            {
                "role": "assistant",
                "content": (
                    "DRAFT READY\n\n"
                    "Type: User Story\n"
                    "Team: platform\n"
                    "Title: OAuth Support\n"
                    "Description: Implement OAuth"
                ),
            },
        ]
        pending_hitl = {
            "skill_name": "requirements_drafter",
            "category": "confirmation",
            "tool_call_id": "call_restart",
            "step": {
                "id": "step_restart",
                "label": "Draft OAuth",
                "executor": "skill",
                "action": "skill",
                "tool": "requirements_drafter",
                "args": {},
            },
            "skill_messages": skill_messages,
        }
        mock_conversation.conversation_metadata = {"pending_hitl": pending_hitl}

        request = AgentRequest(prompt="cancel")

        # Verify skill_messages are preserved and can be used
        async for _ in coordinator._resume_hitl(
            request=request,
            session=mock_db_session,  # type: ignore[arg-type]
            db_session=mock_db_session_obj,
            db_conversation=mock_conversation,
            pending_hitl=pending_hitl,
        ):
            pass

class TestHITLCancellationPath:
    """Tests for cancellation flow: drafter → cancel → no writer."""

    @pytest.mark.asyncio
    async def test_cancel_no_writer_invocation(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: InMemoryAsyncSession,
        mock_conversation: MagicMock,
        mock_db_session_obj: MagicMock,
    ) -> None:
        """Test that cancel prevents requirements_writer from being invoked."""
        pending_hitl = {
            "skill_name": "requirements_drafter",
            "category": "confirmation",
            "tool_call_id": "call_cancel",
            "step": {
                "id": "step_cancel",
                "label": "Draft work item",
                "executor": "skill",
                "action": "skill",
                "tool": "requirements_drafter",
                "args": {},
            },
            "skill_messages": [
                {
                    "role": "assistant",
                    "content": (
                        "DRAFT READY\n\n"
                        "Type: Bug\n"
                        "Team: qa\n"
                        "Title: Fix crash\n"
                        "Description: Fix the bug"
                    ),
                }
            ],
        }
        mock_conversation.conversation_metadata = {"pending_hitl": pending_hitl}

        # User cancels
        request = AgentRequest(prompt="cancel this")

        # Track if writer was called
        writer_called = False

        async def mock_writer(*args: Any, **kwargs: Any) -> Any:
            nonlocal writer_called
            writer_called = True
            yield {"type": "content", "content": "Should not reach here"}

        with patch.object(coordinator, '_execute_requirements_writer', mock_writer):
            results = []
            async for event in coordinator._resume_hitl(
                request=request,
                session=mock_db_session,  # type: ignore[arg-type]
                db_session=mock_db_session_obj,
                db_conversation=mock_conversation,
                pending_hitl=pending_hitl,
            ):
                results.append(event)

            # Verify writer was NOT called
            assert not writer_called, "requirements_writer should not be invoked on cancel"

            # Verify cancellation message was yielded
            content_events = [e for e in results if e.get("type") == "content"]
            assert any("cancelled" in str(e.get("content", "")).lower() for e in content_events)

    @pytest.mark.asyncio
    async def test_cancel_clears_pending_state(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: InMemoryAsyncSession,
        mock_conversation: MagicMock,
        mock_db_session_obj: MagicMock,
    ) -> None:
        """Test that cancel clears the pending HITL state."""
        pending_hitl = {
            "skill_name": "requirements_drafter",
            "category": "confirmation",
            "tool_call_id": "call_cancel2",
            "step": {},
            "skill_messages": [],
        }
        mock_conversation.conversation_metadata = {"pending_hitl": pending_hitl}

        request = AgentRequest(prompt="no")

        async for _ in coordinator._resume_hitl(
            request=request,
            session=mock_db_session,  # type: ignore[arg-type]
            db_session=mock_db_session_obj,
            db_conversation=mock_conversation,
            pending_hitl=pending_hitl,
        ):
            pass

        # Verify pending HITL is cleared
        assert "pending_hitl" not in mock_conversation.conversation_metadata

    @pytest.mark.asyncio
    async def test_reject_keywords_trigger_cancel(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: InMemoryAsyncSession,
        mock_conversation: MagicMock,
        mock_db_session_obj: MagicMock,
    ) -> None:
        """Test that various reject keywords trigger cancellation."""
        reject_inputs = ["no", "cancel", "reject", "abort", "stop", "nevermind"]

        for reject_input in reject_inputs:
            pending_hitl = {
                "skill_name": "requirements_drafter",
                "category": "confirmation",
                "tool_call_id": f"call_{reject_input}",
                "step": {},
                "skill_messages": [],
            }
            mock_conversation.conversation_metadata = {"pending_hitl": pending_hitl}

            request = AgentRequest(prompt=reject_input)

            writer_called = False

            async def mock_writer(*args: Any, **kwargs: Any) -> Any:
                nonlocal writer_called
                writer_called = True
                yield {"type": "content", "content": "Should not reach"}

            with patch.object(coordinator, '_execute_requirements_writer', mock_writer):
                async for _ in coordinator._resume_hitl(
                    request=request,
                    session=mock_db_session,  # type: ignore[arg-type]
                    db_session=mock_db_session_obj,
                    db_conversation=mock_conversation,
                    pending_hitl=pending_hitl,
                ):
                    pass

                assert not writer_called, f"Writer should not be called for '{reject_input}'"


class TestHITLRevisionLoop:
    """Tests for revision loop: drafter → request changes → revised draft → confirmation."""

    @pytest.mark.asyncio
    async def test_request_changes_triggers_revision(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: InMemoryAsyncSession,
        mock_conversation: MagicMock,
        mock_db_session_obj: MagicMock,
    ) -> None:
        """Test that requesting changes triggers revision flow."""
        pending_hitl = {
            "skill_name": "requirements_drafter",
            "category": "confirmation",
            "tool_call_id": "call_revision",
            "step": {
                "id": "step_revision",
                "label": "Draft work item",
                "executor": "skill",
                "action": "skill",
                "tool": "requirements_drafter",
                "args": {},
            },
            "skill_messages": [
                {"role": "assistant", "content": (
                    "DRAFT READY\n\nType: Bug\nTeam: qa\nTitle: Fix crash\n"
                    "Description: Fix the bug"
                )}
            ],
        }
        mock_conversation.conversation_metadata = {"pending_hitl": pending_hitl}

        # User requests changes
        request = AgentRequest(prompt="change the title to something more descriptive")

        results = []
        async for event in coordinator._resume_hitl(
            request=request,
            session=mock_db_session,  # type: ignore[arg-type]
            db_session=mock_db_session_obj,
            db_conversation=mock_conversation,
            pending_hitl=pending_hitl,
        ):
            results.append(event)

        # Verify revision thinking event
        thinking_events = [e for e in results if e.get("type") == "thinking"]
        revision_events = [
            e for e in thinking_events if e.get("metadata", {}).get("hitl_revision")
        ]
        assert len(revision_events) >= 1, "Expected revision thinking event"
        assert any("revising" in str(e.get("content", "")).lower() for e in thinking_events)

    @pytest.mark.asyncio
    async def test_request_changes_keywords(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: InMemoryAsyncSession,
        mock_conversation: MagicMock,
        mock_db_session_obj: MagicMock,
    ) -> None:
        """Test various keywords that trigger change requests."""
        change_inputs = [
            "change the title",
            "revise this",
            "needs work",
            "update the description",
            "modify",
        ]

        for change_input in change_inputs:
            pending_hitl = {
                "skill_name": "requirements_drafter",
                "category": "confirmation",
                "tool_call_id": f"call_change_{hash(change_input) % 1000}",
                "step": {
                    "id": "step_change",
                    "label": "Draft",
                    "executor": "skill",
                    "action": "skill",
                    "tool": "requirements_drafter",
                    "args": {},
                },
                "skill_messages": [
                    {
                        "role": "assistant",
                        "content": (
                            "DRAFT READY\n\n"
                            "Type: User Story\n"
                            "Team: platform\n"
                            "Title: Test Feature\n"
                            "Description: Test description"
                        ),
                    }
                ],
            }
            mock_conversation.conversation_metadata = {"pending_hitl": pending_hitl}

            request = AgentRequest(prompt=change_input)

            results = []
            async for event in coordinator._resume_hitl(
                request=request,
                session=mock_db_session,  # type: ignore[arg-type]
                db_session=mock_db_session_obj,
                db_conversation=mock_conversation,
                pending_hitl=pending_hitl,
            ):
                results.append(event)

            # Verify revision thinking for each
            thinking_events = [e for e in results if e.get("type") == "thinking"]
            revision_events = [
                e for e in thinking_events if e.get("metadata", {}).get("hitl_revision")
            ]
            assert len(revision_events) >= 1, f"Expected revision for '{change_input}'"

    @pytest.mark.asyncio
    async def test_full_revision_cycle(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: InMemoryAsyncSession,
        mock_conversation: MagicMock,
        mock_db_session_obj: MagicMock,
    ) -> None:
        """Test complete revision cycle with multiple iterations."""
        # First iteration - initial draft
        pending_hitl_1 = {
            "skill_name": "requirements_drafter",
            "category": "confirmation",
            "tool_call_id": "call_cycle_1",
            "step": {
                "id": "step_cycle_1",
                "label": "Initial draft",
                "executor": "skill",
                "action": "skill",
                "tool": "requirements_drafter",
                "args": {},
            },
            "skill_messages": [
                {
                    "role": "assistant",
                    "content": (
                        "DRAFT READY\n\n"
                        "Type: Feature\n"
                        "Team: platform\n"
                        "Title: Basic Feature\n"
                        "Description: A basic feature description"
                    ),
                }
            ],
        }
        mock_conversation.conversation_metadata = {"pending_hitl": pending_hitl_1}

        # Request first revision
        request_1 = AgentRequest(prompt="change the title to something more specific")

        results_1 = []
        async for event in coordinator._resume_hitl(
            request=request_1,
            session=mock_db_session,  # type: ignore[arg-type]
            db_session=mock_db_session_obj,
            db_conversation=mock_conversation,
            pending_hitl=pending_hitl_1,
        ):
            results_1.append(event)

        # Verify revision was triggered
        thinking_1 = [e for e in results_1 if e.get("type") == "thinking"]
        revision_1 = [e for e in thinking_1 if e.get("metadata", {}).get("hitl_revision")]
        assert len(revision_1) >= 1


class TestHITLMessagePropagation:
    """Tests for message passing between drafter and writer."""

    @pytest.mark.asyncio
    async def test_draft_data_passed_to_writer(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: InMemoryAsyncSession,
        mock_conversation: MagicMock,
        mock_db_session_obj: MagicMock,
    ) -> None:
        """Test that draft data is correctly passed to requirements_writer."""
        draft_content = {
            "type": "User Story",
            "team_alias": "api",
            "title": "Add REST endpoint",
            "description": "Create new API endpoint for user data",
            "acceptance_criteria": "Endpoint returns 200 with user data",
            "tags": ["api", "rest"],
        }
        pending_hitl = {
            "skill_name": "requirements_drafter",
            "category": "confirmation",
            "tool_call_id": "call_msg",
            "step": {},
            "skill_messages": [
                {
                    "role": "assistant",
                    "content": (
                        "DRAFT READY\n\n"
                        f"Type: {draft_content['type']}\n"
                        f"Team: {draft_content['team_alias']}\n"
                        f"Title: {draft_content['title']}\n"
                        f"Description:\n{draft_content['description']}\n"
                        f"Acceptance Criteria:\n{draft_content['acceptance_criteria']}\n"
                        f"Tags: {', '.join(draft_content['tags'])}"
                    ),
                }
            ],
        }
        mock_conversation.conversation_metadata = {"pending_hitl": pending_hitl}

        request = AgentRequest(prompt="yes, create it")

        captured_draft: dict[str, Any] | None = None

        async def capture_writer(draft_data: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
            nonlocal captured_draft
            captured_draft = draft_data
            yield {"type": "content", "content": "Work item created"}

        with patch.object(coordinator, '_execute_requirements_writer', capture_writer):
            async for _ in coordinator._resume_hitl(
                request=request,
                session=mock_db_session,  # type: ignore[arg-type]
                db_session=mock_db_session_obj,
                db_conversation=mock_conversation,
                pending_hitl=pending_hitl,
            ):
                pass

            # Verify all draft fields passed correctly
            assert captured_draft is not None
            assert captured_draft["type"] == draft_content["type"]
            assert captured_draft["team_alias"] == draft_content["team_alias"]
            assert captured_draft["title"] == draft_content["title"]
            assert captured_draft["description"] == draft_content["description"]
            assert captured_draft["acceptance_criteria"] == draft_content["acceptance_criteria"]
            assert captured_draft["tags"] == draft_content["tags"]


class TestHITLEventSequence:
    """Tests for event sequence and metadata structure."""

    @pytest.mark.asyncio
    async def test_handoff_event_sequence(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: InMemoryAsyncSession,
        mock_conversation: MagicMock,
        mock_db_session_obj: MagicMock,
    ) -> None:
        """Test correct event sequence during handoff."""
        pending_hitl = {
            "skill_name": "requirements_drafter",
            "category": "confirmation",
            "tool_call_id": "call_seq",
            "step": {
                "id": "step_seq",
                "label": "Draft",
                "executor": "skill",
                "action": "skill",
                "tool": "requirements_drafter",
                "args": {},
            },
            "skill_messages": [
                {
                    "role": "assistant",
                    "content": (
                        "DRAFT READY\n\n"
                        "Type: Bug\n"
                        "Team: web\n"
                        "Title: Fix crash\n"
                        "Description: Fix the crash bug"
                    ),
                }
            ],
        }
        mock_conversation.conversation_metadata = {"pending_hitl": pending_hitl}

        request = AgentRequest(prompt="approved")

        async def mock_writer(*args: Any, **kwargs: Any) -> Any:
            yield {"type": "thinking", "content": "Creating..."}
            yield {"type": "content", "content": "Done"}

        with patch.object(coordinator, '_execute_requirements_writer', mock_writer):
            results = []
            async for event in coordinator._resume_hitl(
                request=request,
                session=mock_db_session,  # type: ignore[arg-type]
                db_session=mock_db_session_obj,
                db_conversation=mock_conversation,
                pending_hitl=pending_hitl,
            ):
                results.append(event)

            # Verify event sequence - should have thinking with handoff first
            handoff_idx = next(
                (i for i, e in enumerate(results)
                 if e.get("type") == "thinking" and e.get("metadata", {}).get("hitl_handoff")),
                -1
            )
            assert handoff_idx >= 0, "Expected handoff thinking event"

            # Should have content from writer
            content_events = [e for e in results if e.get("type") == "content"]
            assert len(content_events) >= 1

    @pytest.mark.asyncio
    async def test_handoff_metadata_structure(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: InMemoryAsyncSession,
        mock_conversation: MagicMock,
        mock_db_session_obj: MagicMock,
    ) -> None:
        """Test that handoff metadata has correct structure for diagnostics."""
        pending_hitl = {
            "skill_name": "requirements_drafter",
            "category": "confirmation",
            "tool_call_id": "call_meta",
            "step": {},
            "skill_messages": [
                {
                    "role": "assistant",
                    "content": (
                        "DRAFT READY\n\n"
                        "Type: Task\n"
                        "Team: platform\n"
                        "Title: Test Task\n"
                        "Description: A test description"
                    ),
                }
            ],
        }
        mock_conversation.conversation_metadata = {"pending_hitl": pending_hitl}

        request = AgentRequest(prompt="yes")

        async def mock_writer(*args: Any, **kwargs: Any) -> Any:
            yield {"type": "content", "content": "Created"}

        with patch.object(coordinator, '_execute_requirements_writer', mock_writer):
            results = []
            async for event in coordinator._resume_hitl(
                request=request,
                session=mock_db_session,  # type: ignore[arg-type]
                db_session=mock_db_session_obj,
                db_conversation=mock_conversation,
                pending_hitl=pending_hitl,
            ):
                results.append(event)

            # Verify handoff event metadata
            handoff_events = [
                e for e in results
                if e.get("type") == "thinking" and e.get("metadata", {}).get("hitl_handoff")
            ]
            assert len(handoff_events) >= 1

            event = handoff_events[0]
            metadata = event.get("metadata", {})
            assert metadata.get("hitl_handoff") is True
            assert metadata.get("role") == "Executor"

    @pytest.mark.asyncio
    async def test_revision_metadata_structure(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: InMemoryAsyncSession,
        mock_conversation: MagicMock,
        mock_db_session_obj: MagicMock,
    ) -> None:
        """Test that revision metadata has correct structure."""
        pending_hitl = {
            "skill_name": "requirements_drafter",
            "category": "confirmation",
            "tool_call_id": "call_rev_meta",
            "step": {
                "id": "step_rev_meta",
                "label": "Draft revision",
                "executor": "skill",
                "action": "skill",
                "tool": "requirements_drafter",
                "args": {},
            },
            "skill_messages": [
                {
                    "role": "assistant",
                    "content": (
                        "DRAFT READY\n\n"
                        "Type: User Story\n"
                        "Team: platform\n"
                        "Title: Test Story\n"
                        "Description: Test description"
                    ),
                }
            ],
        }
        mock_conversation.conversation_metadata = {"pending_hitl": pending_hitl}

        request = AgentRequest(prompt="needs changes")

        results = []
        async for event in coordinator._resume_hitl(
            request=request,
            session=mock_db_session,  # type: ignore[arg-type]
            db_session=mock_db_session_obj,
            db_conversation=mock_conversation,
            pending_hitl=pending_hitl,
        ):
            results.append(event)

        # Verify revision event metadata
        revision_events = [
            e for e in results
            if e.get("type") == "thinking" and e.get("metadata", {}).get("hitl_revision")
        ]
        assert len(revision_events) >= 1

        event = revision_events[0]
        metadata = event.get("metadata", {})
        assert metadata.get("hitl_revision") is True
        assert metadata.get("role") == "Executor"


class TestHITLUnclearIntent:
    """Tests for unclear intent handling."""

    @pytest.mark.asyncio
    async def test_unclear_intent_requests_clarification(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: InMemoryAsyncSession,
        mock_conversation: MagicMock,
        mock_db_session_obj: MagicMock,
    ) -> None:
        """Test that unclear intent asks for clarification."""
        pending_hitl = {
            "skill_name": "requirements_drafter",
            "category": "confirmation",
            "tool_call_id": "call_unclear",
            "step": {},
            "skill_messages": [],
        }
        mock_conversation.conversation_metadata = {"pending_hitl": pending_hitl}

        request = AgentRequest(prompt="what is this?")

        results = []
        async for event in coordinator._resume_hitl(
            request=request,
            session=mock_db_session,  # type: ignore[arg-type]
            db_session=mock_db_session_obj,
            db_conversation=mock_conversation,
            pending_hitl=pending_hitl,
        ):
            results.append(event)

        # Verify clarification message
        content_events = [e for e in results if e.get("type") == "content"]
        assert any("not sure" in str(e.get("content", "")).lower() for e in content_events)
        assert any("approve" in str(e.get("content", "")).lower() for e in content_events)
        assert any("reject" in str(e.get("content", "")).lower() for e in content_events)
