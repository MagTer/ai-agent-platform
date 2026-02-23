"""Tests for the UnifiedOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.routing.unified_orchestrator import UnifiedOrchestrator
from shared.models import AgentMessage


@pytest.fixture
def mock_litellm() -> MagicMock:
    """Create a mock LiteLLM client."""
    litellm = MagicMock()
    litellm._settings = MagicMock()
    litellm._settings.model_planner = "test-planner-model"
    litellm.generate = AsyncMock()
    return litellm


class TestDirectAnswerDetection:
    """Tests for direct answer detection (plain text responses)."""

    @pytest.mark.asyncio
    async def test_plain_text_response(self, mock_litellm: MagicMock) -> None:
        """Test that plain text without JSON is treated as direct answer."""
        mock_litellm.generate.return_value = "The answer is 42."

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("What is the meaning of life?")

        assert result.is_direct
        assert result.direct_answer == "The answer is 42."
        assert result.plan is None

    @pytest.mark.asyncio
    async def test_multiline_text_response(self, mock_litellm: MagicMock) -> None:
        """Test that multiline plain text is handled correctly."""
        response = """Bonjour is the French word for hello.

It's commonly used as a greeting in France."""
        mock_litellm.generate.return_value = response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("What is hello in French?")

        assert result.is_direct
        assert result.direct_answer is not None
        assert "Bonjour" in result.direct_answer
        assert "greeting" in result.direct_answer
        assert result.plan is None

    @pytest.mark.asyncio
    async def test_greeting_response(self, mock_litellm: MagicMock) -> None:
        """Test greeting responses are treated as direct answers."""
        mock_litellm.generate.return_value = "Hello! How can I help you today?"

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Hi!")

        assert result.is_direct
        assert result.direct_answer is not None
        assert "Hello" in result.direct_answer
        assert result.plan is None

    @pytest.mark.asyncio
    async def test_math_answer(self, mock_litellm: MagicMock) -> None:
        """Test math answers are treated as direct answers."""
        mock_litellm.generate.return_value = "105"

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("What is 15 * 7?")

        assert result.is_direct
        assert result.direct_answer == "105"
        assert result.plan is None


class TestPlanDetectionMarkdownFences:
    """Tests for plan detection with markdown code fences."""

    @pytest.mark.asyncio
    async def test_plan_with_json_fence(self, mock_litellm: MagicMock) -> None:
        """Test plan parsing with ```json fence."""
        response = """```json
{
  "description": "AI news research",
  "steps": [
    {
      "id": "1",
      "label": "Research",
      "executor": "skill",
      "action": "skill",
      "tool": "researcher",
      "args": {"goal": "Latest AI news"}
    }
  ]
}
```"""
        mock_litellm.generate.return_value = response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Research the latest AI news")

        assert not result.is_direct
        assert result.direct_answer is None
        assert result.plan is not None
        assert result.plan.description == "AI news research"
        assert len(result.plan.steps) == 1
        assert result.plan.steps[0].tool == "researcher"
        assert result.plan.steps[0].args["goal"] == "Latest AI news"

    @pytest.mark.asyncio
    async def test_plan_with_generic_fence(self, mock_litellm: MagicMock) -> None:
        """Test plan parsing with generic ``` fence."""
        response = """```
{
  "description": "Smart home control",
  "steps": [
    {
      "id": "1",
      "label": "Control lights",
      "executor": "skill",
      "action": "skill",
      "tool": "general/homey",
      "args": {"goal": "Turn off kitchen lights"}
    }
  ]
}
```"""
        mock_litellm.generate.return_value = response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Turn off the kitchen lights")

        assert not result.is_direct
        assert result.plan is not None
        assert result.plan.description == "Smart home control"
        assert len(result.plan.steps) == 1
        assert result.plan.steps[0].tool == "general/homey"

    @pytest.mark.asyncio
    async def test_plan_with_text_before_and_after_fence(self, mock_litellm: MagicMock) -> None:
        """Test plan parsing with text surrounding the JSON fence."""
        response = """Here's the plan for your request:

```json
{
  "description": "Price tracking",
  "steps": [
    {
      "id": "1",
      "label": "Track price",
      "executor": "skill",
      "action": "skill",
      "tool": "general/priser",
      "args": {"goal": "Find best price for iPhone"}
    }
  ]
}
```

I'll execute this plan now."""
        mock_litellm.generate.return_value = response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Find the best price for an iPhone")

        assert not result.is_direct
        assert result.plan is not None
        assert result.plan.description == "Price tracking"
        assert result.plan.steps[0].tool == "general/priser"


