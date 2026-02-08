"""Tests for ChunkFilter shared chunk filtering."""

from shared.chunk_filter import ChunkFilter
from shared.streaming import VerbosityLevel


class TestShouldShowDefault:
    """Tests for should_show() in DEFAULT verbosity."""

    def setup_method(self) -> None:
        self.f = ChunkFilter(VerbosityLevel.DEFAULT)

    def test_content_shown(self) -> None:
        assert self.f.should_show("content") is True

    def test_error_shown(self) -> None:
        assert self.f.should_show("error") is True

    def test_trace_info_shown(self) -> None:
        assert self.f.should_show("trace_info") is True

    def test_awaiting_input_shown(self) -> None:
        assert self.f.should_show("awaiting_input") is True

    def test_tool_start_hidden(self) -> None:
        assert self.f.should_show("tool_start") is False

    def test_tool_output_hidden(self) -> None:
        assert self.f.should_show("tool_output") is False

    def test_skill_activity_hidden(self) -> None:
        assert self.f.should_show("skill_activity") is False

    def test_thinking_planner_shown(self) -> None:
        assert self.f.should_show("thinking", metadata={"role": "Planner"}) is True

    def test_thinking_reasoning_model_hidden(self) -> None:
        assert self.f.should_show("thinking", metadata={"source": "reasoning_model"}) is False

    def test_thinking_skill_internal_hidden(self) -> None:
        assert self.f.should_show("thinking", metadata={"source": "skill_internal"}) is False

    def test_thinking_supervisor_replan_shown(self) -> None:
        assert (
            self.f.should_show("thinking", metadata={"role": "Supervisor"}, content="Replan needed")
            is True
        )

    def test_thinking_supervisor_non_replan_hidden(self) -> None:
        assert (
            self.f.should_show("thinking", metadata={"role": "Supervisor"}, content="Plan approved")
            is False
        )

    def test_thinking_plan_orchestration_shown(self) -> None:
        assert self.f.should_show("thinking", metadata={"orchestration": "plan"}) is True

    def test_thinking_plan_type_shown(self) -> None:
        assert self.f.should_show("thinking", metadata={"type": "plan"}) is True

    def test_thinking_no_role_hidden(self) -> None:
        assert self.f.should_show("thinking", metadata={}) is False

    def test_step_start_skill_shown(self) -> None:
        assert self.f.should_show("step_start", metadata={"executor": "skill"}) is True

    def test_step_start_skill_action_shown(self) -> None:
        assert self.f.should_show("step_start", metadata={"action": "skill"}) is True

    def test_step_start_other_hidden(self) -> None:
        assert self.f.should_show("step_start", metadata={"action": "tool"}) is False


class TestShouldShowVerbose:
    """Tests for should_show() in VERBOSE mode."""

    def test_all_types_shown(self) -> None:
        f = ChunkFilter(VerbosityLevel.VERBOSE)
        for chunk_type in (
            "content",
            "error",
            "tool_start",
            "tool_output",
            "thinking",
            "step_start",
            "skill_activity",
        ):
            assert f.should_show(chunk_type) is True


class TestShouldShowDebug:
    """Tests for should_show() in DEBUG mode."""

    def test_all_types_shown(self) -> None:
        f = ChunkFilter(VerbosityLevel.DEBUG)
        for chunk_type in (
            "content",
            "error",
            "tool_start",
            "tool_output",
            "thinking",
            "step_start",
            "skill_activity",
        ):
            assert f.should_show(chunk_type) is True


class TestIsSafeContent:
    """Tests for is_safe_content()."""

    def test_clean_content_passes(self) -> None:
        f = ChunkFilter(VerbosityLevel.DEFAULT)
        assert f.is_safe_content("Hello, how can I help you?") is True

    def test_raw_model_tokens_rejected(self) -> None:
        f = ChunkFilter(VerbosityLevel.DEFAULT)
        assert f.is_safe_content("<|im_start|>assistant") is False

    def test_raw_model_tokens_rejected_in_verbose(self) -> None:
        f = ChunkFilter(VerbosityLevel.VERBOSE)
        assert f.is_safe_content("<|im_start|>assistant") is False

    def test_noise_fragment_rejected_in_default(self) -> None:
        f = ChunkFilter(VerbosityLevel.DEFAULT)
        assert f.is_safe_content("[") is False

    def test_noise_fragment_allowed_in_verbose(self) -> None:
        f = ChunkFilter(VerbosityLevel.VERBOSE)
        assert f.is_safe_content("[") is True

    def test_empty_brackets_rejected_in_default(self) -> None:
        f = ChunkFilter(VerbosityLevel.DEFAULT)
        assert f.is_safe_content("[]") is False

    def test_think_token_rejected(self) -> None:
        f = ChunkFilter(VerbosityLevel.DEFAULT)
        assert f.is_safe_content("Let me <think> about this") is False


class TestIsDuplicatePlan:
    """Tests for plan dedup."""

    def test_first_plan_not_duplicate(self) -> None:
        f = ChunkFilter()
        assert f.is_duplicate_plan("Plan: Do something important") is False

    def test_same_plan_is_duplicate(self) -> None:
        f = ChunkFilter()
        f.is_duplicate_plan("Plan: Do something important")
        assert f.is_duplicate_plan("Plan: Do something important") is True

    def test_different_plans_not_duplicate(self) -> None:
        f = ChunkFilter()
        f.is_duplicate_plan("Plan: First plan")
        assert f.is_duplicate_plan("Plan: Second plan") is False
