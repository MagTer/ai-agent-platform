from typing import Any, Literal, TypedDict


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
    ]
    content: str | None
    tool_call: dict[str, Any] | None
    metadata: dict[str, Any] | None