class TestPlanDetectionWithoutFences:
    """Tests for plan detection without markdown fences."""

    @pytest.mark.asyncio
    async def test_raw_json_response(self, mock_litellm: MagicMock) -> None:
        """Test parsing raw JSON without fences."""
        response = (
            '{"description": "Web research", "steps": '
            '[{"id": "1", "label": "Search", "executor": "skill", '
            '"action": "skill", "tool": "researcher", '
            '"args": {"goal": "Python tutorials"}}]}'
        )
        mock_litellm.generate.return_value = response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Find Python tutorials")

        assert not result.is_direct
        assert result.plan is not None
        assert result.plan.description == "Web research"
        assert len(result.plan.steps) == 1
        assert result.plan.steps[0].tool == "researcher"

    @pytest.mark.asyncio
    async def test_json_with_surrounding_text(self, mock_litellm: MagicMock) -> None:
        """Test JSON extraction from text without fences."""
        response = (
            'I will create a plan: {"description": "Azure DevOps query"'
            ', "steps": [{"id": "1", "label": "Read backlog", '
            '"executor": "skill", "action": "skill", '
            '"tool": "backlog_manager", '
            '"args": {"goal": "List open work items"}}]}'
            " and execute it."
        )
        mock_litellm.generate.return_value = response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("List my Azure DevOps work items")

        assert not result.is_direct
        assert result.plan is not None
        assert result.plan.description == "Azure DevOps query"
        assert result.plan.steps[0].tool == "backlog_manager"


class TestBothStepsAndPlanFormats:
    """Tests for both 'steps' and 'plan' JSON formats."""

    @pytest.mark.asyncio
    async def test_steps_format(self, mock_litellm: MagicMock) -> None:
        """Test standard 'steps' format."""
        response = """```json
{
  "description": "Multi-step plan",
  "steps": [
    {
      "id": "1",
      "label": "First step",
      "executor": "skill",
      "action": "skill",
      "tool": "researcher",
      "args": {"goal": "Research topic"}
    },
    {
      "id": "2",
      "label": "Second step",
      "executor": "skill",
      "action": "skill",
      "tool": "deep_research",
      "args": {"goal": "Deep dive"}
    }
  ]
}
```"""
        mock_litellm.generate.return_value = response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Research this topic deeply")

        assert not result.is_direct
        assert result.plan is not None
        assert len(result.plan.steps) == 2
        assert result.plan.steps[0].label == "First step"
        assert result.plan.steps[1].label == "Second step"

    @pytest.mark.asyncio
    async def test_plan_format(self, mock_litellm: MagicMock) -> None:
        """Test alternative 'plan' format (converted to 'steps')."""
        response = """```json
{
  "description": "Alternative format",
  "plan": [
    {
      "id": "1",
      "label": "Research",
      "executor": "skill",
      "action": "skill",
      "tool": "researcher",
      "args": {"goal": "Find information"}
    }
  ]
}
```"""
        mock_litellm.generate.return_value = response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Find some information")

        assert not result.is_direct
        assert result.plan is not None
        assert len(result.plan.steps) == 1
        assert result.plan.steps[0].tool == "researcher"


class TestMalformedJSONFallback:
    """Tests for malformed JSON handling."""

    @pytest.mark.asyncio
    async def test_incomplete_json(self, mock_litellm: MagicMock) -> None:
        """Test incomplete JSON is treated as direct answer."""
        response = '{"description": "Broken plan", "steps": [{'
        mock_litellm.generate.return_value = response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Do something")

        assert result.is_direct
        assert result.direct_answer == response
        assert result.plan is None

    @pytest.mark.asyncio
    async def test_invalid_json_syntax(self, mock_litellm: MagicMock) -> None:
        """Test invalid JSON syntax is treated as direct answer."""
        response = '{"description": "Bad JSON", steps: [{"id": "1"}]}'  # Missing quotes
        mock_litellm.generate.return_value = response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Test request")

        assert result.is_direct
        assert result.plan is None

    @pytest.mark.asyncio
    async def test_json_without_steps_or_plan_key(self, mock_litellm: MagicMock) -> None:
        """Test JSON without 'steps' or 'plan' key is treated as direct answer."""
        response = '{"description": "Missing steps", "other": "data"}'
        mock_litellm.generate.return_value = response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Request")

        assert result.is_direct
        assert result.plan is None

    @pytest.mark.asyncio
    async def test_empty_steps_array(self, mock_litellm: MagicMock) -> None:
        """Test JSON with empty steps array is treated as direct answer."""
        response = '{"description": "No steps", "steps": []}'
        mock_litellm.generate.return_value = response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Request")

        assert result.is_direct
        assert result.plan is None


