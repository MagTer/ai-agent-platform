"""Tests for shared content classification functions."""

from __future__ import annotations

from shared.content_classifier import (
    ContentCategory,
    classify_content,
    contains_raw_model_tokens,
    is_noise_fragment,
    is_reasoning_content,
    strip_raw_tokens,
)


class TestContainsRawModelTokens:
    def test_detects_header_tokens(self) -> None:
        assert contains_raw_model_tokens("<|header_start|>assistant")
        assert contains_raw_model_tokens("hello<|im_end|>")

    def test_detects_think_tags(self) -> None:
        assert contains_raw_model_tokens("<think>planning step")
        assert contains_raw_model_tokens("result</think>")

    def test_detects_eot_token(self) -> None:
        assert contains_raw_model_tokens("<|eot_id|>")

    def test_passes_clean_content(self) -> None:
        assert not contains_raw_model_tokens("Hello, how can I help?")
        assert not contains_raw_model_tokens("The price is $42.50")

    def test_empty_string(self) -> None:
        assert not contains_raw_model_tokens("")


class TestStripRawTokens:
    def test_removes_embedded_tokens(self) -> None:
        result = strip_raw_tokens("Hello<|im_end|> world")
        assert result == "Hello world"

    def test_removes_multiple_tokens(self) -> None:
        result = strip_raw_tokens("<|header_start|>assistant<|header_end|>Hi")
        assert result == "assistantHi"

    def test_fully_raw_content_returns_empty(self) -> None:
        result = strip_raw_tokens("<|im_start|><|im_end|>")
        assert result == ""

    def test_clean_content_unchanged(self) -> None:
        result = strip_raw_tokens("No tokens here")
        assert result == "No tokens here"

    def test_empty_string(self) -> None:
        assert strip_raw_tokens("") == ""

    def test_think_tags_stripped(self) -> None:
        result = strip_raw_tokens("<think>reasoning</think>")
        assert result == "reasoning"


class TestIsNoiseFragment:
    def test_single_brackets(self) -> None:
        assert is_noise_fragment("[")
        assert is_noise_fragment("]")
        assert is_noise_fragment("[]")

    def test_single_braces(self) -> None:
        assert is_noise_fragment("{")
        assert is_noise_fragment("}")
        assert is_noise_fragment("{}")

    def test_single_punctuation(self) -> None:
        assert is_noise_fragment(",")
        assert is_noise_fragment(":")
        assert is_noise_fragment(".")

    def test_whitespace_padded(self) -> None:
        assert is_noise_fragment("  [  ")
        assert is_noise_fragment(" { ")

    def test_real_content_not_noise(self) -> None:
        assert not is_noise_fragment("Hello")
        assert not is_noise_fragment("The answer is 42")

    def test_empty_string(self) -> None:
        assert not is_noise_fragment("")

    def test_short_real_content(self) -> None:
        assert not is_noise_fragment("Hi")
        assert not is_noise_fragment("Yes")


class TestIsReasoningContent:
    def test_planning_phrases(self) -> None:
        assert is_reasoning_content("Let me search for that")
        assert is_reasoning_content("I'll start by looking up")
        assert is_reasoning_content("First, I need to check")

    def test_tool_call_patterns(self) -> None:
        assert is_reasoning_content('  web_search("query")')
        assert is_reasoning_content('{"tool": "search"}')

    def test_continuation_phrases(self) -> None:
        assert is_reasoning_content("Now I will search for more info")
        assert is_reasoning_content("Next, let me check the price")

    def test_normal_content_not_reasoning(self) -> None:
        assert not is_reasoning_content("The Tesla Model 3 costs $35,000")
        assert not is_reasoning_content("Here are the results:")

    def test_empty_string(self) -> None:
        assert not is_reasoning_content("")

    def test_partial_reasoning_phrases(self) -> None:
        assert is_reasoning_content("start by searching the web")
        assert is_reasoning_content("searching for the latest prices")


class TestClassifyContent:
    def test_raw_token_highest_priority(self) -> None:
        assert classify_content("<|im_start|>[") == ContentCategory.RAW_TOKEN

    def test_noise_over_reasoning(self) -> None:
        assert classify_content("[") == ContentCategory.NOISE

    def test_reasoning_detected(self) -> None:
        assert classify_content("Let me search for that") == ContentCategory.REASONING

    def test_clean_content(self) -> None:
        assert classify_content("The answer is 42") == ContentCategory.CLEAN

    def test_raw_token_with_content(self) -> None:
        assert classify_content("Hello<|im_end|>") == ContentCategory.RAW_TOKEN
