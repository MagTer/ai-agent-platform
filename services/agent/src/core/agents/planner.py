"""Planner agent responsible for generating orchestration plans."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, Callable
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from shared.models import AgentMessage, AgentRequest, Plan

from core.core.litellm_client import LiteLLMClient
from core.models.pydantic_schemas import PlanEvent, TraceContext
from core.observability.logging import log_event
from core.observability.tracing import current_trace_ids, start_span

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
        tool_descriptions: list[dict[str, Any]],
        available_skills_text: str = "",
        stream_callback: Callable[[str], Any] | None = None,
    ) -> Plan:
        """Return a :class:`Plan` describing execution steps.

        Backward compatibility wrapper around generate_stream.
        """
        last_plan = None
        async for event in self.generate_stream(
            request,
            history=history,
            tool_descriptions=tool_descriptions,
            available_skills_text=available_skills_text,
        ):
            if event["type"] == "plan":
                last_plan = event["plan"]
            elif event["type"] == "token" and stream_callback:
                try:
                    await stream_callback(event["content"])
                except Exception as e:
                    LOGGER.warning("Stream callback failed: %s", e)

        if not last_plan:
            raise ValueError("Planner failed to return a plan")
        return last_plan

    async def generate_stream(
        self,
        request: AgentRequest,
        *,
        history: list[AgentMessage],
        tool_descriptions: list[dict[str, Any]],
        available_skills_text: str = "",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Generate a plan and yield tokens/results incrementally."""

        lines = []
        for entry in tool_descriptions:
            name = entry["name"]
            desc = entry["description"]
            schema = entry.get("parameters") or entry.get("schema")

            if schema:
                # If we have a schema/dict, format it nicely
                try:
                    schema_str = json.dumps(schema, indent=None)
                except (TypeError, ValueError):
                    schema_str = str(schema)
                lines.append(f"- Tool: {name}\n  Desc: {desc}\n  Args: {schema_str}")
            else:
                # Fallback: Instruct to check tool usage or assume standard args
                lines.append(
                    f"- Tool: {name}\n  Desc: {desc}\n  Args: (Inspect usage or standard dict)"
                )

        available_tools_text = "\n".join(lines) or "- (no MCP-specific tools are registered)"
        LOGGER.info(f"PLANNER DEBUG: Available tools passed to LLM:\n{available_tools_text}")

        history_text = (
            "\n".join(f"{message.role}: {message.content}" for message in history) or "(no history)"
        )
        try:
            metadata_text = json.dumps(request.metadata or {}, indent=2)
        except (TypeError, ValueError):
            metadata_text = str(request.metadata)

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        today = datetime.now().strftime("%Y-%m-%d")
        year = datetime.now().year

        system_context = (
            "SYSTEM CONTEXT:\n"
            f"- Current Date & Time: {now}\n"
            f"- Your knowledge cutoff is static, but YOU ARE LIVE in {year}.\n"
            f"- Treat all retrieved documents dated up to {today} "
            "as HISTORICAL FACTS, not predictions.\n"
        )

        system_message = AgentMessage(
            role="system",
            content=(
                f"{system_context}\n"
                "You are the Planner Agent. Your goal is to orchestrate \n"
                "a precise JSON execution plan.\n"
                "You are an ORCHESTRATOR, not a worker. You CANNOT perform tasks directly \n"
                "(e.g., searching the web, writing code, reading files).\n"
                "You MUST delegate work to Domain Experts using the `consult_expert` tool.\n\n"
                f"### AVAILABLE SKILLS (Roles)\n{available_skills_text}\n\n"
                "### RESPONSE FORMAT (Strict JSON Only)\n"
                "You must output a single JSON object. No conversational text.\n"
                "{\n"
                '  "description": "Brief summary of the plan",\n'
                '  "steps": [\n'
                "    {\n"
                '      "id": "step-1",\n'
                '      "label": "Action Label",\n'
                '      "executor": "agent" | "litellm",\n'
                '      "action": "memory" | "tool" | "completion",\n'
                '      "tool": "tool_name" (REQUIRED if action="tool"),\n'
                '      "args": { "arg_name": "value" } (REQUIRED for all actions)\n'
                "    }\n"
                "  ]\n"
                "}\n\n"
                "### RULES\n"
                "1. **NO GUESSING**: If you need information, delegate to a SKILL\n"
                "   from the list above.\n"
                "2. **CRITICAL**: The `skill` argument for `consult_expert`\n"
                "   MUST be one of the exact 'Role' keys listed in 'AVAILABLE SKILLS'.\n"
                "   DO NOT put tool names (like 'web_search', 'python', 'google')\n"
                "   as the skill name.\n"
                "   Example: call `consult_expert(skill='researcher')`,\n"
                "   NOT `confirm_expert(skill='web_search')`.\n"
                "3. **STRICT ARGS**: Use exactly the arguments defined in the \n"
                "   'Available Tools' list.\n"
                "4. **FINAL STEP**: Must be `action: completion` (executor: litellm) \n"
                "   to answer the user.\n"
                "5. **MEMORY**: Use `action: memory` (args: { 'query': '...' }) \n"
                "   to find context if needed.\n"
                "6. **TOOL EXECUTOR**: If `action` is 'tool', `executor` MUST be 'agent'.\n"
                "7. **REQUIRED ARGS**: When calling `consult_expert`, you MUST provide \n"
                "   the specific arguments required by that skill (e.g., 'domain' for \n"
                "   researcher, 'repo' for git). Do not omit them.\n\n"
                "### EXAMPLES\n"
                "User: 'Research python 3.12'\n"
                "Plan:\n"
                "{\n"
                '  "description": "Delegate research to expert",\n'
                '  "steps": [\n'
                '    { "id": "1", "label": "Research", "executor": "agent", \n'
                '      "action": "tool", "tool": "consult_expert", \n'
                '      "args": { "skill": "researcher", "domain": "technology", \n'
                '                "goal": "Find features of Python 3.12" } },\n'
                '    { "id": "2", "label": "Answer", "executor": "litellm", \n'
                '      "action": "completion" }\n'
                "  ]\n"
                "}\n\n"
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

                plan_text_chunks = []
                async for chunk in self._litellm.stream_chat(msgs, model=model_name):
                    if chunk["type"] == "content" and chunk["content"]:
                        content = chunk["content"]
                        plan_text_chunks.append(content)
                        yield {"type": "token", "content": content}

                plan_text = "".join(plan_text_chunks)
                span.set_attribute("llm.output.size", len(plan_text))
                if model_name:
                    span.set_attribute("llm.model", model_name)

                candidate = self._extract_json_fragment(plan_text)
                exc_msg = None

                if candidate:
                    # Guardrail: Force executor='agent' for tool actions to prevent hallucinations
                    steps = candidate.get("steps")
                    if isinstance(steps, list):
                        for step in steps:
                            if isinstance(step, dict) and step.get("action") == "tool":
                                step["executor"] = "agent"

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
                        yield {"type": "plan", "plan": plan}
                        return
                    except ValidationError as exc:
                        exc_msg = str(exc)
                        LOGGER.warning(
                            "Planner generated invalid JSON schema: %s - %s",
                            candidate,
                            exc,
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
                                    role="user",
                                    content=f"Still invalid. Error:\n{exc_msg}",
                                ),
                            ]
                        )
                else:
                    # Final fallback
                    final_plan = Plan(
                        steps=[],
                        description=(
                            f"Planner failed after {attempts} attempts. Last error: {exc_msg}"
                        ),
                    )
                    yield {"type": "plan", "plan": final_plan}
                    return

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