class TestLLMExceptionFallback:
    """Tests for LLM exception handling."""

    @pytest.mark.asyncio
    async def test_llm_raises_exception_returns_fallback_plan(
        self, mock_litellm: MagicMock
    ) -> None:
        """Test that LLM exceptions return a fallback researcher plan."""
        mock_litellm.generate.side_effect = RuntimeError("LLM service unavailable")

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Find information about AI")

        assert not result.is_direct
        assert result.plan is not None
        assert result.plan.description == "Fallback plan due to orchestrator error"
        assert len(result.plan.steps) == 1
        assert result.plan.steps[0].tool == "researcher"
        assert result.plan.steps[0].args["goal"] == "Find information about AI"

    @pytest.mark.asyncio
    async def test_llm_timeout_returns_fallback_plan(self, mock_litellm: MagicMock) -> None:
        """Test that LLM timeout returns a fallback plan."""
        mock_litellm.generate.side_effect = TimeoutError("Request timed out")

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Research quantum computing")

        assert not result.is_direct
        assert result.plan is not None
        assert result.plan.steps[0].tool == "researcher"
        assert result.plan.steps[0].args["goal"] == "Research quantum computing"


class TestPlanStepStructure:
    """Tests for PlanStep field mapping."""

    @pytest.mark.asyncio
    async def test_all_fields_mapped_correctly(self, mock_litellm: MagicMock) -> None:
        """Test that all PlanStep fields are correctly mapped."""
        response = """```json
{
  "description": "Complete plan",
  "steps": [
    {
      "id": "step-1",
      "label": "First action",
      "executor": "skill",
      "action": "skill",
      "tool": "researcher",
      "args": {"goal": "Find data", "extra": "param"},
      "description": "This step searches for data"
    }
  ]
}
```"""
        mock_litellm.generate.return_value = response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Test")

        assert result.plan is not None
        step = result.plan.steps[0]
        assert step.id == "step-1"
        assert step.label == "First action"
        assert step.executor == "skill"
        assert step.action == "skill"
        assert step.tool == "researcher"
        assert step.args == {"goal": "Find data", "extra": "param"}
        assert step.description == "This step searches for data"

    @pytest.mark.asyncio
    async def test_missing_optional_fields_have_defaults(self, mock_litellm: MagicMock) -> None:
        """Test that missing optional fields get default values."""
        response = """```json
{
  "steps": [
    {
      "tool": "researcher"
    }
  ]
}
```"""
        mock_litellm.generate.return_value = response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Test")

        assert result.plan is not None
        step = result.plan.steps[0]
        assert step.id == "1"  # Default from index
        assert step.label == "Step 1"  # Default label
        assert step.executor == "skill"  # Default executor
        assert step.action == "skill"  # Default action
        assert step.tool == "researcher"
        assert step.args == {}  # Empty dict default
        assert step.description is None  # None default

    @pytest.mark.asyncio
    async def test_skill_field_fallback(self, mock_litellm: MagicMock) -> None:
        """Test that 'skill' field can be used instead of 'tool'."""
        response = """```json
{
  "description": "Uses skill field",
  "steps": [
    {
      "id": "1",
      "label": "Research",
      "executor": "skill",
      "action": "skill",
      "skill": "researcher",
      "args": {"goal": "Find info"}
    }
  ]
}
```"""
        mock_litellm.generate.return_value = response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Test")

        assert result.plan is not None
        assert result.plan.steps[0].tool == "researcher"


