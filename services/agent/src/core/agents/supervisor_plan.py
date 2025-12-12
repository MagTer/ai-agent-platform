"""Plan supervisor agent."""

from __future__ import annotations

from core.core.models import Plan
from core.models.pydantic_schemas import SupervisorDecision, TraceContext
from core.observability.logging import log_event
from core.observability.tracing import current_trace_ids, start_span


class PlanSupervisorAgent:
    """Lightweight supervisor that can approve or adjust plans."""

    async def review(self, plan: Plan) -> Plan:
        with start_span("supervisor.plan_review", attributes={"plan.steps": len(plan.steps)}):
            decision = SupervisorDecision(
                item_id="plan",
                decision="ok",
                comments="Plan approved",
                trace=TraceContext(**current_trace_ids()),
            )
            log_event(decision)
            return plan


__all__ = ["PlanSupervisorAgent"]
