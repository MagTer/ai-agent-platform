"""Tooling abstractions used by the agent."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ToolError(RuntimeError):
    """Raised when a tool call fails."""


class ToolConfirmationError(ToolError):
    """Raised when a tool requires user confirmation to proceed."""

    def __init__(self, tool_name: str, tool_args: dict[str, Any]) -> None:
        self.tool_name = tool_name
        self.tool_args = tool_args
        super().__init__(f"Tool '{tool_name}' requires confirmation.")


class Tool(ABC):
    """Abstract base class for agent tools."""

    name: str
    description: str
    category: str = "domain"
    requires_confirmation: bool = False

    @abstractmethod
    async def run(self, *args: Any, **kwargs: Any) -> Any:
        """Execute the tool and return the result."""


__all__ = ["Tool", "ToolError"]
