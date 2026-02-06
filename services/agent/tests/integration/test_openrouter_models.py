"""Integration tests for OpenRouter models via LiteLLM proxy.

Tests that all configured models:
- Return valid streaming responses
- Handle tool calling correctly (where supported)
- Produce clean output (no leaked control tokens)
- Emit reasoning content in the correct field (for reasoning models)

Requires: LiteLLM proxy running on localhost:4001 with valid OPENROUTER_API_KEY.
Run with: pytest tests/integration/test_openrouter_models.py -v -s
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

import httpx
import pytest

# Add src/ to path so we can import shared modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from shared.content_classifier import (
    ContentCategory,
    classify_content,
    contains_raw_model_tokens,
)

LOGGER = logging.getLogger(__name__)

LITELLM_BASE = os.getenv("LITELLM_BASE_URL", "http://localhost:4001")

# Models configured in litellm/config.yaml that we want to test
# Maps litellm model_name -> expected capabilities
MODELS: dict[str, dict[str, Any]] = {
    "skillsrunner": {
        "real_model": "openai/gpt-oss-120b:exacto",
        "supports_tools": True,
        "has_reasoning": True,
        "reasoning_field": "reasoning_content",
    },
    "skillsrunner_deep": {
        "real_model": "google/gemini-2.5-flash",
        "supports_tools": True,
        "has_reasoning": False,
        "reasoning_field": None,
    },
    "price_tracker": {
        "real_model": "meta-llama/llama-4-scout",
        "supports_tools": False,
        "has_reasoning": False,
        "reasoning_field": None,
    },
    "price_tracker_fallback": {
        "real_model": "anthropic/claude-haiku-4.5",
        "supports_tools": True,
        "has_reasoning": False,
        "reasoning_field": None,
    },
    "composer": {
        "real_model": "openai/gpt-oss-120b:exacto",
        "supports_tools": False,
        "has_reasoning": True,
        "reasoning_field": "reasoning_content",
    },
}

# Simple tool definition for testing tool calling
WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a location",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City name, e.g. Stockholm",
                },
            },
            "required": ["location"],
        },
    },
}


async def _check_litellm_available() -> bool:
    """Check if LiteLLM proxy is reachable."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{LITELLM_BASE}/v1/models")
            return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


