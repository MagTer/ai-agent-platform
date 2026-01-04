"""
Trace Validator for Semantic Tests.

Provides utilities to correlate HTTP requests with trace data
via the diagnostics API.
"""

import asyncio
import re
from typing import Any

import httpx


async def get_recent_traces(
    client: httpx.AsyncClient,
    base_url: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Get recent trace groups from the diagnostics API.

    Args:
        client: HTTP client
        base_url: Agent base URL
        limit: Maximum traces to retrieve

    Returns:
        List of trace groups (newest first)
    """
    url = f"{base_url}/diagnostics/traces"
    params = {"limit": limit, "show_all": "false"}

    resp = await client.get(url, params=params)
    resp.raise_for_status()
    return resp.json()


async def get_trace_by_id(
    client: httpx.AsyncClient,
    base_url: str,
    trace_id: str,
    max_retries: int = 5,
    retry_delay: float = 0.5,
) -> dict[str, Any] | None:
    """
    Find a specific trace by its ID.

    Since traces are written asynchronously, we may need to retry.

    Args:
        client: HTTP client
        base_url: Agent base URL
        trace_id: The trace ID to find
        max_retries: Number of retry attempts
        retry_delay: Seconds between retries

    Returns:
        Trace group dict if found, None otherwise
    """
    for attempt in range(max_retries):
        traces = await get_recent_traces(client, base_url, limit=50)

        for trace in traces:
            if trace.get("trace_id") == trace_id:
                return trace

        # Trace not yet written, wait and retry
        if attempt < max_retries - 1:
            await asyncio.sleep(retry_delay)

    return None


def assert_span_exists(
    trace_group: dict[str, Any],
    span_name_pattern: str,
) -> bool:
    """
    Check if a span matching the pattern exists in the trace.

    Args:
        trace_group: Trace group from diagnostics API
        span_name_pattern: Regex pattern to match span names

    Returns:
        True if a matching span exists
    """
    pattern = re.compile(span_name_pattern, re.IGNORECASE)

    spans = trace_group.get("spans", [])
    for span in spans:
        name = span.get("name", "")
        if pattern.search(name):
            return True

        # Also check attributes for tool names
        attrs = span.get("attributes", {})
        tool_name = attrs.get("tool.name", "")
        if tool_name and pattern.search(tool_name):
            return True

    return False


def get_span_by_name(
    trace_group: dict[str, Any],
    span_name_pattern: str,
) -> dict[str, Any] | None:
    """
    Get the first span matching the pattern.

    Args:
        trace_group: Trace group from diagnostics API
        span_name_pattern: Regex pattern to match span names

    Returns:
        Span dict if found, None otherwise
    """
    pattern = re.compile(span_name_pattern, re.IGNORECASE)

    spans = trace_group.get("spans", [])
    for span in spans:
        name = span.get("name", "")
        if pattern.search(name):
            return span

    return None


def assert_no_error_spans(trace_group: dict[str, Any]) -> list[str]:
    """
    Check for any error spans and return their names.

    Args:
        trace_group: Trace group from diagnostics API

    Returns:
        List of error span names (empty if no errors)
    """
    error_spans: list[str] = []

    spans = trace_group.get("spans", [])
    for span in spans:
        status = span.get("status", "")
        if status in ("ERROR", "fail"):
            error_spans.append(span.get("name", "unknown"))

    return error_spans
