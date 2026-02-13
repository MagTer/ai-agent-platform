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


# --- Homey Smart Home Fast Paths ---


def _map_homey_turn_on(match: re.Match) -> dict[str, Any]:
    """Map 'turn on X' / 'tand X' to Homey control."""
    device_name = match.group(1).strip()
    return {
        "action": "control_device",
        "device_name": device_name,
        "capability": "onoff",
        "value": True,
    }


def _map_homey_turn_off(match: re.Match) -> dict[str, Any]:
    """Map 'turn off X' / 'slack X' to Homey control."""
    device_name = match.group(1).strip()
    return {
        "action": "control_device",
        "device_name": device_name,
        "capability": "onoff",
        "value": False,
    }


def _map_homey_dim(match: re.Match) -> dict[str, Any]:
    """Map 'dim X to Y%' / 'dimma X till Y%' to Homey control."""
    device_name = match.group(1).strip()
    percent = int(match.group(2))
    return {
        "action": "control_device",
        "device_name": device_name,
        "capability": "dim",
        "value": percent / 100.0,  # Convert to 0.0-1.0
    }


def _map_homey_trigger_flow(match: re.Match) -> dict[str, Any]:
    """Map 'trigger flow X' / 'starta flode X' to Homey flow trigger."""
    flow_name = match.group(1).strip()
    return {
        "action": "trigger_flow",
        "flow_name": flow_name,
    }


# Register default paths (migrated from dispatcher.py)
registry.register(
    {
        "pattern": re.compile(r"^/ado\s+(.+)", re.IGNORECASE),
        "tool": "azure_devops",
        "arg_mapper": _map_ado_args,
        "description": "Create Azure DevOps work item.",
    }
)

# Swedish: "tand X" / "tand pa X"
registry.register(
    {
        "pattern": re.compile(r"^t[a\xe4]nd\s+(?:p[a\xe5]\s+)?(.+)", re.IGNORECASE),
        "tool": "homey",
        "arg_mapper": _map_homey_turn_on,
        "description": "Turn on Homey device",
    }
)

# English: "turn on X"
registry.register(
    {
        "pattern": re.compile(r"^turn\s+on\s+(.+)", re.IGNORECASE),
        "tool": "homey",
        "arg_mapper": _map_homey_turn_on,
        "description": "Turn on Homey device",
    }
)

# Swedish: "slack X" / "stang av X"
registry.register(
    {
        "pattern": re.compile(r"^(?:sl[a\xe4]ck|st[a\xe4]ng\s+av)\s+(.+)", re.IGNORECASE),
        "tool": "homey",
        "arg_mapper": _map_homey_turn_off,
        "description": "Turn off Homey device",
    }
)

# English: "turn off X"
registry.register(
    {
        "pattern": re.compile(r"^turn\s+off\s+(.+)", re.IGNORECASE),
        "tool": "homey",
        "arg_mapper": _map_homey_turn_off,
        "description": "Turn off Homey device",
    }
)

# Swedish: "dimma X till Y%"
registry.register(
    {
        "pattern": re.compile(r"^dimma\s+(.+?)\s+till\s+(\d+)\s*%?", re.IGNORECASE),
        "tool": "homey",
        "arg_mapper": _map_homey_dim,
        "description": "Dim Homey device",
    }
)

# English: "dim X to Y%"
registry.register(
    {
        "pattern": re.compile(r"^dim\s+(.+?)\s+to\s+(\d+)\s*%?", re.IGNORECASE),
        "tool": "homey",
        "arg_mapper": _map_homey_dim,
        "description": "Dim Homey device",
    }
)

# Swedish: "starta flode X" / "kor flode X"
registry.register(
    {
        "pattern": re.compile(r"^(?:starta|k[o\xf6]r)\s+fl[o\xf6]de\s+(.+)", re.IGNORECASE),
        "tool": "homey",
        "arg_mapper": _map_homey_trigger_flow,
        "description": "Trigger Homey flow",
    }
)

# English: "trigger flow X" / "run flow X" / "start flow X"
registry.register(
    {
        "pattern": re.compile(r"^(?:trigger|run|start)\s+flow\s+(.+)", re.IGNORECASE),
        "tool": "homey",
        "arg_mapper": _map_homey_trigger_flow,
        "description": "Trigger Homey flow",
    }
)
