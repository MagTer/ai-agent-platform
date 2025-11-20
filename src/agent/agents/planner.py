"""Planner agent responsible for generating orchestration plans."""

from __future__ import annotations

import json
from typing import Any

from agent.core.litellm_client import LiteLLMClient
from agent.core.models import AgentMessage, AgentRequest, Plan
from agent.models.pydantic_schemas import PlanEvent, TraceContext
from agent.observability.logging import log_event
from agent.observability.tracing import current_trace_ids, start_span


class PlannerAgent:
    """Generate plans using the configured planning model."""

    def __init__(self, litellm: LiteLLMClient, model_name: str | None = None) -> None:
        self._litellm = litellm
        self._model_name = model_name

    async def generate(
        self,
        request: AgentRequest,
        *,
        history: list[AgentMessage],
        tool_descriptions: list[dict[str, str]],
    ) -> Plan:
        """Return a :class:`Plan` describing execution steps."""

        available_tools_text = (
            "\n".join(f"- {entry['name']}: {entry['description']}" for entry in tool_descriptions)
            or "- (no MCP-specific tools are registered)"
        )

        history_text = (
            "\n".join(f"{message.role}: {message.content}" for message in history) or "(no history)"
        )
        try:
            metadata_text = json.dumps(request.metadata or {}, indent=2)
        except (TypeError, ValueError):
            metadata_text = str(request.metadata)

        system_message = AgentMessage(
            role="system",
            content=(
                "You are the planner agent. Return a JSON object with a `steps` array containing"
                " sequential actions to fulfil the user prompt."
            ),
        )

        user_message = AgentMessage(
            role="user",
            content=(
                f"Question:\n{request.prompt}\n\n"
                f"Conversation history:\n{history_text}\n\n"
                f"Metadata provided to the agent:\n{metadata_text}\n\n"
                f"Available tools:\n{available_tools_text}\n\n"
                "Ensure the final step is a completion action."
            ),
        )

        model_name = self._model_name
        if model_name is None:
            settings = getattr(self._litellm, "_settings", None)
            model_name = getattr(settings, "litellm_model", None)

        span_attributes = {"step": "plan"}
        if model_name:
            span_attributes["model"] = model_name

        with start_span(
            "planner.generate",
            attributes=span_attributes,
        ) as span:
            plan_text = await self._litellm.plan([system_message, user_message])
            span.set_attribute("llm.output.size", len(plan_text))
            if model_name:
                span.set_attribute("llm.model", model_name)
            candidate = self._extract_json_fragment(plan_text)
            if candidate is None:
                candidate = {"steps": [], "description": "Unable to parse planner output"}
            plan = Plan(**candidate)
            trace_ctx = TraceContext(**current_trace_ids())
            log_event(
                PlanEvent(description=plan.description, step_count=len(plan.steps), trace=trace_ctx)
            )
            span.set_attribute("plan.step_count", len(plan.steps))
            return plan

    @staticmethod
    def _extract_json_fragment(raw: str) -> dict[str, Any] | None:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start == -1 or end == -1:
                return None
            fragment = raw[start : end + 1]
            try:
                return json.loads(fragment)
            except json.JSONDecodeError:
                return None


__all__ = ["PlannerAgent"]
