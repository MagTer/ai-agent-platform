"""Tooling abstractions used by the agent."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ToolError(RuntimeError):
    """Raised when a tool call fails."""


class Tool(ABC):
    """Abstract base class for agent tools."""

    name: str
    description: str

    @abstractmethod
    async def run(self, *args: Any, **kwargs: Any) -> Any:
        """Execute the tool and return the result."""


__all__ = ["Tool", "ToolError"]
