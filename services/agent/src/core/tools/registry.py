"""Registry used to discover available tools."""

from __future__ import annotations

from collections.abc import Iterable

from core.tools.base import Tool


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

    def list_tools(self) -> list[Tool]:
        """Return the registered tool instances (alias for tools())."""
        return self.tools()

    def clone(self) -> ToolRegistry:
        """Create a shallow copy of this registry.

        Used to create per-context registries without duplicating tool instances.
        Each context gets its own registry dict, but the tool instances themselves
        are shared (which is safe since tools are stateless or manage their own state).

        Returns:
            New ToolRegistry with copied tool dict
        """
        cloned = ToolRegistry()
        cloned._tools = self._tools.copy()  # Shallow copy of dict
        return cloned

    def filter_by_permissions(self, permissions: dict[str, bool]) -> None:
        """Remove tools not allowed for this context.

        Modifies the registry in-place to only include allowed tools.
        SECURITY: Tools not in the permissions dict are DENIED by default.

        Args:
            permissions: Mapping of tool_name → allowed (True/False)
                        Only True values will keep tools.

        Example:
            >>> registry.filter_by_permissions({"bash": False, "python": True})
            >>> # Only python remains (bash explicitly denied, others denied by default)
        """
        if not permissions:
            # No permissions defined - DENY all tools (secure default)
            import logging

            logger = logging.getLogger(__name__)
            logger.warning("No permissions defined - denying all tools (secure default)")
            self._tools = {}
            return

        # Filter to only include tools where permission is explicitly True
        filtered_tools = {
            name: tool
            for name, tool in self._tools.items()
            if permissions.get(name, False)  # SECURITY: Default deny if not in permissions
        }

        removed_count = len(self._tools) - len(filtered_tools)
        if removed_count > 0:
            import logging

            logger = logging.getLogger(__name__)
            removed_tools = set(self._tools.keys()) - set(filtered_tools.keys())
            logger.info(f"Filtered {removed_count} tools by permissions: {sorted(removed_tools)}")

        self._tools = filtered_tools


__all__ = ["ToolRegistry"]
