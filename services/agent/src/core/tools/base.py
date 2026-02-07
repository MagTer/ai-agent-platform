"""Tooling abstractions used by the agent."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ToolError(RuntimeError):
    """Raised when a tool call fails."""


class ToolConfirmationError(ToolError):
    """Raised when a tool requires user confirmation to proceed.

    Used for dangerous operations that need explicit user approval
    before execution (e.g., destructive git commands, file deletions).

    Attributes:
        tool_name: Name of the tool requiring confirmation.
        tool_args: Arguments that will be passed to the tool.
    """

    def __init__(self, tool_name: str, tool_args: dict[str, Any]) -> None:
        """Initialize confirmation error.

        Args:
            tool_name: Name of the tool.
            tool_args: Tool arguments for transparency.
        """
        self.tool_name = tool_name
        self.tool_args = tool_args
        super().__init__(f"Tool '{tool_name}' requires confirmation.")


class Tool(ABC):
    """Abstract base class for agent tools.

    All tools must inherit from this class and implement the run() method.
    Tools provide discrete capabilities to the agent (web search, file operations,
    API integrations, etc.).

    Attributes:
        name: Unique tool identifier (used in plans and skill frontmatter).
        description: Human-readable description for LLM context.
        category: Tool category (domain, orchestration, smart_home, etc.).
        requires_confirmation: If True, tool execution pauses for user approval.
        mcp_annotations: MCP protocol hints (readOnlyHint, destructiveHint).
        activity_hint: UI display patterns for tool arguments.
    """

    name: str
    description: str
    category: str = "domain"
    requires_confirmation: bool = False

    # MCP tool annotations (readOnlyHint, destructiveHint, etc.)
    mcp_annotations: dict[str, bool | None] | None = None

    # Optional hint for displaying tool activity in the UI
    # Maps argument names to display patterns, e.g. {"query": "Searching: \"{query}\""}
    # Special placeholder {domain} extracts netloc from URL values
    activity_hint: dict[str, str] | None = None

    @abstractmethod
    async def run(self, *args: Any, **kwargs: Any) -> Any:
        """Execute the tool and return results.

        Args:
            *args: Positional arguments (typically unused).
            **kwargs: Tool-specific keyword arguments.

        Returns:
            Tool output (str, dict, or custom type).
        """


__all__ = ["Tool", "ToolError"]
