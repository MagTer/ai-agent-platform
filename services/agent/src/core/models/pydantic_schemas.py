"""Structured observability schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class TraceContext(BaseModel):
    trace_id: str | None = Field(default=None)
    span_id: str | None = Field(default=None)


class StepEvent(BaseModel):
    step_id: str
    label: str
    action: str
    executor: str
    status: Literal["ok", "error", "skipped", "in_progress"]
    decision: Literal["ok", "adjust", "redo", "abort"] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace: TraceContext | None = None


class PlanEvent(BaseModel):
    description: str | None = None
    step_count: int
    trace: TraceContext | None = None


class SupervisorDecision(BaseModel):
    item_id: str
    decision: Literal["ok", "adjust", "redo", "abort"]
    comments: str | None = None
    trace: TraceContext | None = None


class ToolCallEvent(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    status: str
    output_preview: str | None = None
    trace: TraceContext | None = None


class UserFacingEvent(BaseModel):
    message: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    trace: TraceContext | None = None


__all__ = [
    "TraceContext",
    "StepEvent",
    "PlanEvent",
    "SupervisorDecision",
    "ToolCallEvent",
    "UserFacingEvent",
]
