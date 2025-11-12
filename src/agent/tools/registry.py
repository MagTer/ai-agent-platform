"""Registry used to discover available tools."""

from __future__ import annotations

from collections.abc import Iterable

from .base import Tool


class ToolRegistry:
    """Simple in-memory registry for agent tools."""

    def __init__(self, tools: Iterable[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        if tools:
            for tool in tools:
                self.register(tool)

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def available(self) -> list[str]:
        return sorted(self._tools)

    def tools(self) -> list[Tool]:
        """Return the registered tool instances."""
        return list(self._tools.values())


__all__ = ["ToolRegistry"]
