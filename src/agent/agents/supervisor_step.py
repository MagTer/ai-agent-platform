"""Step supervisor agent."""

from __future__ import annotations

from agent.core.models import PlanStep
from agent.models.pydantic_schemas import SupervisorDecision, TraceContext
from agent.observability.logging import log_event
from agent.observability.tracing import current_trace_ids, start_span


class StepSupervisorAgent:
    """Validate step execution results."""

    async def review(self, step: PlanStep, status: str) -> str:
        decision = "ok" if status == "ok" else "adjust"
        with start_span(
            "supervisor.step_review",
            attributes={"step": step.id, "status": status, "decision": decision},
        ):
            log_event(
                SupervisorDecision(
                    item_id=step.id,
                    decision=decision,
                    comments=f"Step {status}",
                    trace=TraceContext(**current_trace_ids()),
                )
            )
            return decision


__all__ = ["StepSupervisorAgent"]
