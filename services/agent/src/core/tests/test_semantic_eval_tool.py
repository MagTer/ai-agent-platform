"""Unit tests for SemanticEvalTool."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest
import yaml

from core.tools.semantic_eval import SemanticEvalTool, _grade_response

# ---------------------------------------------------------------------------
# _grade_response tests (pure, synchronous)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scenario, content, expect_pass",
    [
        # must_contain: present
        (
            {"must_contain": ["Paris"]},
            "The capital of France is Paris.",
            True,
        ),
        # must_contain: missing
        (
            {"must_contain": ["Paris"]},
            "The capital of France is Rome.",
            False,
        ),
        # must_contain_pattern: matches
        (
            {"must_contain_pattern": [r"\d+\.\d+%"]},
            "The rate is 3.5% today.",
            True,
        ),
        # must_contain_pattern: no match
        (
            {"must_contain_pattern": [r"\d+\.\d+%"]},
            "No numbers here.",
            False,
        ),
        # forbidden: clean
        (
            {"forbidden": ["Error", "exception"]},
            "Everything went fine.",
            True,
        ),
        # forbidden: contains bad word
        (
            {"forbidden": ["Error"]},
            "There was an Error processing your request.",
            False,
        ),
        # min_response_length: long enough
        (
            {"min_response_length": 10},
            "A" * 20,
            True,
        ),
        # min_response_length: too short
        (
            {"min_response_length": 100},
            "Short.",
            False,
        ),
        # combined pass
        (
            {
                "must_contain": ["hello"],
                "must_contain_pattern": [r"hello\s+world"],
                "forbidden": ["error"],
                "min_response_length": 5,
            },
            "hello world",
            True,
        ),
    ],
)
def test_grade_response(scenario: dict[str, Any], content: str, expect_pass: bool) -> None:
    errors = _grade_response(scenario, content)
    if expect_pass:
        assert errors == [], f"Expected pass but got errors: {errors}"
    else:
        assert errors, "Expected failure but got no errors"


# ---------------------------------------------------------------------------
# SemanticEvalTool.run tests (async, with httpx mocked)
# ---------------------------------------------------------------------------

_SAMPLE_QUERIES = [
    {
        "id": "routing_greeting",
        "category": "routing",
        "query": "Hello",
        "must_contain": ["assistant"],
    },
    {
        "id": "routing_simple",
        "category": "routing",
        "query": "What is 2+2?",
        "must_contain": ["4"],
    },
]

_SAMPLE_YAML = yaml.dump(_SAMPLE_QUERIES)


def _make_streaming_response(text: str) -> AsyncMock:
    """Build a mock httpx streaming response that yields an SSE chunk."""
    lines = [
        f'data: {{"choices": [{{"delta": {{"content": "{text}"}}}}]}}',
        "data: [DONE]",
    ]

    async def aiter_lines() -> Any:
        for line in lines:
            yield line

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.aiter_lines = aiter_lines
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)
    return mock_response


@pytest.mark.asyncio
async def test_run_all_pass() -> None:
    """Both queries pass; result shows 2/2 passed."""
    tool = SemanticEvalTool()

    # Provide responses: "hello assistant" and "the answer is 4"
    responses = [
        _make_streaming_response("hello assistant"),
        _make_streaming_response("the answer is 4"),
    ]

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.stream = MagicMock(side_effect=responses)

    with (
        patch("core.tools.semantic_eval._GOLDEN_QUERIES_PATH", Path("/fake/path.yaml")),
        patch("pathlib.Path.exists", return_value=True),
        patch(
            "builtins.open",
            mock_open(read_data=_SAMPLE_YAML),
        ),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await tool.run(category="routing")

    assert "routing: 2/2 passed" in result
    assert "FAIL" not in result


@pytest.mark.asyncio
async def test_run_one_fail() -> None:
    """First query fails (missing 'assistant'); result shows 1/2 passed."""
    tool = SemanticEvalTool()

    responses = [
        _make_streaming_response("good day"),  # missing "assistant"
        _make_streaming_response("the answer is 4"),
    ]

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.stream = MagicMock(side_effect=responses)

    with (
        patch("core.tools.semantic_eval._GOLDEN_QUERIES_PATH", Path("/fake/path.yaml")),
        patch("pathlib.Path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data=_SAMPLE_YAML)),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await tool.run(category="routing")

    assert "routing: 1/2 passed" in result
    assert "FAIL" in result
    assert "routing_greeting" in result


@pytest.mark.asyncio
async def test_run_unknown_category() -> None:
    """Unknown category returns an error string."""
    tool = SemanticEvalTool()

    with (
        patch("core.tools.semantic_eval._GOLDEN_QUERIES_PATH", Path("/fake/path.yaml")),
        patch("pathlib.Path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data=_SAMPLE_YAML)),
    ):
        result = await tool.run(category="nonexistent_category")

    assert "ERROR" in result
    assert "nonexistent_category" in result


@pytest.mark.asyncio
async def test_run_yaml_not_found() -> None:
    """Missing yaml file returns an error string."""
    tool = SemanticEvalTool()

    with patch("pathlib.Path.exists", return_value=False):
        result = await tool.run(category="routing")

    assert "ERROR" in result


@pytest.mark.asyncio
async def test_run_http_error() -> None:
    """HTTP error from agent is captured and counted as failure."""
    tool = SemanticEvalTool()

    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    async def aread() -> bytes:
        return b"Service Unavailable"

    mock_response.aread = aread

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.stream = MagicMock(return_value=mock_response)

    with (
        patch("core.tools.semantic_eval._GOLDEN_QUERIES_PATH", Path("/fake/path.yaml")),
        patch("pathlib.Path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data=_SAMPLE_YAML)),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await tool.run(category="routing")

    # Both queries fail due to HTTP 503 (response missing "assistant" and "4")
    assert "routing: 0/2 passed" in result
    assert "FAIL" in result
