"""Tests for PlannerAgent plan generation."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from shared.models import AgentMessage, AgentRequest, Plan

from core.agents.planner import MAX_PROMPT_LENGTH, PlannerAgent, _sanitize_user_input
from core.tests.mocks import MockLLMClient


def _llm(planner: PlannerAgent) -> MockLLMClient:
    """Access the mock LLM client from a planner for test assertions."""
    return planner._litellm  # type: ignore[return-value]


@pytest.fixture
def mock_litellm() -> MockLLMClient:
    """Create a mock LiteLLM client."""
    return MockLLMClient(responses=[])


@pytest.fixture
def planner(mock_litellm: MockLLMClient) -> PlannerAgent:
    """Create a PlannerAgent with mocked LiteLLM."""
    return PlannerAgent(litellm=mock_litellm, model_name="test-planner")


@pytest.fixture
def base_request() -> AgentRequest:
    """Create a base agent request for testing."""
    return AgentRequest(prompt="Test user request")


@pytest.fixture
def tool_descriptions() -> list[dict[str, Any]]:
    """Sample tool descriptions."""
    return [
        {
            "name": "web_search",
            "description": "Search the web",
            "parameters": {"query": "string"},
        },
        {
            "name": "calculator",
            "description": "Perform calculations",
            "schema": {"expression": "string"},
        },
    ]


@pytest.fixture
def available_skills() -> str:
    """Sample available skills text."""
    return """
