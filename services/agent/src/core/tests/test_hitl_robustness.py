"""Unit tests for HITL robustness - structured draft extraction and intent classification."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from shared.models import (
    AgentRequest,
    AwaitingInputCategory,
    DraftOutput,
    HITLRequest,
    UserIntent,
    WorkItemDraft,
)

from core.db import Conversation, Session
from core.runtime.hitl import HITLCoordinator
from core.tests.mocks import InMemoryAsyncSession, MockLLMClient


async def async_iter(items: list[dict]) -> AsyncIterator[dict]:
    """Helper to create async iterator from list."""
    for item in items:
        yield item


class TestWorkItemDraftModel:
    """Tests for WorkItemDraft Pydantic model validation."""

    def test_valid_work_item_draft(self) -> None:
        """Test creating a valid WorkItemDraft with all fields."""
        draft = WorkItemDraft(
            type="User Story",
            team_alias="platform",
            title="Add OAuth support",
            description="Implement OAuth2 authentication flow",
            acceptance_criteria="User can login with OAuth",
            tags=["auth", "oauth"],
        )
        assert draft.type == "User Story"
        assert draft.team_alias == "platform"
        assert draft.title == "Add OAuth support"
        assert draft.description == "Implement OAuth2 authentication flow"
        assert draft.acceptance_criteria == "User can login with OAuth"
        assert draft.tags == ["auth", "oauth"]

    def test_minimal_valid_draft(self) -> None:
        """Test creating a draft with only required fields."""
        draft = WorkItemDraft(
            type="Bug",
            team_alias="web",
            title="Fix login button",
            description="Button is not clickable",
        )
        assert draft.acceptance_criteria is None
        assert draft.tags == []

    def test_draft_missing_required_fields(self) -> None:
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            WorkItemDraft(  # type: ignore[call-arg]
                type="Bug",
                # Missing team_alias
                title="Fix button",
                description="Button broken",
            )
        assert "team_alias" in str(exc_info.value)

    def test_draft_from_dict_with_string_tags(self) -> None:
        """Test from_dict with comma-separated tags string."""
        data = {
            "type": "Feature",
            "team_alias": "mobile",
            "title": "Dark mode",
            "description": "Add dark mode support",
            "tags": "ui, theme, dark",
        }
        draft = WorkItemDraft.from_dict(data)
        assert draft.tags == ["ui", "theme", "dark"]

    def test_draft_from_dict_with_semicolon_tags(self) -> None:
        """Test from_dict with semicolon-separated tags."""
        data = {
            "type": "Task",
            "team_alias": "infra",
            "title": "Update config",
            "description": "Update server config",
            "tags": "config; server; update",
        }
        draft = WorkItemDraft.from_dict(data)
        assert draft.tags == ["config", "server", "update"]

    def test_draft_from_dict_with_list_tags(self) -> None:
        """Test from_dict with list tags (passthrough)."""
        data = {
            "type": "Bug",
            "team_alias": "qa",
            "title": "Test failure",
            "description": "Test is flaky",
            "tags": ["testing", "flaky"],
        }
        draft = WorkItemDraft.from_dict(data)
        assert draft.tags == ["testing", "flaky"]

    def test_draft_from_dict_with_none_tags(self) -> None:
        """Test from_dict with None tags."""
        data = {
            "type": "Task",
            "team_alias": "dev",
            "title": "Refactor code",
            "description": "Clean up legacy code",
            "tags": None,
        }
        draft = WorkItemDraft.from_dict(data)
        assert draft.tags == []

    def test_draft_from_dict_with_single_tag(self) -> None:
        """Test from_dict with single tag string."""
        data = {
            "type": "Feature",
            "team_alias": "api",
            "title": "New endpoint",
            "description": "Add REST endpoint",
            "tags": "api",
        }
        draft = WorkItemDraft.from_dict(data)
        assert draft.tags == ["api"]



class TestDraftOutputModel:
    """Tests for DraftOutput Pydantic model."""

    def test_valid_draft_output(self) -> None:
        """Test creating a valid DraftOutput with HITL request."""
        draft = WorkItemDraft(
            type="User Story",
            team_alias="platform",
            title="Add feature",
            description="New feature",
        )
        hitl = HITLRequest(
            category=AwaitingInputCategory.CONFIRMATION,
            prompt="Please confirm this draft",
        )
        output = DraftOutput(draft=draft, hitl=hitl)
        assert output.draft.title == "Add feature"
        cat = AwaitingInputCategory.CONFIRMATION
        assert output.hitl.category == cat  # type: ignore[union-attr]

    def test_draft_output_without_hitl(self) -> None:
        """Test creating a DraftOutput without HITL request."""
        draft = WorkItemDraft(
            type="Bug",
            team_alias="web",
            title="Fix crash",
            description="App crashes on startup",
        )
        output = DraftOutput(draft=draft)
        assert output.hitl is None

    def test_draft_output_json_roundtrip(self) -> None:
        """Test JSON serialization and deserialization."""
        draft = WorkItemDraft(
            type="Feature",
            team_alias="mobile",
            title="Push notifications",
            description="Add push notification support",
            acceptance_criteria="User receives push notifications",
            tags=["mobile", "notifications"],
        )
        hitl = HITLRequest(
            category=AwaitingInputCategory.CONFIRMATION,
            prompt="Does this look correct?",
        )
        output = DraftOutput(draft=draft, hitl=hitl)
        json_str = output.model_dump_json()
        parsed = DraftOutput.model_validate_json(json_str)
        assert parsed.draft.title == "Push notifications"
        assert parsed.draft.tags == ["mobile", "notifications"]
        assert parsed.hitl.prompt == "Does this look correct?"  # type: ignore[union-attr]


class TestExtractDraftFromMessages:
    """Tests for _extract_draft_from_messages method."""

    @pytest.fixture
    def coordinator(self) -> HITLCoordinator:
        mock_skill_registry = MagicMock()
        mock_tool_registry = MagicMock()
        mock_litellm = MockLLMClient()
        return HITLCoordinator(
            skill_registry=mock_skill_registry,
            tool_registry=mock_tool_registry,
            litellm=mock_litellm,
        )

    def test_extract_valid_json_from_code_block(self, coordinator: HITLCoordinator) -> None:
        """Test extracting valid JSON from markdown code block."""
        draft_output = DraftOutput(
            draft=WorkItemDraft(
                type="User Story",
                team_alias="platform",
                title="OAuth Support",
                description="Add OAuth2 authentication",
                acceptance_criteria="User can login with OAuth",
                tags=["auth"],
            ),
            hitl=HITLRequest(
                category=AwaitingInputCategory.CONFIRMATION,
                prompt="Confirm?",
            ),
        )
        messages = [
            {
                "role": "assistant",
                "content": f"```json\n{draft_output.model_dump_json()}\n```",
            }
        ]
        result = coordinator._extract_draft_from_messages(messages)
        assert result is not None
        assert result["type"] == "User Story"
        assert result["team_alias"] == "platform"
        assert result["title"] == "OAuth Support"
        assert result["acceptance_criteria"] == "User can login with OAuth"
        assert result["tags"] == ["auth"]

    def test_extract_valid_json_without_language_tag(self, coordinator: HITLCoordinator) -> None:
        """Test extracting JSON from code block without 'json' tag."""
        draft_output = DraftOutput(
            draft=WorkItemDraft(
                type="Bug",
                team_alias="web",
                title="Fix button",
                description="Button not working",
            ),
        )
        messages = [
            {
                "role": "assistant",
                "content": f"```\n{draft_output.model_dump_json()}\n```",
            }
        ]
        result = coordinator._extract_draft_from_messages(messages)
        assert result is not None
        assert result["type"] == "Bug"
        assert result["title"] == "Fix button"

    def test_extract_raw_json_without_code_block(self, coordinator: HITLCoordinator) -> None:
        """Test extracting raw JSON containing 'draft' key."""
        draft_data = {
            "draft": {
                "type": "Feature",
                "team_alias": "api",
                "title": "New API",
                "description": "Add REST API",
                "tags": [],
            }
        }
        messages = [
            {
                "role": "assistant",
                "content": f"DRAFT READY\n\n{json.dumps(draft_data)}",
            }
        ]
        result = coordinator._extract_draft_from_messages(messages)
        assert result is not None
        assert result["type"] == "Feature"
        assert result["team_alias"] == "api"

    def test_malformed_json_falls_back_to_regex(self, coordinator: HITLCoordinator) -> None:
        """Test that malformed JSON falls back to regex extraction."""
        messages = [
            {
                "role": "assistant",
                "content": (
                    "DRAFT READY\n\n"
                    "Type: Bug\n"
                    "Team: platform - Platform Team\n"
                    "Title: Fix crash\n"
                    "Description:\n"
                    "App crashes when loading\n"
                    "Acceptance Criteria:\n"
                    "App loads successfully\n"
                    "Tags: bug, crash\n"
                ),
            }
        ]
        result = coordinator._extract_draft_from_messages(messages)
        assert result is not None
        assert result["type"] == "Bug"
        assert result["team_alias"] == "platform"
        assert result["title"] == "Fix crash"
        assert "crash" in result.get("tags", [])

    def test_missing_required_fields_returns_none(self, coordinator: HITLCoordinator) -> None:
        """Test that missing required fields in regex fallback returns None."""
        messages = [
            {
                "role": "assistant",
                "content": (
                    "DRAFT READY\n\n"
                    "Type: Bug\n"
                    "Title: Fix crash\n"
                    "Description: Something\n"
                ),
            }
        ]
        result = coordinator._extract_draft_from_messages(messages)
        assert result is None

    def test_empty_messages_returns_none(self, coordinator: HITLCoordinator) -> None:
        """Test that empty messages returns None."""
        result = coordinator._extract_draft_from_messages([])
        assert result is None

    def test_messages_without_draft_content(self, coordinator: HITLCoordinator) -> None:
        """Test that messages without draft content returns None."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = coordinator._extract_draft_from_messages(messages)
        assert result is None

    def test_json_with_validation_error_falls_back(self, coordinator: HITLCoordinator) -> None:
        """Test that JSON with missing required fields falls back to regex."""
        invalid_json = json.dumps({
            "draft": {
                "type": "Bug",
                "description": "Something",
            }
        })
        messages = [
            {
                "role": "assistant",
                "content": f"```json\n{invalid_json}\n```",
            }
        ]
        result = coordinator._extract_draft_from_messages(messages)
        assert result is None

    def test_nested_json_extraction(self, coordinator: HITLCoordinator) -> None:
        """Test extracting nested JSON from complex content."""
        draft_output = DraftOutput(
            draft=WorkItemDraft(
                type="User Story",
                team_alias="mobile",
                title="Offline mode",
                description="Support offline usage",
                acceptance_criteria="Works without internet",
                tags=["offline", "mobile"],
            ),
        )
        json_content = draft_output.model_dump_json()
        messages = [
            {
                "role": "assistant",
                "content": (
                    f"I've prepared a draft for you:\n\n"
                    f"```json\n{json_content}\n```\n\n"
                    f"Please review and confirm."
                ),
            }
        ]
        result = coordinator._extract_draft_from_messages(messages)
        assert result is not None
        assert result["type"] == "User Story"
        assert result["title"] == "Offline mode"
        assert result["team_alias"] == "mobile"

    def test_regex_team_alias_strips_display_name(self, coordinator: HITLCoordinator) -> None:
        """Test that regex extraction strips display name from team."""
        messages = [
            {
                "role": "assistant",
                "content": (
                    "DRAFT READY\n\n"
                    "Type: Feature\n"
                    "Team: infra - Infrastructure Team\n"
                    "Title: New deployment\n"
                    "Description:\n"
                    "Automate deployments\n"
                    "Acceptance Criteria:\n"
                    "One-click deploy\n"
                    "Tags: devops\n"
                ),
            }
        ]
        result = coordinator._extract_draft_from_messages(messages)
        assert result is not None
        assert result["team_alias"] == "infra"