class TestHistoryContext:
    """Tests for conversation history handling."""

    @pytest.mark.asyncio
    async def test_history_included_in_prompt(self, mock_litellm: MagicMock) -> None:
        """Test that history messages are included in the LLM call."""
        mock_litellm.generate.return_value = (
            "Based on our previous conversation, the answer is yes."
        )

        history = [
            AgentMessage(role="user", content="What is AI?"),
            AgentMessage(role="assistant", content="AI stands for Artificial Intelligence."),
            AgentMessage(role="user", content="Is it useful?"),
        ]

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Can you remind me?", history=history)

        assert result.is_direct

        # Verify generate was called with history
        call_args = mock_litellm.generate.call_args
        messages = call_args[0][0]

        # Should have: system + 3 history + current user message
        assert len(messages) >= 4

        # Check history messages are included
        user_messages = [m for m in messages if m.role == "user"]
        assert len(user_messages) >= 2  # At least the history + current

    @pytest.mark.asyncio
    async def test_only_last_six_messages_included(self, mock_litellm: MagicMock) -> None:
        """Test that only last 6 history messages are included."""
        mock_litellm.generate.return_value = "OK"

        # Create 10 history messages
        history = []
        for i in range(10):
            history.append(AgentMessage(role="user", content=f"Message {i}"))
            history.append(AgentMessage(role="assistant", content=f"Response {i}"))

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Latest question", history=history)

        assert result.is_direct

        # Verify generate was called
        call_args = mock_litellm.generate.call_args
        messages = call_args[0][0]

        # Should have: system + last 6 history + current user
        # = 1 system + 6 history + 1 current = 8 total
        assert len(messages) == 8

        # Verify the last 6 history messages are included (not the first ones)
        history_messages = messages[1:7]  # Skip system, get next 6
        assert history_messages[0].content == "Message 7"  # Last 6 start from message 7

    @pytest.mark.asyncio
    async def test_no_history_works(self, mock_litellm: MagicMock) -> None:
        """Test that orchestrator works without history."""
        mock_litellm.generate.return_value = "Answer"

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Question")

        assert result.is_direct

        # Should have system + current user message only
        call_args = mock_litellm.generate.call_args
        messages = call_args[0][0]
        assert len(messages) == 2  # system + user


class TestModelSelection:
    """Tests for model selection."""

    @pytest.mark.asyncio
    async def test_uses_provided_model_name(self, mock_litellm: MagicMock) -> None:
        """Test that provided model name is used."""
        mock_litellm.generate.return_value = "Answer"

        orchestrator = UnifiedOrchestrator(mock_litellm, model_name="custom-model")
        await orchestrator.process("Question")

        # Verify generate was called with correct model
        call_args = mock_litellm.generate.call_args
        assert call_args[1]["model"] == "custom-model"

    @pytest.mark.asyncio
    async def test_uses_settings_model_when_none_provided(self, mock_litellm: MagicMock) -> None:
        """Test that settings model is used when no model name provided."""
        mock_litellm.generate.return_value = "Answer"

        orchestrator = UnifiedOrchestrator(mock_litellm)
        await orchestrator.process("Question")

        # Verify generate was called with model from settings
        call_args = mock_litellm.generate.call_args
        assert call_args[1]["model"] == "test-planner-model"


class TestAvailableSkillsText:
    """Tests for available_skills_text parameter."""

    @pytest.mark.asyncio
    async def test_skills_text_included_in_system_prompt(self, mock_litellm: MagicMock) -> None:
        """Test that available_skills_text is included in system prompt."""
        mock_litellm.generate.return_value = "Answer"

        skills_text = """Available skills:
- researcher: Web research
- homey: Smart home control"""

        orchestrator = UnifiedOrchestrator(mock_litellm)
        await orchestrator.process("Question", available_skills_text=skills_text)

        # Verify system prompt includes skills text
        call_args = mock_litellm.generate.call_args
        messages = call_args[0][0]
        system_message = messages[0]

        assert system_message.role == "system"
        assert "researcher: Web research" in system_message.content
        assert "homey: Smart home control" in system_message.content