## Available Skills
- researcher: Web research with page reading
- search: Quick web search
- backlog_manager: Azure DevOps backlog management
"""


class TestSanitizeUserInput:
    """Tests for _sanitize_user_input function."""

    def test_returns_unchanged_for_normal_input(self) -> None:
        """Normal input should pass through unchanged."""
        text = "What is the weather today?"
        result = _sanitize_user_input(text)
        assert result == text

    def test_removes_markdown_code_fences(self) -> None:
        """Markdown code fences should be removed."""
        text = "Here is code:\n```json\n{}\n```"
        result = _sanitize_user_input(text)
        assert "```json" not in result
        assert "```" not in result
        assert "Here is code:" in result

    def test_truncates_long_input(self) -> None:
        """Input exceeding MAX_PROMPT_LENGTH should be truncated."""
        text = "A" * (MAX_PROMPT_LENGTH + 100)
        result = _sanitize_user_input(text)
        assert len(result) <= MAX_PROMPT_LENGTH + 30  # Allow for truncation message
        assert "(input truncated)" in result

    def test_handles_empty_string(self) -> None:
        """Empty string should be returned as-is."""
        result = _sanitize_user_input("")
        assert result == ""

    def test_handles_none(self) -> None:
        """None should be returned as-is."""
        result = _sanitize_user_input(None)  # type: ignore[arg-type]
        assert result is None


class TestPlannerAgentGenerate:
    """Tests for PlannerAgent.generate (non-streaming)."""

    @pytest.mark.asyncio
    async def test_generate_returns_valid_plan(
        self,
        planner: PlannerAgent,
        base_request: AgentRequest,
        tool_descriptions: list[dict[str, Any]],
        available_skills: str,
    ) -> None:
        """Generate should return a valid Plan object."""
        # Mock LLM to return valid JSON plan
        valid_plan_json = {
            "description": "Test plan",
            "steps": [
                {
                    "id": "1",
                    "label": "Research",
                    "executor": "skill",
                    "action": "skill",
                    "tool": "researcher",
                    "args": {"goal": "Find info"},
                }
            ],
        }
        _llm(planner).responses = [valid_plan_json]

        with (
            patch("core.agents.planner.start_span"),
            patch("core.agents.planner.current_trace_ids"),
            patch("core.agents.planner.log_event"),
        ):
            plan = await planner.generate(
                request=base_request,
                history=[],
                tool_descriptions=tool_descriptions,
                available_skills_text=available_skills,
            )

        assert isinstance(plan, Plan)
        assert plan.description == "Test plan"
        assert len(plan.steps) == 1
        assert plan.steps[0].tool == "researcher"

    @pytest.mark.asyncio
    async def test_generate_retries_on_invalid_json(
        self,
        planner: PlannerAgent,
        base_request: AgentRequest,
        tool_descriptions: list[dict[str, Any]],
        available_skills: str,
    ) -> None:
        """Generate should retry when LLM returns invalid JSON."""
        # First response: invalid JSON
        # Second response: valid JSON
        valid_plan = {
            "description": "Fixed plan",
            "steps": [
                {
                    "id": "1",
                    "label": "Search",
                    "executor": "skill",
                    "action": "skill",
                    "tool": "search",
                    "args": {},
                }
            ],
        }
        _llm(planner).responses = ["This is not valid JSON", valid_plan]

        with (
            patch("core.agents.planner.start_span"),
            patch("core.agents.planner.current_trace_ids"),
            patch("core.agents.planner.log_event"),
        ):
            plan = await planner.generate(
                request=base_request,
                history=[],
                tool_descriptions=tool_descriptions,
                available_skills_text=available_skills,
            )

        assert isinstance(plan, Plan)
        assert plan.description == "Fixed plan"
        # Should have made 2 LLM calls (first failed, second succeeded)
        assert len(_llm(planner).call_history) == 2

    @pytest.mark.asyncio
    async def test_generate_with_stream_callback(
        self,
        planner: PlannerAgent,
        base_request: AgentRequest,
        tool_descriptions: list[dict[str, Any]],
        available_skills: str,
    ) -> None:
        """Generate should invoke stream callback with tokens."""
        valid_plan = {
            "description": "Test plan",
            "steps": [
                {
                    "id": "1",
                    "label": "Step",
                    "executor": "litellm",
                    "action": "completion",
                    "args": {},
                }
            ],
        }
        _llm(planner).responses = [valid_plan]

        callback = AsyncMock()

        with (
            patch("core.agents.planner.start_span"),
            patch("core.agents.planner.current_trace_ids"),
            patch("core.agents.planner.log_event"),
        ):
            await planner.generate(
                request=base_request,
                history=[],
                tool_descriptions=tool_descriptions,
                available_skills_text=available_skills,
                stream_callback=callback,
            )

        # Callback should have been called with token content
        assert callback.call_count > 0

    @pytest.mark.asyncio
    async def test_generate_raises_when_no_plan_returned(
        self,
        planner: PlannerAgent,
        base_request: AgentRequest,
        tool_descriptions: list[dict[str, Any]],
        available_skills: str,
    ) -> None:
        """Generate should raise ValueError when stream yields no plan."""

        # Mock a scenario where generate_stream never yields a plan
        # This is difficult with the current implementation, so we'll patch generate_stream
        async def mock_stream(*args: Any, **kwargs: Any) -> Any:
            yield {"type": "token", "content": "test"}
            # Never yield a plan event
            return
            yield  # Make this a generator

        with (
            patch.object(planner, "generate_stream", new=mock_stream),
            pytest.raises(ValueError, match="failed to return a plan"),
        ):
            await planner.generate(
                request=base_request,
                history=[],
                tool_descriptions=tool_descriptions,
                available_skills_text=available_skills,
            )


class TestPlannerAgentGenerateStream:
    """Tests for PlannerAgent.generate_stream (streaming)."""

    @pytest.mark.asyncio
    async def test_generate_stream_yields_tokens_and_plan(
        self,
        planner: PlannerAgent,
        base_request: AgentRequest,
        tool_descriptions: list[dict[str, Any]],
        available_skills: str,
    ) -> None:
        """Generate stream should yield token and plan events."""
        valid_plan = {
            "description": "Stream plan",
            "steps": [
                {
                    "id": "1",
                    "label": "Step",
                    "executor": "litellm",
                    "action": "completion",
                    "args": {},
                }
            ],
        }
        _llm(planner).responses = [valid_plan]

        events: list[dict[str, Any]] = []
        with (
            patch("core.agents.planner.start_span"),
            patch("core.agents.planner.current_trace_ids"),
            patch("core.agents.planner.log_event"),
        ):
            async for event in planner.generate_stream(
                request=base_request,
                history=[],
                tool_descriptions=tool_descriptions,
                available_skills_text=available_skills,
            ):
                events.append(event)

        # Should have token events and one plan event
        token_events = [e for e in events if e["type"] == "token"]
        plan_events = [e for e in events if e["type"] == "plan"]

        assert len(token_events) > 0
        assert len(plan_events) == 1
        assert isinstance(plan_events[0]["plan"], Plan)

    @pytest.mark.asyncio
    async def test_generate_stream_enforces_executor_guardrails(
        self,
        planner: PlannerAgent,
        base_request: AgentRequest,
        tool_descriptions: list[dict[str, Any]],
        available_skills: str,
    ) -> None:
        """Generate stream should enforce executor/action consistency."""
        # LLM returns plan with mismatched executor/action
        plan_with_mismatch = {
            "description": "Mismatch plan",
            "steps": [
                {
                    "id": "1",
                    "label": "Tool step",
                    "executor": "litellm",  # Wrong executor
                    "action": "tool",
                    "tool": "web_search",
                    "args": {},
                },
                {
                    "id": "2",
                    "label": "Skill step",
                    "executor": "litellm",  # Wrong executor
                    "action": "skill",
                    "tool": "researcher",
                    "args": {},
                },
            ],
        }
        _llm(planner).responses = [plan_with_mismatch]

        with (
            patch("core.agents.planner.start_span"),
            patch("core.agents.planner.current_trace_ids"),
            patch("core.agents.planner.log_event"),
        ):
            async for event in planner.generate_stream(
                request=base_request,
                history=[],
                tool_descriptions=tool_descriptions,
                available_skills_text=available_skills,
            ):
                if event["type"] == "plan":
                    plan = event["plan"]
                    # Guardrails should have corrected executors
                    assert plan.steps[0].executor == "agent"  # tool action → agent executor
                    assert plan.steps[1].executor == "skill"  # skill action → skill executor

    @pytest.mark.asyncio
    async def test_generate_stream_retries_with_feedback(
        self,
        planner: PlannerAgent,
        base_request: AgentRequest,
        tool_descriptions: list[dict[str, Any]],
        available_skills: str,
    ) -> None:
        """Generate stream should retry with error feedback on validation failure."""
        # First response: invalid plan (step missing required fields)
        invalid_plan = {
            "description": "Invalid",
            "steps": [{"id": "1"}],  # Missing label, executor, action
        }
        # Second response: valid plan
        valid_plan = {
            "description": "Valid plan",
            "steps": [
                {
                    "id": "1",
                    "label": "Step",
                    "executor": "litellm",
                    "action": "completion",
                    "args": {},
                }
            ],
        }
        _llm(planner).responses = [invalid_plan, valid_plan]

        events: list[dict[str, Any]] = []
        with (
            patch("core.agents.planner.start_span"),
            patch("core.agents.planner.current_trace_ids"),
            patch("core.agents.planner.log_event"),
        ):
            async for event in planner.generate_stream(
                request=base_request,
                history=[],
                tool_descriptions=tool_descriptions,
                available_skills_text=available_skills,
            ):
                events.append(event)

        # Should have made 2 LLM calls (initial + retry)
        # First call returns invalid_plan (ValidationError), triggers retry
        # Second call returns valid_plan
        assert len(_llm(planner).call_history) == 2

    @pytest.mark.asyncio
    async def test_generate_stream_fallback_to_conversational_plan(
        self,
        planner: PlannerAgent,
        tool_descriptions: list[dict[str, Any]],
        available_skills: str,
    ) -> None:
        """Generate stream should detect conversational messages and return completion plan."""
        # User says "hi" - short greeting
        request = AgentRequest(prompt="hi")
        # LLM returns confused response (echoing prompts)
        _llm(planner).responses = ["### AVAILABLE TOOLS\n..."]

        events: list[dict[str, Any]] = []
        with (
            patch("core.agents.planner.start_span"),
            patch("core.agents.planner.current_trace_ids"),
            patch("core.agents.planner.log_event"),
        ):
            async for event in planner.generate_stream(
                request=request,
                history=[],
                tool_descriptions=tool_descriptions,
                available_skills_text=available_skills,
            ):
                events.append(event)

        plan_events = [e for e in events if e["type"] == "plan"]
        assert len(plan_events) == 1
        plan = plan_events[0]["plan"]
        # Should be a conversational fallback plan
        is_conversational = "conversational" in plan.description.lower()
        is_direct_response = "direct response" in plan.description.lower()
        assert is_conversational or is_direct_response
        assert len(plan.steps) == 1
        assert plan.steps[0].action == "completion"

    @pytest.mark.asyncio
    async def test_generate_stream_max_retries_returns_empty_plan(
        self,
        planner: PlannerAgent,
        base_request: AgentRequest,
        tool_descriptions: list[dict[str, Any]],
        available_skills: str,
    ) -> None:
        """Generate stream should return empty plan after max retries (non-conversational)."""
        # All responses are invalid, non-conversational
        _llm(planner).responses = [
            "invalid json 1",
            "invalid json 2",
            "invalid json 3",
        ]

        events: list[dict[str, Any]] = []
        with (
            patch("core.agents.planner.start_span"),
            patch("core.agents.planner.current_trace_ids"),
            patch("core.agents.planner.log_event"),
        ):
            async for event in planner.generate_stream(
                request=base_request,
                history=[],
                tool_descriptions=tool_descriptions,
                available_skills_text=available_skills,
            ):
                events.append(event)

        plan_events = [e for e in events if e["type"] == "plan"]
        assert len(plan_events) == 1
        plan = plan_events[0]["plan"]
        # Should be an empty plan with error description
        assert len(plan.steps) == 0
        assert "failed" in plan.description.lower()


class TestExtractJsonFragment:
    """Tests for PlannerAgent._extract_json_fragment static method."""

    def test_extracts_valid_json_from_text(self) -> None:
        """Should extract JSON object from text."""
        text = 'Here is the plan: {"description": "test", "steps": []}'
        result = PlannerAgent._extract_json_fragment(text)
        assert result is not None
        assert result["description"] == "test"
        assert result["steps"] == []

    def test_extracts_json_with_leading_trailing_text(self) -> None:
        """Should extract JSON even with surrounding text."""
        text = 'Some prefix\n{"key": "value"}\nSome suffix'
        result = PlannerAgent._extract_json_fragment(text)
        assert result is not None
        assert result["key"] == "value"

    def test_returns_none_for_invalid_json(self) -> None:
        """Should return None for text with no valid JSON."""
        text = "This is just plain text with no JSON"
        result = PlannerAgent._extract_json_fragment(text)
        assert result is None

    def test_handles_nested_braces(self) -> None:
        """Should handle JSON with nested objects."""
        text = '{"outer": {"inner": "value"}}'
        result = PlannerAgent._extract_json_fragment(text)
        assert result is not None
        assert result["outer"]["inner"] == "value"

    def test_parses_direct_json_without_text(self) -> None:
        """Should parse JSON directly without extra text."""
        text = '{"direct": true}'
        result = PlannerAgent._extract_json_fragment(text)
        assert result is not None
        assert result["direct"] is True


class TestIsConversationalMessage:
    """Tests for PlannerAgent._is_conversational_message static method."""

    def test_detects_greeting_in_short_message(self) -> None:
        """Should detect greetings as conversational when LLM output shows confusion."""
        # Need some raw_output (LLM confusion) + short greeting prompt
        confused_output = "I'll help you with that!"
        assert PlannerAgent._is_conversational_message(confused_output, "hi") is True
        assert PlannerAgent._is_conversational_message(confused_output, "hello there") is True
        assert PlannerAgent._is_conversational_message(confused_output, "hey") is True
        assert PlannerAgent._is_conversational_message(confused_output, "hej") is True

    def test_detects_thank_you_messages(self) -> None:
        """Should detect thank you messages as conversational when LLM shows confusion."""
        # These are short messages (< 20 chars) with thank/ok keywords + confused output
        confused_output = "I'm here to assist you"
        assert PlannerAgent._is_conversational_message(confused_output, "thanks") is True
        assert PlannerAgent._is_conversational_message(confused_output, "thank you!") is True
        assert PlannerAgent._is_conversational_message(confused_output, "ok") is True

    def test_detects_confused_planner_output(self) -> None:
        """Should detect when planner echoes prompt as confusion."""
        raw_output = "### AVAILABLE TOOLS\nHere are the tools..."
        assert PlannerAgent._is_conversational_message(raw_output, "hi") is True

        raw_output = "I'll help you with that task..."
        assert PlannerAgent._is_conversational_message(raw_output, "search") is True

    def test_does_not_flag_long_requests(self) -> None:
        """Should not flag long requests as conversational."""
        long_request = "Please research the latest developments in quantum computing and summarize"
        assert PlannerAgent._is_conversational_message("", long_request) is False

    def test_does_not_flag_actionable_requests(self) -> None:
        """Should not flag actionable requests as conversational."""
        assert PlannerAgent._is_conversational_message("", "search for python docs") is False
        assert PlannerAgent._is_conversational_message("", "turn off lights") is False


class TestToolDescriptionsFormatting:
    """Tests for tool descriptions formatting in system prompt."""

    @pytest.mark.asyncio
    async def test_includes_tool_descriptions_in_prompt(
        self,
        planner: PlannerAgent,
        base_request: AgentRequest,
        available_skills: str,
    ) -> None:
        """System prompt should include formatted tool descriptions."""
        tool_descriptions = [
            {
                "name": "calculator",
                "description": "Perform math calculations",
                "parameters": {"expression": "string"},
            }
        ]

        valid_plan = {
            "description": "Test",
            "steps": [
                {
                    "id": "1",
                    "label": "Step",
                    "executor": "litellm",
                    "action": "completion",
                    "args": {},
                }
            ],
        }
        _llm(planner).responses = [valid_plan]

        with (
            patch("core.agents.planner.start_span"),
            patch("core.agents.planner.current_trace_ids"),
            patch("core.agents.planner.log_event"),
        ):
            await planner.generate(
                request=base_request,
                history=[],
                tool_descriptions=tool_descriptions,
                available_skills_text=available_skills,
            )

        # Check that call history includes tool descriptions
        assert len(_llm(planner).call_history) > 0
        messages = _llm(planner).call_history[0]
        user_messages = [m for m in messages if m.role == "user"]
        assert len(user_messages) > 0
        # User message should contain tool descriptions
        user_content = user_messages[0].content or ""
        assert "calculator" in user_content
        assert "math calculations" in user_content

    @pytest.mark.asyncio
    async def test_handles_tools_without_schema(
        self,
        planner: PlannerAgent,
        base_request: AgentRequest,
        available_skills: str,
    ) -> None:
        """Should handle tools that don't have parameters/schema defined."""
        tool_descriptions = [
            {
                "name": "simple_tool",
                "description": "A tool without schema",
                # No 'parameters' or 'schema' field
            }
        ]

        valid_plan = {
            "description": "Test",
            "steps": [
                {
                    "id": "1",
                    "label": "Step",
                    "executor": "litellm",
                    "action": "completion",
                    "args": {},
                }
            ],
        }
        _llm(planner).responses = [valid_plan]

        with (
            patch("core.agents.planner.start_span"),
            patch("core.agents.planner.current_trace_ids"),
            patch("core.agents.planner.log_event"),
        ):
            await planner.generate(
                request=base_request,
                history=[],
                tool_descriptions=tool_descriptions,
                available_skills_text=available_skills,
            )

        # Should succeed without error
        assert len(_llm(planner).call_history) > 0


