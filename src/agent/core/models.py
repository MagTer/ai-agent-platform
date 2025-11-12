"""Domain models for the agent API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentMessage(BaseModel):
    """Representation of a chat message exchanged with the agent."""

    role: str = Field(description="Chat role such as 'user', 'assistant' or 'system'.")
    content: str = Field(description="Natural language content of the message.")


class AgentRequest(BaseModel):
    """Inbound request payload for the agent endpoint."""

    prompt: str = Field(description="Latest user prompt to process.")
    conversation_id: str | None = Field(
        default=None,
        description="Optional conversation identifier to preserve context across calls.",
    )
    metadata: dict[str, Any] | None = Field(
        default=None, description="Arbitrary metadata forwarded to the orchestrator."
    )
    messages: list[AgentMessage] | None = Field(
        default=None,
        description=(
            "Optional explicit conversation history to seed the prompt. When provided "
            "the state store history is ignored for this call."
        ),
    )


class AgentResponse(BaseModel):
    """Response payload returned to the caller."""

    conversation_id: str = Field(description="Conversation identifier used for follow-up calls.")
    response: str = Field(description="Assistant completion text.")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    messages: list[AgentMessage] = Field(
        default_factory=list,
        description="Messages used to assemble the final prompt for observability.",
    )
    steps: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Structured orchestration trace describing memory lookups, tool invocations, "
            "and LLM calls performed while answering the prompt."
        ),
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class HealthStatus(BaseModel):
    """Lightweight health payload for monitoring."""

    status: str = Field(default="ok")
    detail: str | None = None


class PlanStep(BaseModel):
    """Structured instruction for orchestrating the prompt lifecycle."""

    id: str = Field(description="Unique identifier for the plan step.")
    label: str = Field(description="Human-readable label describing the step.")
    executor: Literal["agent", "litellm", "remote"] = Field(
        description="Indicates which execution context runs the step."
    )
    action: Literal["memory", "tool", "completion"] = Field(
        description="Semantic action performed by the step."
    )
    tool: str | None = Field(default=None, description="Tool referenced by this step (if any).")
    args: dict[str, Any] = Field(
        default_factory=dict, description="Optional arguments consumed by the step."
    )
    description: str | None = Field(
        default=None, description="Additional context the planner wanted to capture."
    )
    provider: str | None = Field(
        default=None,
        description="Override provider identifier when the step reaches a remote LLM.",
    )


class Plan(BaseModel):
    """Planner output injected into response metadata for observability."""

    steps: list[PlanStep] = Field(
        default_factory=list,
        description="Ordered list of directives that the agent service consumes sequentially.",
    )
    description: str | None = Field(
        default=None, description="Optional summary text produced by the planner."
    )


class ChatCompletionMessage(BaseModel):
    """Minimal OpenAI-compatible chat message payload."""

    role: str
    content: str

    def to_agent_message(self) -> AgentMessage:
        """Convert to an :class:`AgentMessage`."""

        return AgentMessage(role=self.role, content=self.content)


class ChatCompletionRequest(BaseModel):
    """Subset of the OpenAI chat completion schema accepted by the agent."""

    model: str
    messages: list[ChatCompletionMessage]
    conversation_id: str | None = None
    metadata: dict[str, Any] | None = None


class ChatCompletionChoice(BaseModel):
    """Choice payload mirroring the OpenAI response shape."""

    index: int
    finish_reason: str
    message: dict[str, Any]


class ChatCompletionResponse(BaseModel):
    """Lightweight OpenAI-compatible response returned to Open WebUI."""

    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    steps: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] | None = None


__all__ = [
    "AgentMessage",
    "AgentRequest",
    "AgentResponse",
    "Plan",
    "PlanStep",
    "HealthStatus",
    "ChatCompletionMessage",
    "ChatCompletionRequest",
    "ChatCompletionChoice",
    "ChatCompletionResponse",
]
