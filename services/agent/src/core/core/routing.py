import re
from collections.abc import Callable
from re import Pattern
from typing import Any, TypedDict


class FastPathEntry(TypedDict, total=False):
    """Defines the structure for a fast path entry."""

    pattern: Pattern[str]
    tool: str
    args: dict[str, Any]
    arg_mapper: Callable[[re.Match], dict[str, Any]]
    description: str


class FastPathRegistry:
    """Registry for managing fast path routing rules."""

    def __init__(self) -> None:
        self._paths: list[FastPathEntry] = []

    def register(self, entry: FastPathEntry) -> None:
        """Register a new fast path entry."""
        self._paths.append(entry)

    def get_match(self, message: str) -> tuple[FastPathEntry, re.Match] | None:
        """Find the first matching fast path for the given message."""
        stripped_message = message.strip()
        for path in self._paths:
            match = path["pattern"].search(stripped_message)
            if match:
                return path, match
        return None


# Global registry instance
registry = FastPathRegistry()


# Helper for ADO args
def _map_ado_args(match: re.Match) -> dict[str, Any]:
    return {"title": match.group(1), "description": "Created via Fast Path"}


# Register default paths (migrated from dispatcher.py)
registry.register(
    {
        "pattern": re.compile(r"^tänd lampan", re.IGNORECASE),
        "tool": "home_automation",
        "args": {"action": "turn_on", "device": "lamp"},
        "description": "Direct command to turn on the lamp.",
    }
)

registry.register(
    {
        "pattern": re.compile(r"^/ado\s+(.+)", re.IGNORECASE),
        "tool": "azure_devops",
        "arg_mapper": _map_ado_args,
        "description": "Create Azure DevOps work item.",
    }
)