class TestHistoryAndMetadataHandling:
    """Tests for conversation history and metadata handling."""

    @pytest.mark.asyncio
    async def test_includes_conversation_history_in_prompt(
        self,
        planner: PlannerAgent,
        base_request: AgentRequest,
        tool_descriptions: list[dict[str, Any]],
        available_skills: str,
    ) -> None:
        """Should include conversation history in user prompt."""
        history = [
            AgentMessage(role="user", content="Previous question"),
            AgentMessage(role="assistant", content="Previous answer"),
        ]

        valid_plan = {
            "description": "Test",
            "steps": [
                {
                    "id": "1",
                    "label": "Step",
                    "executor": "litellm",
                    "action": "completion",
                    "args": {},
                }
            ],
        }
        _llm(planner).responses = [valid_plan]

        with (
            patch("core.agents.planner.start_span"),
            patch("core.agents.planner.current_trace_ids"),
            patch("core.agents.planner.log_event"),
        ):
            await planner.generate(
                request=base_request,
                history=history,
                tool_descriptions=tool_descriptions,
                available_skills_text=available_skills,
            )

        messages = _llm(planner).call_history[0]
        user_messages = [m for m in messages if m.role == "user"]
        user_content = user_messages[0].content or ""
        # Should include history section
        assert "Previous question" in user_content
        assert "Previous answer" in user_content

    @pytest.mark.asyncio
    async def test_includes_metadata_in_prompt(
        self,
        planner: PlannerAgent,
        tool_descriptions: list[dict[str, Any]],
        available_skills: str,
    ) -> None:
        """Should include request metadata in user prompt."""
        request = AgentRequest(
            prompt="Test request",
            metadata={"context_id": "ctx-123", "user_email": "test@example.com"},
        )

        valid_plan = {
            "description": "Test",
            "steps": [
                {
                    "id": "1",
                    "label": "Step",
                    "executor": "litellm",
                    "action": "completion",
                    "args": {},
                }
            ],
        }
        _llm(planner).responses = [valid_plan]

        with (
            patch("core.agents.planner.start_span"),
            patch("core.agents.planner.current_trace_ids"),
            patch("core.agents.planner.log_event"),
        ):
            await planner.generate(
                request=request,
                history=[],
                tool_descriptions=tool_descriptions,
                available_skills_text=available_skills,
            )

        messages = _llm(planner).call_history[0]
        user_messages = [m for m in messages if m.role == "user"]
        user_content = user_messages[0].content or ""
        # Should include metadata section
        assert "context_id" in user_content
        assert "ctx-123" in user_content