class TestClassifyUserIntent:
    """Tests for _classify_user_intent method."""

    @pytest.fixture
    def coordinator(self) -> HITLCoordinator:
        mock_skill_registry = MagicMock()
        mock_tool_registry = MagicMock()
        mock_litellm = MockLLMClient()
        return HITLCoordinator(
            skill_registry=mock_skill_registry,
            tool_registry=mock_tool_registry,
            litellm=mock_litellm,
        )

    @pytest.mark.asyncio
    async def test_approve_keyword_looks_good(self, coordinator: HITLCoordinator) -> None:
        """Test 'looks good' is classified as APPROVE."""
        intent, confidence = await coordinator._classify_user_intent("looks good")
        assert intent == UserIntent.APPROVE
        assert confidence == 1.0

    @pytest.mark.asyncio
    async def test_approve_keyword_do_it(self, coordinator: HITLCoordinator) -> None:
        """Test 'do it' is classified as APPROVE."""
        intent, confidence = await coordinator._classify_user_intent("do it")
        assert intent == UserIntent.APPROVE
        assert confidence == 1.0

    @pytest.mark.asyncio
    async def test_approve_keyword_yes(self, coordinator: HITLCoordinator) -> None:
        """Test 'yes' is classified as APPROVE."""
        intent, confidence = await coordinator._classify_user_intent("yes")
        assert intent == UserIntent.APPROVE
        assert confidence == 1.0

    @pytest.mark.asyncio
    async def test_approve_keyword_okay(self, coordinator: HITLCoordinator) -> None:
        """Test 'okay' is classified as APPROVE."""
        intent, confidence = await coordinator._classify_user_intent("okay")
        assert intent == UserIntent.APPROVE
        assert confidence == 1.0

    @pytest.mark.asyncio
    async def test_reject_keyword_cancel_this(self, coordinator: HITLCoordinator) -> None:
        """Test 'cancel this' is classified as REJECT."""
        intent, confidence = await coordinator._classify_user_intent("cancel this")
        assert intent == UserIntent.REJECT
        assert confidence == 1.0

    @pytest.mark.asyncio
    async def test_reject_keyword_no(self, coordinator: HITLCoordinator) -> None:
        """Test 'no' is classified as REJECT."""
        intent, confidence = await coordinator._classify_user_intent("no")
        assert intent == UserIntent.REJECT
        assert confidence == 1.0

    @pytest.mark.asyncio
    async def test_reject_keyword_stop(self, coordinator: HITLCoordinator) -> None:
        """Test 'stop' is classified as REJECT."""
        intent, confidence = await coordinator._classify_user_intent("stop")
        assert intent == UserIntent.REJECT
        assert confidence == 1.0

    @pytest.mark.asyncio
    async def test_request_changes_keyword_needs_work(self, coordinator: HITLCoordinator) -> None:
        """Test 'needs work' is classified as REQUEST_CHANGES."""
        intent, confidence = await coordinator._classify_user_intent("needs work")
        assert intent == UserIntent.REQUEST_CHANGES
        assert confidence == 1.0

    @pytest.mark.asyncio
    async def test_request_changes_keyword_change_title(self, coordinator: HITLCoordinator) -> None:
        """Test 'change the title' is classified as REQUEST_CHANGES."""
        intent, confidence = await coordinator._classify_user_intent("change the title")
        assert intent == UserIntent.REQUEST_CHANGES
        assert confidence == 1.0

    @pytest.mark.asyncio
    async def test_request_changes_keyword_revise(self, coordinator: HITLCoordinator) -> None:
        """Test 'revise this' is classified as REQUEST_CHANGES."""
        intent, confidence = await coordinator._classify_user_intent("revise this")
        assert intent == UserIntent.REQUEST_CHANGES
        assert confidence == 1.0

    @pytest.mark.asyncio
    async def test_unclear_with_llm_fallback(self, coordinator: HITLCoordinator) -> None:
        """Test unclear input uses LLM for classification."""
        coordinator._litellm.responses = ["APPROVE"]  # type: ignore[attr-defined]
        intent, confidence = await coordinator._classify_user_intent("perhaps this is tolerable")
        assert intent == UserIntent.APPROVE
        assert confidence == 0.95

    @pytest.mark.asyncio
    async def test_unclear_with_llm_reject(self, coordinator: HITLCoordinator) -> None:
        """Test LLM can classify as REJECT."""
        coordinator._litellm.responses = ["REJECT"]  # type: ignore[attr-defined]
        intent, confidence = await coordinator._classify_user_intent(
            "that approach seems ill-advised"
        )
        assert intent == UserIntent.REJECT
        assert confidence == 0.95

    @pytest.mark.asyncio
    async def test_unclear_with_llm_request_changes(self, coordinator: HITLCoordinator) -> None:
        """Test LLM can classify as REQUEST_CHANGES."""
        coordinator._litellm.responses = ["REQUEST_CHANGES"]  # type: ignore[attr-defined]
        intent, confidence = await coordinator._classify_user_intent("It needs something different")
        assert intent == UserIntent.REQUEST_CHANGES
        assert confidence == 0.90

    @pytest.mark.asyncio
    async def test_llm_unclear_response(self, coordinator: HITLCoordinator) -> None:
        """Test LLM unclear response returns UNCLEAR with low confidence."""
        coordinator._litellm.responses = ["I don't understand"]  # type: ignore[attr-defined]
        intent, confidence = await coordinator._classify_user_intent("what is this?")
        assert intent == UserIntent.UNCLEAR
        assert confidence == 0.50

    @pytest.mark.asyncio
    async def test_llm_timeout_returns_unclear(self, coordinator: HITLCoordinator) -> None:
        """Test that LLM timeout returns UNCLEAR with 0 confidence."""
        mock_litellm = MagicMock()
        mock_litellm.generate = AsyncMock(side_effect=TimeoutError())
        
        timeout_coordinator = HITLCoordinator(
            skill_registry=MagicMock(),
            tool_registry=MagicMock(),
            litellm=mock_litellm,
        )
        
        intent, confidence = await timeout_coordinator._classify_user_intent("some text")
        assert intent == UserIntent.UNCLEAR
        assert confidence == 0.0

    @pytest.mark.asyncio
    async def test_llm_exception_returns_unclear(self, coordinator: HITLCoordinator) -> None:
        """Test that LLM exception returns UNCLEAR with 0 confidence."""
        coordinator._litellm.responses = []  # type: ignore[attr-defined]
        
        async def raise_error(*args: Any, **kwargs: Any) -> str:
            raise ValueError("LLM error")
        
        coordinator._litellm.generate = raise_error  # type: ignore[method-assign]
        
        intent, confidence = await coordinator._classify_user_intent("test")
        assert intent == UserIntent.UNCLEAR
        assert confidence == 0.0