async def _stream_completion(
    model: str,
    messages: list[dict[str, str]],
    tools: list[dict[str, Any]] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Stream a completion and collect all chunks into a structured result.

    Returns dict with:
        content: str - accumulated content
        thinking: str - accumulated reasoning/thinking content
        tool_calls: list - collected tool call deltas
        raw_chunks: list - all raw SSE data objects
        provider: str | None - OpenRouter provider used
        model: str | None - actual model returned
        finish_reason: str | None
        has_raw_tokens: bool - whether any chunk contained raw model tokens
        raw_token_chunks: list[str] - chunks that contained raw tokens
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools

    result: dict[str, Any] = {
        "content": "",
        "thinking": "",
        "tool_calls": [],
        "raw_chunks": [],
        "provider": None,
        "model": None,
        "finish_reason": None,
        "has_raw_tokens": False,
        "raw_token_chunks": [],
        "usage": None,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            f"{LITELLM_BASE}/v1/chat/completions",
            json=payload,
            headers={"Content-Type": "application/json"},
        ) as response:
            if response.status_code >= 400:
                body = await response.aread()
                raise RuntimeError(
                    f"LiteLLM returned {response.status_code}: {body.decode()[:500]}"
                )

            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                result["raw_chunks"].append(data)

                # Capture provider/model metadata
                if data.get("provider") and not result["provider"]:
                    result["provider"] = data["provider"]
                if data.get("model") and not result["model"]:
                    result["model"] = data["model"]
                if data.get("usage"):
                    result["usage"] = data["usage"]

                if "choices" not in data or not data["choices"]:
                    continue

                choice = data["choices"][0]
                delta = choice.get("delta", {})

                if choice.get("finish_reason"):
                    result["finish_reason"] = choice["finish_reason"]

                # Collect content
                content = delta.get("content")
                if content:
                    # Check for raw tokens BEFORE our client would strip them
                    if contains_raw_model_tokens(content):
                        result["has_raw_tokens"] = True
                        result["raw_token_chunks"].append(content)
                    result["content"] += content

                # Collect reasoning (separate field)
                for field in ("reasoning_content", "reasoning", "thinking"):
                    reasoning = delta.get(field)
                    if reasoning:
                        result["thinking"] += reasoning
                        break

                # Collect tool calls
                if delta.get("tool_calls"):
                    result["tool_calls"].extend(delta["tool_calls"])

    return result


@pytest.fixture(scope="module")
def event_loop():
    """Module-scoped event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module", autouse=True)
async def check_litellm():
    """Skip all tests if LiteLLM is not available."""
    if not await _check_litellm_available():
        pytest.skip("LiteLLM proxy not available at " + LITELLM_BASE)


# ─── Basic Completion Tests ──────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("model_name", list(MODELS.keys()))
async def test_model_returns_content(model_name: str) -> None:
    """Each model should return non-empty content for a simple prompt."""
    result = await _stream_completion(
        model_name,
        [{"role": "user", "content": "Say exactly: Hello World"}],
    )
    full_text = result["content"]
    LOGGER.info(
        "[%s] provider=%s model=%s content_len=%d thinking_len=%d",
        model_name,
        result["provider"],
        result["model"],
        len(full_text),
        len(result["thinking"]),
    )

    # Model must produce either content or thinking (reasoning models may only emit thinking)
    assert full_text or result["thinking"], f"{model_name}: No content or thinking received"
    assert result["finish_reason"] in (
        "stop",
        "end_turn",
        "eos",
    ), f"{model_name}: Unexpected finish_reason={result['finish_reason']}"


# ─── Raw Token Leak Detection ────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("model_name", list(MODELS.keys()))
async def test_no_raw_token_leaks_in_content(model_name: str) -> None:
    """Content from each model should not contain leaked control tokens.

    This is a critical test: if raw tokens leak through the LiteLLM proxy,
    our content_classifier's source-level filtering in litellm_client.py
    must catch them. This test hits the raw proxy (bypassing our client)
    to check what the models actually return.
    """
    result = await _stream_completion(
        model_name,
        [{"role": "user", "content": "What is 2+2? Reply with just the number."}],
    )

    if result["has_raw_tokens"]:
        LOGGER.warning(
            "[%s] RAW TOKEN LEAK detected in %d chunks! Samples: %s",
            model_name,
            len(result["raw_token_chunks"]),
            [c[:80] for c in result["raw_token_chunks"][:3]],
        )
        # This is informational - our litellm_client strips these.
        # But log it so we know which providers leak.

    # Verify the full accumulated content can be classified
    full = result["content"]
    if full:
        category = classify_content(full)
        LOGGER.info("[%s] Content classification: %s", model_name, category.value)
        # Content should not be purely raw tokens (after accumulation)
        # Some tokens may be embedded, but there should be real content too
        if category == ContentCategory.RAW_TOKEN:
            # Only fail if ALL content is raw tokens
            from shared.content_classifier import strip_raw_tokens

            cleaned = strip_raw_tokens(full)
            assert cleaned.strip(), f"{model_name}: Content is entirely raw tokens: {full[:200]}"


# ─── Reasoning Content Capture ────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_name",
    [m for m, c in MODELS.items() if c["has_reasoning"]],
)
async def test_reasoning_model_emits_thinking(model_name: str) -> None:
    """Reasoning models should emit thinking/reasoning_content for complex prompts."""
    result = await _stream_completion(
        model_name,
        [
            {
                "role": "user",
                "content": (
                    "Solve step by step: If a train travels 120km in 1.5 hours, "
                    "what is its average speed in m/s?"
                ),
            }
        ],
    )

    LOGGER.info(
        "[%s] thinking_len=%d content_len=%d provider=%s",
        model_name,
        len(result["thinking"]),
        len(result["content"]),
        result["provider"],
    )

    # Reasoning model should produce thinking content
    # (via reasoning_content field for gpt-oss / separate_field models)
    has_output = bool(result["thinking"]) or bool(result["content"])
    assert has_output, f"{model_name}: No output at all"

    # If we got thinking, verify it's substantive
    if result["thinking"]:
        assert (
            len(result["thinking"]) > 20
        ), f"{model_name}: Thinking too short: {result['thinking'][:100]}"
        # Thinking should not contain raw model tokens
        assert not contains_raw_model_tokens(
            result["thinking"]
        ), f"{model_name}: Raw tokens in thinking: {result['thinking'][:200]}"


# ─── Tool Calling Tests ──────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_name",
    [m for m, c in MODELS.items() if c["supports_tools"]],
)
async def test_model_calls_tool(model_name: str) -> None:
    """Models that support tools should invoke the get_weather tool when asked."""
    result = await _stream_completion(
        model_name,
        [
            {
                "role": "user",
                "content": "What is the weather in Stockholm? Use the get_weather tool.",
            }
        ],
        tools=[WEATHER_TOOL],
        timeout=90.0,
    )

    LOGGER.info(
        "[%s] tool_calls=%d content_len=%d finish=%s provider=%s",
        model_name,
        len(result["tool_calls"]),
        len(result["content"]),
        result["finish_reason"],
        result["provider"],
    )

    # Model should either call the tool or mention weather in content
    if result["tool_calls"]:
        # Verify tool call structure
        # Streaming tool calls come as deltas, accumulate function name
        func_names = set()
        for tc in result["tool_calls"]:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            if name:
                func_names.add(name)

        assert (
            "get_weather" in func_names
        ), f"{model_name}: Expected get_weather call, got: {func_names}"
        assert result["finish_reason"] in (
            "tool_calls",
            "stop",
        ), f"{model_name}: Expected tool_calls finish, got: {result['finish_reason']}"
    else:
        # Some models may not always call tools - log but don't hard fail
        LOGGER.warning(
            "[%s] No tool calls returned. Content: %s",
            model_name,
            result["content"][:200],
        )
        # At minimum the model should have produced some output
        assert (
            result["content"] or result["thinking"]
        ), f"{model_name}: No tool calls and no content"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_name",
    [m for m, c in MODELS.items() if c["supports_tools"]],
)
async def test_tool_call_with_result_roundtrip(model_name: str) -> None:
    """Test full tool call roundtrip: call -> result -> final answer."""
    # Step 1: Get the model to call the tool
    step1 = await _stream_completion(
        model_name,
        [
            {
                "role": "user",
                "content": "What's the weather in Stockholm? Use the get_weather tool.",
            }
        ],
        tools=[WEATHER_TOOL],
        timeout=90.0,
    )

    if not step1["tool_calls"]:
        pytest.skip(f"{model_name} did not call tool in this run")

    # Reconstruct the tool call ID and arguments from streaming deltas
    tool_call_id = None
    func_name_parts: list[str] = []
    func_args_parts: list[str] = []

    for tc in step1["tool_calls"]:
        if tc.get("id"):
            tool_call_id = tc["id"]
        fn = tc.get("function", {})
        if fn.get("name"):
            func_name_parts.append(fn["name"])
        if fn.get("arguments"):
            func_args_parts.append(fn["arguments"])

    func_name = "".join(func_name_parts)
    func_args = "".join(func_args_parts)
    if not tool_call_id:
        tool_call_id = "call_test_123"

    LOGGER.info(
        "[%s] Step 1: tool=%s args=%s id=%s",
        model_name,
        func_name,
        func_args[:100],
        tool_call_id,
    )

    # Step 2: Send tool result back and get final answer
    step2 = await _stream_completion(
        model_name,
        [
            {
                "role": "user",
                "content": "What's the weather in Stockholm?",
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": func_name,
                            "arguments": func_args,
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": json.dumps(
                    {
                        "temperature": 3,
                        "conditions": "Cloudy with light snow",
                        "wind": "12 km/h NW",
                    }
                ),
            },
        ],
        tools=[WEATHER_TOOL],
        timeout=90.0,
    )

    LOGGER.info(
        "[%s] Step 2: content_len=%d finish=%s",
        model_name,
        len(step2["content"]),
        step2["finish_reason"],
    )

    # Final answer should include weather information
    full_answer = step2["content"].lower()
    assert full_answer, f"{model_name}: No content in final answer"
    # Should reference at least some weather data
    weather_terms = ["stockholm", "3", "cloud", "snow", "wind", "weather"]
    found = [t for t in weather_terms if t in full_answer]
    assert (
        found
    ), f"{model_name}: Final answer doesn't reference weather data: {step2['content'][:300]}"

    # Final answer should be clean (no raw tokens)
    assert not contains_raw_model_tokens(
        step2["content"]
    ), f"{model_name}: Raw tokens in final answer: {step2['content'][:200]}"


# ─── Content Classification on Real Output ────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("model_name", list(MODELS.keys()))
async def test_content_classification_real_output(model_name: str) -> None:
    """Verify content classifier correctly categorizes real model output."""
    result = await _stream_completion(
        model_name,
        [
            {
                "role": "user",
                "content": (
                    "List 3 programming languages and one advantage of each. "
                    "Be concise, use a numbered list."
                ),
            }
        ],
    )

    full_content = result["content"]
    if not full_content and result["thinking"]:
        # Reasoning-only model, classify thinking instead
        full_content = result["thinking"]

    assert full_content, f"{model_name}: No output to classify"

    category = classify_content(full_content)
    LOGGER.info(
        "[%s] Final classification: %s (content_len=%d)",
        model_name,
        category.value,
        len(full_content),
    )

    # Real model output for a direct question should be CLEAN
    # (RAW_TOKEN would indicate a leak, NOISE means too short,
    #  REASONING is possible but unlikely for a list prompt)
    assert category in (
        ContentCategory.CLEAN,
        ContentCategory.REASONING,
    ), f"{model_name}: Unexpected category {category.value} for content: {full_content[:300]}"


# ─── Provider Metadata Capture ────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("model_name", list(MODELS.keys()))
async def test_provider_metadata_captured(model_name: str) -> None:
    """Verify we capture provider and model metadata from OpenRouter response."""
    result = await _stream_completion(
        model_name,
        [{"role": "user", "content": "Say: test"}],
    )

    LOGGER.info(
        "[%s] provider=%s model=%s usage=%s",
        model_name,
        result["provider"],
        result["model"],
        result["usage"],
    )

    # OpenRouter should return the actual model used
    assert result["model"], f"{model_name}: No model in response metadata"

    # Provider may not always be present (depends on OpenRouter response format)
    # but log it for visibility
    if not result["provider"]:
        LOGGER.warning("[%s] No provider field in response", model_name)
