"""Domain models for the agent API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from shared.models import (
    AgentMessage,
    AgentRequest,
    AgentResponse,
    HealthStatus,
    Plan,
    PlanStep,
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