class TestPlanParsingRobustness:
    """Tests for plan parsing error handling and edge cases."""

    @pytest.mark.asyncio
    async def test_preserves_step_order(self, mock_litellm: MagicMock) -> None:
        """Test that steps maintain their order from JSON."""
        response = """```json
{
  "description": "Multi-step ordered plan",
  "steps": [
    {"id": "3", "label": "Third", "tool": "researcher"},
    {"id": "1", "label": "First", "tool": "homey"},
    {"id": "2", "label": "Second", "tool": "deep_research"}
  ]
}
```"""
        mock_litellm.generate.return_value = response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Test order preservation")

        assert result.plan is not None
        assert len(result.plan.steps) == 3
        # Order should match JSON array order, not ID order
        assert result.plan.steps[0].id == "3"
        assert result.plan.steps[0].label == "Third"
        assert result.plan.steps[1].id == "1"
        assert result.plan.steps[1].label == "First"
        assert result.plan.steps[2].id == "2"
        assert result.plan.steps[2].label == "Second"

    @pytest.mark.asyncio
    async def test_non_dict_step_items_skipped(self, mock_litellm: MagicMock) -> None:
        """Test that non-dict items in steps array are skipped."""
        response = """```json
{
  "description": "Plan with invalid step items",
  "steps": [
    {"id": "1", "label": "Valid step", "tool": "researcher"},
    "invalid string step",
    null,
    42,
    {"id": "2", "label": "Another valid step", "tool": "homey"}
  ]
}
```"""
        mock_litellm.generate.return_value = response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Test invalid step items")

        assert result.plan is not None
        # Should only have the 2 valid dict steps
        assert len(result.plan.steps) == 2
        assert result.plan.steps[0].label == "Valid step"
        assert result.plan.steps[1].label == "Another valid step"

    @pytest.mark.asyncio
    async def test_all_non_dict_steps_returns_none(self, mock_litellm: MagicMock) -> None:
        """Test that plan with only non-dict steps is treated as direct answer."""
        response = """```json
{
  "description": "Invalid steps",
  "steps": ["string", 42, null, true]
}
```"""
        mock_litellm.generate.return_value = response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Test all invalid steps")

        # Should be treated as direct answer since no valid steps
        assert result.is_direct
        assert result.plan is None

    @pytest.mark.asyncio
    async def test_missing_tool_and_skill_fields(self, mock_litellm: MagicMock) -> None:
        """Test step with neither 'tool' nor 'skill' field gets None."""
        response = """```json
{
  "description": "Step without tool reference",
  "steps": [
    {"id": "1", "label": "No tool", "executor": "skill", "action": "skill"}
  ]
}
```"""
        mock_litellm.generate.return_value = response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Test missing tool")

        assert result.plan is not None
        assert len(result.plan.steps) == 1
        # Tool should be None when neither field exists
        assert result.plan.steps[0].tool is None

    @pytest.mark.asyncio
    async def test_steps_not_a_list(self, mock_litellm: MagicMock) -> None:
        """Test that 'steps' as non-list is treated as direct answer."""
        response = """```json
{
  "description": "Steps is not a list",
  "steps": {"id": "1", "label": "Single step as object"}
}
```"""
        mock_litellm.generate.return_value = response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Test steps not list")

        assert result.is_direct
        assert result.plan is None

    @pytest.mark.asyncio
    async def test_fallback_plan_structure_is_valid(self, mock_litellm: MagicMock) -> None:
        """Test that fallback plan on error has valid structure."""
        mock_litellm.generate.side_effect = RuntimeError("Test error")

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Original user request")

        assert result.plan is not None
        # Verify fallback plan structure
        assert result.plan.description == "Fallback plan due to orchestrator error"
        assert len(result.plan.steps) == 1

        step = result.plan.steps[0]
        assert step.id == "1"
        assert step.label == "Research"
        assert step.executor == "skill"
        assert step.action == "skill"
        assert step.tool == "researcher"
        assert step.args == {"goal": "Original user request"}


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_empty_response(self, mock_litellm: MagicMock) -> None:
        """Test empty response is treated as direct answer."""
        mock_litellm.generate.return_value = ""

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Question")

        assert result.is_direct
        assert result.direct_answer == ""

    @pytest.mark.asyncio
    async def test_whitespace_only_response(self, mock_litellm: MagicMock) -> None:
        """Test whitespace-only response is treated as direct answer."""
        mock_litellm.generate.return_value = "   \n\n   "

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Question")

        assert result.is_direct
        assert result.direct_answer == ""  # Stripped

    @pytest.mark.asyncio
    async def test_response_with_only_braces(self, mock_litellm: MagicMock) -> None:
        """Test response with only braces (not valid JSON) is direct answer."""
        mock_litellm.generate.return_value = "{}"

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Question")

        assert result.is_direct

    @pytest.mark.asyncio
    async def test_very_long_response(self, mock_litellm: MagicMock) -> None:
        """Test very long direct answer is handled correctly."""
        long_response = "A" * 10000
        mock_litellm.generate.return_value = long_response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Tell me a long story")

        assert result.is_direct
        assert result.direct_answer is not None
        assert len(result.direct_answer) == 10000

    @pytest.mark.asyncio
    async def test_plan_with_unicode_characters(self, mock_litellm: MagicMock) -> None:
        """Test plan with Unicode characters is parsed correctly."""
        response = """```json
{
  "description": "Søk på norsk 🇳🇴",
  "steps": [
    {
      "id": "1",
      "label": "Søk",
      "executor": "skill",
      "action": "skill",
      "tool": "researcher",
      "args": {"goal": "Finn informasjon på norsk"}
    }
  ]
}
```"""
        mock_litellm.generate.return_value = response

        orchestrator = UnifiedOrchestrator(mock_litellm)
        result = await orchestrator.process("Søk etter noe")

        assert not result.is_direct
        assert result.plan is not None
        assert result.plan.description is not None
        assert "norsk" in result.plan.description
        assert result.plan.steps[0].args["goal"] == "Finn informasjon på norsk"
