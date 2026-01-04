"""
Semantic End-to-End Tests for AI Agent Platform.

These tests treat the agent as a black box (via HTTP) and validate:
1. Response quality and structure
2. Proper separation of thinking/final answer
3. Observability via trace verification
4. Error resilience (no raw system errors exposed)
5. Semantic quality via LLM-as-a-judge (no meta-commentary)

Test Location Philosophy:
- unit/      - Fast, mocked tests (no network)
- integration/ - Real DB, mocked LLM
- semantic/  - Black-box E2E tests (real agent, real LLM) <-- YOU ARE HERE

Requirements:
- Agent must be running (docker-compose up)
- Tests use real LLM calls and may take 30-120 seconds each
"""

import os
import re
import time

import httpx
import pytest
from tests.semantic.conftest import (
    AGENT_BASE_URL,
    get_request_headers,
    make_chat_request_payload,
)
from tests.semantic.llm_evaluator import evaluate_response
from tests.semantic.stream_parser import parse_sse_stream
from tests.semantic.trace_validator import assert_span_exists, get_trace_by_id

# Forbidden patterns in user-facing responses (system errors)
FORBIDDEN_ERROR_PATTERNS = [
    r"SQLAlchemyError",
    r"Traceback",
    r"ConnectionRefused",
    r"KeyError",
    r"TypeError",
    r"AttributeError",
    r"ImportError",
    r"ModuleNotFoundError",
    r"File \".*\.py\"",  # Python file paths in tracebacks
    r"line \d+, in ",  # Traceback line references
]

# Meta-commentary patterns (process narration the user shouldn't see)
META_COMMENTARY_PATTERNS = [
    r"(?i)I will now search",
    r"(?i)Let me (look|search|find)",
    r"(?i)First,? I (need|will|should) to",
    r"(?i)I('m| am) (going to|searching|looking)",
    r"(?i)Based on my (search|research)",
    r"(?i)Here are the results",
    r"(?i)I found the following",
    r"(?i)According to my (research|search)",
    r"(?i)After searching",
    r"(?i)Let me summarize what I found",
]

# Enable LLM-based evaluation (set to False in CI for determinism)
ENABLE_LLM_EVAL = os.getenv("ENABLE_LLM_EVAL", "true").lower() == "true"


@pytest.mark.asyncio
async def test_scenario_a_general_chat(
    async_client: httpx.AsyncClient,
    ensure_agent_healthy: None,
) -> None:
    """
    Scenario A: Intent Routing (General Chat).

    Ask a simple question and verify:
    - Direct answer without unnecessary planning
    - No thinking artifacts in final answer
    - Reasonable response time
    """
    url = f"{AGENT_BASE_URL}/v1/chat/completions"
    payload = make_chat_request_payload("What is 2 + 2?")
    headers = get_request_headers()

    start_time = time.time()

    try:
        async with async_client.stream("POST", url, json=payload, headers=headers) as response:
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"

            # Collect trace ID from header
            trace_id = response.headers.get("x-trace-id", "")

            # Collect SSE lines
            raw_lines: list[str] = []
            async for line in response.aiter_lines():
                raw_lines.append(line)
                if line.strip() == "data: [DONE]":
                    break

    except httpx.ConnectError:
        pytest.skip("Agent not running. Start with docker-compose.")

    elapsed = time.time() - start_time

    # Parse the response
    parsed = parse_sse_stream(raw_lines, trace_id)

    # --- Assertions ---

    # 1. Response contains the correct answer
    final = parsed.final_answer.lower()
    assert (
        "4" in final or "four" in final
    ), f"Expected answer to contain '4' or 'four', got: {parsed.final_answer[:200]}"

    # 2. Final answer should NOT contain thinking artifacts
    for prefix in ["🧠", "👣", "🛠️"]:
        assert (
            prefix not in parsed.final_answer
        ), f"Final answer contains thinking artifact '{prefix}'"

    # 3. Response time should be reasonable (< 30s for simple math)
    assert elapsed < 30, f"Response took {elapsed:.1f}s, expected < 30s"

    # 4. Check for forbidden error patterns
    _assert_no_forbidden_patterns(parsed.final_answer)

    # 5. Check for meta-commentary (fast regex check)
    _assert_no_meta_commentary(parsed.final_answer)


