"""Semantic evaluation tool for running golden query regression tests."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx
import yaml

from core.tools.base import Tool

LOGGER = logging.getLogger(__name__)

# Path to golden queries yaml (relative to repo root)
_GOLDEN_QUERIES_PATH = (
    Path(__file__).resolve().parents[6] / "services/agent/tests/semantic/golden_queries.yaml"
)

_AGENT_BASE_URL = "http://localhost:8000"
_PER_QUERY_TIMEOUT = 60.0
_TOTAL_TIMEOUT = 300.0


def _grade_response(scenario: dict[str, Any], content: str) -> list[str]:
    """Grade a response against scenario expectations.

    Args:
        scenario: Scenario dict from golden_queries.yaml.
        content: Full response content from the agent.

    Returns:
        List of failure reasons (empty = passed).
    """
    errors: list[str] = []

    for must in scenario.get("must_contain", []):
        if must.lower() not in content.lower():
            errors.append(f"missing '{must}'")

    for pattern in scenario.get("must_contain_pattern", []):
        if not re.search(pattern, content, re.IGNORECASE):
            errors.append(f"missing pattern '{pattern}'")

    for bad in scenario.get("forbidden", []):
        if bad.lower() in content.lower():
            errors.append(f"found forbidden '{bad}'")

    min_length = scenario.get("min_response_length", 0)
    if min_length and len(content) < min_length:
        errors.append(f"response too short ({len(content)} < {min_length})")

    return errors


async def _run_query(
    client: httpx.AsyncClient,
    query: str,
    user_email: str,
    api_key: str | None,
) -> str:
    """Send a single query to the agent and return the accumulated response text.

    Args:
        client: Shared httpx client.
        query: User query string.
        user_email: Synthetic user email for OpenWebUI auth header.
        api_key: Optional bearer token for AGENT_INTERNAL_API_KEY auth.

    Returns:
        Accumulated response text.
    """
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "X-OpenWebUI-User-Email": user_email,
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": "agent",
        "messages": [{"role": "user", "content": query}],
        "stream": True,
    }

    content_parts: list[str] = []

    async with client.stream(
        "POST",
        f"{_AGENT_BASE_URL}/v1/chat/completions",
        json=payload,
        headers=headers,
        timeout=_PER_QUERY_TIMEOUT,
    ) as response:
        if response.status_code != 200:
            body = await response.aread()
            return f"HTTP {response.status_code}: {body[:200].decode('utf-8', errors='replace')}"

        async for line in response.aiter_lines():
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
                choices = chunk.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    text = delta.get("content", "")
                    if text:
                        content_parts.append(text)
            except json.JSONDecodeError:
                pass

    return "".join(content_parts)


class SemanticEvalTool(Tool):
    """Run golden query regression tests for a named category."""

    name = "semantic_eval"
    description = "Run golden query regression tests for a named category."
    category = "system"

    async def run(self, category: str = "routing", **kwargs: Any) -> str:
        """Execute all golden queries for the given category and grade the results.

        Args:
            category: Golden query category (routing, regression, skills, tools, etc.).

        Returns:
            Compact result string, e.g. "routing: 4/5 passed. FAIL: id -- reason"
        """
        # Load golden queries
        yaml_path = _GOLDEN_QUERIES_PATH
        if not yaml_path.exists():
            # Fallback: try relative to cwd
            yaml_path = Path("services/agent/tests/semantic/golden_queries.yaml")

        if not yaml_path.exists():
            return f"ERROR: golden_queries.yaml not found at {_GOLDEN_QUERIES_PATH}"

        try:
            with open(yaml_path) as f:
                all_scenarios: list[dict[str, Any]] = yaml.safe_load(f) or []
        except Exception as exc:
            return f"ERROR: Failed to load golden_queries.yaml: {exc}"

        scenarios = [s for s in all_scenarios if s.get("category") == category]

        if not scenarios:
            return f"ERROR: No scenarios found for category '{category}'"

        user_email = "system@agent.internal"
        api_key = os.environ.get("AGENT_INTERNAL_API_KEY", "")

        passed = 0
        failures: list[str] = []

        async with httpx.AsyncClient() as client:
            for scenario in scenarios:
                sid = scenario.get("id", "unknown")
                query = scenario.get("query", "")
                try:
                    content = await asyncio.wait_for(
                        _run_query(client, query, user_email, api_key or None),
                        timeout=_PER_QUERY_TIMEOUT + 5,
                    )
                    errors = _grade_response(scenario, content)
                    if errors:
                        failures.append(f"{sid} -- {'; '.join(errors)}")
                    else:
                        passed += 1
                except TimeoutError:
                    failures.append(f"{sid} -- timeout after {_PER_QUERY_TIMEOUT}s")
                except Exception as exc:
                    failures.append(f"{sid} -- error: {str(exc)[:100]}")

        total = len(scenarios)
        summary = f"{category}: {passed}/{total} passed"
        if failures:
            summary += ". FAIL: " + "; ".join(failures)

        LOGGER.info("SemanticEvalTool[%s]: %s/%s passed", category, passed, total)
        return summary


__all__ = ["SemanticEvalTool"]
