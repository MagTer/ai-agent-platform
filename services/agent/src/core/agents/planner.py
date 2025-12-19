"""Planner agent responsible for generating orchestration plans."""

from __future__ import annotations

import json
import logging
from typing import Any

from core.core.litellm_client import LiteLLMClient
from core.models.pydantic_schemas import PlanEvent, TraceContext
from core.observability.logging import log_event
from core.observability.tracing import current_trace_ids, start_span
from pydantic import ValidationError
from shared.models import AgentMessage, AgentRequest, Plan

LOGGER = logging.getLogger(__name__)


class PlannerAgent:
    """Generate plans using the configured planning model."""

    def __init__(self, litellm: LiteLLMClient, model_name: str | None = None) -> None:
        self._litellm = litellm
        self._model_name = model_name
        return

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
        LOGGER.info(f"PLANNER DEBUG: Available tools passed to LLM:\n{available_tools_text}")

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
                "You are the Planner Agent. Your task is to create a JSON execution plan "
                "for the user's request.\n"
                "You have access to a specific set of tools. Use them to gather "
                "information if needed.\n\n"
                "### RESPONSE FORMAT (Strict JSON)\n"
                "{\n"
                '  "description": "Brief plan summary",\n'
                '  "steps": [\n'
                "    {\n"
                '      "id": "step-1",\n'
                '      "label": "Step Name",\n'
                '      "executor": "agent" or "litellm",\n'
                '      "action": "memory" or "tool" or "completion",\n'
                '      "tool": "tool_name" (only for action="tool"),\n'
                '      "args": { ... } (arguments for the action/tool)\n'
                "    }\n"
                "  ]\n"
                "}\n\n"
                "### ACTIONS & EXECUTORS\n"
                "1. SEARCH MEMORY (executor='agent', action='memory')\n"
                "   - Use to find past context.\n"
                "   - Args: { 'query': 'search string' }\n"
                "2. EXECUTE TOOL (executor='agent', action='tool')\n"
                "   - Use to call an external function from the Available Tools list.\n"
                "   - Tool: Exact name from the list.\n"
                "   - Args: Dictionary of arguments for that tool.\n"
                "3. GENERATE ANSWER (executor='litellm', action='completion')\n"
                "   - Use this as the FINAL step to synthesize the answer.\n"
                "   - Args: { 'model': 'ollama/llama3.1:8b' }\n\n"
                "### CRITICAL RULES\n"
                "- You have FULL PERMISSION to use all available tools. Do not ask for "
                "confirmation.\n"
                "- The LAST step must ALWAYS be action='completion'.\n"
                "- Do NOT hallucinate tools. Only use provided ones.\n"
                "- **NO GUESSING**: If you need to know the content of a file or a web page, "
                "you MUST plan a tool call to read it first. You cannot 'know' it otherwise.\n"
                "- **VERIFY ARGS**: Check the required arguments for each tool in the "
                "'Available Tools' list.\n"
                "- **WEB SEARCH**: 'web_search' only gives snippets. ALWAYS follow up with "
                "'web_fetch' (Args: url) to get the actual page content.\n"
                "- **NO CONVERSATIONAL TEXT**: Output ONLY the JSON object. Do not say "
                "'Here is the plan'.\n"
                "- If no tools are needed, just plan a 'completion' step.\n"
            ),
        )

        user_message = AgentMessage(
            role="user",
            content=(
                f"### AVAILABLE TOOLS\n{available_tools_text}\n\n"
                f"### USER REQUEST\n{request.prompt}\n\n"
                f"### CONTEXT (History)\n{history_text}\n\n"
                f"### METADATA\n{metadata_text}\n\n"
                "Create the execution plan now."
            ),
        )

        model_name = self._model_name
        if model_name is None:
            settings = getattr(self._litellm, "_settings", None)
            model_name = getattr(settings, "litellm_model", None)

        span_attributes = {"step": "plan"}
        if model_name:
            span_attributes["model"] = model_name

        span_attributes = {"step": "plan"}
        if model_name:
            span_attributes["model"] = model_name

        with start_span(
            "planner.generate",
            attributes=span_attributes,
        ) as span:
            max_retries = 2
            attempts = 0
            history_augmentation: list[AgentMessage] = []

            while attempts <= max_retries:
                attempts += 1
                if attempts == 1:
                    msgs = [system_message, user_message]
                else:
                    msgs = [system_message, user_message] + history_augmentation

                plan_text = await self._litellm.plan(
                    messages=msgs,
                    model=model_name,
                )
                span.set_attribute("llm.output.size", len(plan_text))
                if model_name:
                    span.set_attribute("llm.model", model_name)

                candidate = self._extract_json_fragment(plan_text)
                exc_msg = None

                if candidate:
                    try:
                        plan = Plan(**candidate)
                        trace_ctx = TraceContext(**current_trace_ids())
                        log_event(
                            PlanEvent(
                                description=plan.description,
                                step_count=len(plan.steps),
                                trace=trace_ctx,
                            )
                        )
                        span.set_attribute("plan.step_count", len(plan.steps))
                        return plan
                    except ValidationError as exc:
                        exc_msg = str(exc)
                        LOGGER.warning(
                            "Planner generated invalid JSON schema: %s - %s", candidate, exc
                        )
                else:
                    exc_msg = "Could not extract valid JSON object from response."
                    LOGGER.warning("Failed to extract JSON from planner output: %s", plan_text)

                # If we are here, we failed. Prepare for retry if possible.
                if attempts <= max_retries:
                    LOGGER.info(
                        f"Retrying plan generation (attempt {attempts}/{max_retries + 1}). "
                        f"Error: {exc_msg}"
                    )
                    # Update history to include the failed attempt and error feedback
                    # Note: We need to maintain the conversation flow.
                    # We can't easily append to 'user_message' specifically,
                    # so we construct a temporary history extension.
                    if attempts == 1:
                        history_augmentation = [
                            AgentMessage(role="assistant", content=plan_text),
                            AgentMessage(
                                role="user",
                                content=(
                                    "You generated invalid JSON. Please fix it based on this "
                                    f"error:\n{exc_msg}"
                                ),
                            ),
                        ]
                    else:
                        history_augmentation.extend(
                            [
                                AgentMessage(role="assistant", content=plan_text),
                                AgentMessage(
                                    role="user", content=f"Still invalid. Error:\n{exc_msg}"
                                ),
                            ]
                        )
                else:
                    # Final fallback
                    return Plan(
                        steps=[],
                        description=(
                            f"Planner failed after {attempts} attempts. Last error: {exc_msg}"
                        ),
                    )

            raise RuntimeError("Unreachable: Planner loop exited without return")

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
