"""Activity hint utilities for tool execution UI feedback."""

import logging
from typing import Any
from urllib.parse import urlparse

from core.tools.base import Tool

LOGGER = logging.getLogger(__name__)


def build_activity_message(
    tool: Tool | None,
    tool_name: str,
    args: dict[str, Any],
) -> str:
    """Build user-facing activity message for tool execution.

    Priority order:
    1. Tool's activity_hint with placeholder substitution
    2. Common argument pattern matching (url, query, path)
    3. Generic fallback based on tool name

    Args:
        tool: Tool instance (may be None if not found)
        tool_name: Name of the tool being executed
        args: Arguments passed to the tool

    Returns:
        Human-readable activity message
    """
    # 1. Try tool's activity_hint first
    if tool and tool.activity_hint:
        hint = tool.activity_hint.get("running")
        if hint:
            # Handle special {domain} placeholder
            if "{domain}" in hint and "url" in args:
                try:
                    domain = urlparse(str(args["url"])).netloc
                    return hint.replace("{domain}", domain)
                except Exception as e:
                    LOGGER.debug("Failed to parse URL for activity hint: %s", e)
                    # Fall through to fallback
            return hint

    # 2. Fallback: Common argument patterns
    if "url" in args:
        try:
            domain = urlparse(str(args["url"])).netloc
            return f"Fetching {domain}"
        except Exception as e:
            LOGGER.debug("Failed to parse URL for activity message: %s", e)
            return "Fetching URL"

    if "query" in args:
        query = str(args["query"])
        if len(query) > 50:
            query = query[:47] + "..."
        return f"Searching: {query}"

    if "path" in args:
        path = str(args["path"])
        return f"Reading {path.split('/')[-1]}"

    # 3. Ultimate fallback
    return f"Running {tool_name}"
