from enum import Enum
from typing import Any, Literal, TypedDict


class VerbosityLevel(Enum):
    """Output verbosity levels for streaming responses.

    DEFAULT: Shows planning, skills, errors, supervisor replanning, final answer.
    VERBOSE: Shows all chunks with formatted output (like old default behavior).
    DEBUG: Shows raw JSON for all chunks, except final answer shows normally.
    """

    DEFAULT = "default"
    VERBOSE = "verbose"
    DEBUG = "debug"


class AgentChunk(TypedDict):
    """
    Unified schema for streaming chunks from the agent system.
    """

    type: Literal[
        "content",
        "step_start",
        "tool_start",
        "tool_output",
        "thinking",
        "error",
        "done",
        "history_snapshot",
        "skill_activity",
        "awaiting_input",
    ]
    content: str | None
    tool_call: dict[str, Any] | None
    metadata: dict[str, Any] | None