class TestHITLStateTransitions:
    """Tests for HITL state transitions: pause -> resume -> complete."""

    @pytest.fixture
    def coordinator(self) -> HITLCoordinator:
        mock_skill_registry = MagicMock()
        mock_tool_registry = MagicMock()
        mock_litellm = MockLLMClient()
        return HITLCoordinator(
            skill_registry=mock_skill_registry,
            tool_registry=mock_tool_registry,
            litellm=mock_litellm,
        )

    @pytest.fixture
    def mock_db_session(self) -> Session:
        """Create a mock DB session."""
        session = MagicMock(spec=Session)
        session.id = uuid.uuid4()
        return session

    @pytest.fixture
    def mock_conversation(self) -> Conversation:
        """Create a mock conversation."""
        conv = MagicMock(spec=Conversation)
        conv.id = uuid.uuid4()
        conv.conversation_metadata = {}
        return conv

    @pytest.mark.asyncio
    async def test_resume_hitl_with_approval_executes_writer(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: Session,
        mock_conversation: Conversation,
    ) -> None:
        """Test that approving a draft triggers requirements_writer execution."""
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
                        "Type: User Story\n"
                        "Team: platform\n"
                        "Title: Add OAuth\n"
                        "Description: OAuth support\n"
                    ),
                }
            ],
        }
        mock_conversation.conversation_metadata = {"pending_hitl": pending_hitl}
        request = AgentRequest(prompt="yes")
        
        async def mock_writer(*args: Any, **kwargs: Any) -> AsyncIterator[dict]:
            yield {"type": "content", "content": "Work item created!"}
        
        with patch.object(coordinator, '_execute_requirements_writer', mock_writer):
            results = []
            async for event in coordinator._resume_hitl(
                request=request,
                session=InMemoryAsyncSession(),  # type: ignore[arg-type]
                db_session=mock_db_session,
                db_conversation=mock_conversation,
                pending_hitl=pending_hitl,
            ):
                results.append(event)
            
            # Check that handoff thinking event was yielded
            thinking_events = [e for e in results if e.get("type") == "thinking"]
            assert any(
                "creating work item" in str(e.get("content", "")).lower()
                for e in thinking_events
            )

    @pytest.mark.asyncio
    async def test_resume_hitl_with_rejection_cancels(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: Session,
        mock_conversation: Conversation,
    ) -> None:
        """Test that rejecting a draft cancels work item creation."""
        pending_hitl = {
            "skill_name": "requirements_drafter",
            "category": "confirmation",
            "tool_call_id": "call_123",
            "step": {},
            "skill_messages": [],
        }
        
        request = AgentRequest(prompt="no")
        
        results = []
        async for event in coordinator._resume_hitl(
            request=request,
            session=InMemoryAsyncSession(),  # type: ignore[arg-type]
            db_session=mock_db_session,
            db_conversation=mock_conversation,
            pending_hitl=pending_hitl,
        ):
            results.append(event)
        
        content_events = [e for e in results if e.get("type") == "content"]
        assert any("cancelled" in str(e.get("content", "")).lower() for e in content_events)

    @pytest.mark.asyncio
    async def test_resume_hitl_with_request_changes_asks_for_revision(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: Session,
        mock_conversation: Conversation,
    ) -> None:
        """Test that requesting changes triggers revision flow."""
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
                {"role": "assistant", "content": (
                    "DRAFT READY\n\nType: Bug\nTeam: web\nTitle: Fix\nDescription: Bug"
                )}
            ],
        }
        
        request = AgentRequest(prompt="change the title")
        
        results = []
        async for event in coordinator._resume_hitl(
            request=request,
            session=InMemoryAsyncSession(),  # type: ignore[arg-type]
            db_session=mock_db_session,
            db_conversation=mock_conversation,
            pending_hitl=pending_hitl,
        ):
            results.append(event)
        
        thinking_events = [e for e in results if e.get("type") == "thinking"]
        assert any("revising" in str(e.get("content", "")).lower() for e in thinking_events)

    @pytest.mark.asyncio
    async def test_resume_hitl_clears_pending_after_completion(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: Session,
        mock_conversation: Conversation,
    ) -> None:
        """Test that pending HITL is cleared after resuming."""
        pending_hitl = {
            "skill_name": "requirements_drafter",
            "category": "confirmation",
            "tool_call_id": "call_123",
            "step": {},
            "skill_messages": [],
        }
        
        mock_conversation.conversation_metadata = {"pending_hitl": pending_hitl}
        request = AgentRequest(prompt="no")
        session = InMemoryAsyncSession()
        
        async for _ in coordinator._resume_hitl(
            request=request,
            session=session,  # type: ignore[arg-type]
            db_session=mock_db_session,
            db_conversation=mock_conversation,
            pending_hitl=pending_hitl,
        ):
            pass
        
        assert "pending_hitl" not in mock_conversation.conversation_metadata

    @pytest.mark.asyncio
    async def test_resume_hitl_with_unclear_intent_requests_clarification(
        self,
        coordinator: HITLCoordinator,
        mock_db_session: Session,
        mock_conversation: Conversation,
    ) -> None:
        """Test that unclear intent requests clarification from user."""
        pending_hitl = {
            "skill_name": "requirements_drafter",
            "category": "confirmation",
            "tool_call_id": "call_123",
            "step": {},
            "skill_messages": [],
        }
        
        request = AgentRequest(prompt="hmm, I'm confused")
        
        results = []
        async for event in coordinator._resume_hitl(
            request=request,
            session=InMemoryAsyncSession(),  # type: ignore[arg-type]
            db_session=mock_db_session,
            db_conversation=mock_conversation,
            pending_hitl=pending_hitl,
        ):
            results.append(event)
        
        content_events = [e for e in results if e.get("type") == "content"]
        assert any("not sure" in str(e.get("content", "")).lower() for e in content_events)