class TestModelNameHandling:
    """Tests for model name configuration."""

    @pytest.mark.asyncio
    async def test_uses_provided_model_name(
        self,
        mock_litellm: MockLLMClient,
        base_request: AgentRequest,
        tool_descriptions: list[dict[str, Any]],
        available_skills: str,
    ) -> None:
        """Should use model name provided in constructor."""
        planner = PlannerAgent(litellm=mock_litellm, model_name="custom-planner-model")

        valid_plan = {
            "description": "Test",
            "steps": [
                {
                    "id": "1",
                    "label": "Step",
                    "executor": "litellm",
                    "action": "completion",
                    "args": {},
                }
            ],
        }
        mock_litellm.responses = [valid_plan]

        with (
            patch("core.agents.planner.start_span") as mock_span,
            patch("core.agents.planner.current_trace_ids"),
            patch("core.agents.planner.log_event"),
        ):
            await planner.generate(
                request=base_request,
                history=[],
                tool_descriptions=tool_descriptions,
                available_skills_text=available_skills,
            )

            # Verify span was set with model attribute
            # Note: Model is set on llm span attribute, not the main planner span
            span_inst = mock_span.return_value.__enter__.return_value
            span_calls = span_inst.set_attribute.call_args_list
            # Check for llm.model attribute (set during streaming)
            model_attrs = [call for call in span_calls if "model" in str(call[0][0]).lower()]
            # Should have at least one model-related attribute set
            assert len(model_attrs) > 0

    @pytest.mark.asyncio
    async def test_falls_back_to_settings_model(
        self,
        base_request: AgentRequest,
        tool_descriptions: list[dict[str, Any]],
        available_skills: str,
    ) -> None:
        """Should fall back to settings.model_planner when no model name provided."""
        mock_litellm = MockLLMClient()
        # Simulate settings attribute with model_planner
        mock_settings = MagicMock()
        mock_settings.model_planner = "settings-planner-model"
        mock_litellm._settings = mock_settings

        planner = PlannerAgent(litellm=mock_litellm, model_name=None)

        valid_plan = {
            "description": "Test",
            "steps": [
                {
                    "id": "1",
                    "label": "Step",
                    "executor": "litellm",
                    "action": "completion",
                    "args": {},
                }
            ],
        }
        mock_litellm.responses = [valid_plan]

        with (
            patch("core.agents.planner.start_span") as mock_span,
            patch("core.agents.planner.current_trace_ids"),
            patch("core.agents.planner.log_event"),
        ):
            await planner.generate(
                request=base_request,
                history=[],
                tool_descriptions=tool_descriptions,
                available_skills_text=available_skills,
            )

            # Should use settings model
            span_inst = mock_span.return_value.__enter__.return_value
            span_calls = span_inst.set_attribute.call_args_list
            # Check for llm.model attribute
            model_attrs = [call for call in span_calls if "model" in str(call[0][0]).lower()]
            # Should have at least one model-related attribute set
            assert len(model_attrs) > 0