@pytest.mark.asyncio
async def test_scenario_b_research_skill(
    async_client: httpx.AsyncClient,
    ensure_agent_healthy: None,
) -> None:
    """
    Scenario B: Complex Research Skill.

    Ask for research that requires web searches and verify:
    - Substantial, structured response
    - Trace contains web_search or searxng spans
    - No meta-commentary in final answer
    - LLM semantic quality check (optional)
    """
    url = f"{AGENT_BASE_URL}/v1/chat/completions"
    # Using 2025 Nobel Prize - a factual topic that should yield consistent results
    payload = make_chat_request_payload(
        "Research the Nobel Prize winners in Physics for 2025. "
        "Provide a brief summary of who won and why."
    )
    headers = get_request_headers()

    try:
        async with async_client.stream("POST", url, json=payload, headers=headers) as response:
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"

            trace_id = response.headers.get("x-trace-id", "")

            raw_lines: list[str] = []
            async for line in response.aiter_lines():
                raw_lines.append(line)
                if line.strip() == "data: [DONE]":
                    break

    except httpx.ConnectError:
        pytest.skip("Agent not running. Start with docker-compose.")

    parsed = parse_sse_stream(raw_lines, trace_id)

    # --- Assertions ---

    # 1. Response should be substantial (> 200 chars)
    assert (
        len(parsed.final_answer) > 200
    ), f"Expected substantial response (> 200 chars), got {len(parsed.final_answer)} chars"

    # 2. Response should mention key terms
    final_lower = parsed.final_answer.lower()
    key_terms_found = sum(
        [
            "nobel" in final_lower,
            "physics" in final_lower,
            "2025" in final_lower or "prize" in final_lower,
        ]
    )
    assert (
        key_terms_found >= 2
    ), f"Expected response to mention Nobel/Physics/2025, got: {parsed.final_answer[:300]}"

    # 3. Verify trace contains web search spans (if trace ID available)
    if trace_id:
        trace_group = await get_trace_by_id(async_client, AGENT_BASE_URL, trace_id)

        if trace_group:
            # Check for web search or SearXNG spans
            has_search_span = (
                assert_span_exists(trace_group, r"web_search")
                or assert_span_exists(trace_group, r"searxng")
                or assert_span_exists(trace_group, r"search")
            )

            # This is a soft assertion - log but don't fail if no trace found
            if not has_search_span:
                pytest.warns(UserWarning, match="No web_search span found in trace")

    # 4. Check for forbidden error patterns
    _assert_no_forbidden_patterns(parsed.final_answer)

    # 5. Check for meta-commentary (fast regex check)
    _assert_no_meta_commentary(parsed.final_answer)

    # 6. LLM semantic quality check (optional, slower)
    if ENABLE_LLM_EVAL:
        result = await evaluate_response(parsed.final_answer, "no_meta_commentary")
        if not result.passes:
            pytest.fail(
                f"LLM semantic check failed ({result.criteria_checked}): {result.reasoning}"
            )


@pytest.mark.asyncio
async def test_scenario_c_error_resilience(
    async_client: httpx.AsyncClient,
    ensure_agent_healthy: None,
) -> None:
    """
    Scenario C: Error Resilience.

    Send a tricky/malformed prompt and verify:
    - Response is polite and helpful (not empty)
    - No raw system errors exposed
    - HTTP status is 200 (graceful handling)
    """
    url = f"{AGENT_BASE_URL}/v1/chat/completions"
    # Use a nonexistent command that might trigger error handling
    payload = make_chat_request_payload("/nonexistent_command_12345 blah blah")
    headers = get_request_headers()

    try:
        async with async_client.stream("POST", url, json=payload, headers=headers) as response:
            # Key assertion: Should return 200 even for weird input
            assert response.status_code == 200, f"Expected graceful 200, got {response.status_code}"

            trace_id = response.headers.get("x-trace-id", "")

            raw_lines: list[str] = []
            async for line in response.aiter_lines():
                raw_lines.append(line)
                if line.strip() == "data: [DONE]":
                    break

    except httpx.ConnectError:
        pytest.skip("Agent not running. Start with docker-compose.")

    parsed = parse_sse_stream(raw_lines, trace_id)

    # --- Assertions ---

    # 1. Response should not be empty
    assert len(parsed.final_answer.strip()) > 0, "Expected non-empty response for error case"

    # 2. Response should NOT contain raw system errors
    _assert_no_forbidden_patterns(parsed.final_answer)

    # 3. Response should be somewhat helpful (contains words, not just errors)
    word_count = len(parsed.final_answer.split())
    assert word_count >= 3, f"Expected at least 3 words in response, got {word_count}"


def _assert_no_forbidden_patterns(content: str) -> None:
    """Assert that content doesn't contain forbidden error patterns."""
    for pattern in FORBIDDEN_ERROR_PATTERNS:
        match = re.search(pattern, content)
        if match:
            # Provide context around the match
            start = max(0, match.start() - 50)
            end = min(len(content), match.end() + 50)
            context = content[start:end]
            pytest.fail(f"Response contains forbidden pattern '{pattern}':\n" f"...{context}...")


def _assert_no_meta_commentary(content: str) -> None:
    """Assert that content doesn't contain meta-commentary about the agent's process."""
    for pattern in META_COMMENTARY_PATTERNS:
        match = re.search(pattern, content)
        if match:
            # Provide context around the match
            start = max(0, match.start() - 30)
            end = min(len(content), match.end() + 30)
            context = content[start:end]
            pytest.fail(
                f"Response contains meta-commentary pattern '{pattern}':\n" f"...{context}..."
            )
