"""Response agent responsible for finalising output."""

from __future__ import annotations

from datetime import UTC, datetime

from core.core.models import AgentMessage, AgentResponse
from core.models.pydantic_schemas import TraceContext, UserFacingEvent
from core.observability.logging import log_event
from core.observability.tracing import current_trace_ids, start_span


class ResponseAgent:
    """Prepare the final response with trace metadata."""

    async def finalize(
        self,
        *,
        completion: str,
        conversation_id: str,
        messages: list[AgentMessage],
        steps: list[dict],
        metadata: dict,
    ) -> AgentResponse:
        with start_span("response.finalize"):
            trace_ctx = TraceContext(**current_trace_ids())
            log_event(UserFacingEvent(message="Returning response", trace=trace_ctx))
            enriched_steps: list[dict] = []
            for step in steps:
                step_trace = step.get("trace") or trace_ctx.model_dump()
                enriched_steps.append({**step, "trace": step_trace})
            enriched_metadata = metadata | {"trace": trace_ctx.model_dump()}
            return AgentResponse(
                conversation_id=conversation_id,
                response=completion,
                messages=messages,
                steps=enriched_steps,
                metadata=enriched_metadata,
                created_at=datetime.now(UTC),
            )


__all__ = ["ResponseAgent"]
