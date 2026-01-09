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

# Maximum length for user prompts to prevent context overflow
MAX_PROMPT_LENGTH = 4000


def _sanitize_user_input(text: str) -> str:
    """Sanitize user input to reduce prompt injection risk.

    This function:
    - Truncates excessively long inputs
    - Removes markdown code fence markers that could confuse JSON parsing
    - Logs when sanitization is applied

    Args:
        text: Raw user input text.

    Returns:
        Sanitized text safe for inclusion in planner prompt.
    """
    if not text:
        return text

    original_length = len(text)
    sanitized = text

    # Remove markdown code fences that could interfere with JSON output
    # These patterns could trick the LLM into thinking it's already in a code block
    if "```json" in sanitized or "```" in sanitized:
        sanitized = sanitized.replace("```json", "").replace("```", "")
        LOGGER.debug("Removed markdown code fences from user input")

    # Truncate excessively long inputs to prevent context overflow
    if len(sanitized) > MAX_PROMPT_LENGTH:
        sanitized = sanitized[:MAX_PROMPT_LENGTH] + "\n... (input truncated)"
        LOGGER.warning(
            f"User input truncated from {original_length} to {MAX_PROMPT_LENGTH} chars"
        )

    return sanitized


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

        # Optimized prompt for Llama 3.3 70B - concise, structured, clear rules
        system_message = AgentMessage(
            role="system",
            content=(
                f"{system_context}\n"
                "# PLANNER AGENT\n\n"
                "You generate JSON execution plans. You are an ORCHESTRATOR - "
                "delegate ALL work to skills via `consult_expert`.\n\n"
                f"## AVAILABLE SKILLS\n{available_skills_text}\n\n"
                "## SKILL ROUTING (use exactly ONE skill per task)\n"
                "- Azure DevOps READ (list/get/search items): `backlog_manager`\n"
                "- Azure DevOps WRITE (create items): `requirements_engineer`\n"
                "- Web research with page reading: `researcher`\n"
                "- Quick web search (snippets only): `search`\n"
                "- Deep multi-source research: `deep_researcher`\n\n"
                "## JSON FORMAT (output ONLY valid JSON)\n"
                "{\n"
                '  "description": "Brief plan summary",\n'
                '  "steps": [\n'
                '    {"id": "1", "label": "...", "executor": "agent|litellm", '
                '"action": "tool|completion|memory", "tool": "...", "args": {}}\n'
                "  ]\n"
                "}\n\n"
                "## RULES\n"
                "1. DELEGATE via consult_expert - never guess answers needing current data\n"
                "2. FINAL STEP must be action=completion, executor=litellm\n"
                "3. consult_expert args: skill (from list above), goal (what to find)\n"
                "4. Simple questions (translations, math, syntax) = single completion step\n"
                "5. If action=tool, executor MUST be agent\n\n"
                "## EXAMPLES\n"
                '"What is hello in French?" → direct answer (you know this):\n'
                '{"description":"Translation","steps":[{"id":"1","label":"Answer",'
                '"executor":"litellm","action":"completion","args":{}}]}\n\n'
                '"Research Python 3.12" → delegate then answer:\n'
                '{"description":"Research","steps":[{"id":"1","label":"Research",'
                '"executor":"agent","action":"tool","tool":"consult_expert",'
                '"args":{"skill":"researcher","goal":"Python 3.12 features"}},'
                '{"id":"2","label":"Answer","executor":"litellm",'
                '"action":"completion","args":{}}]}\n'
            ),
        )

        # Sanitize user input to reduce prompt injection risk
        sanitized_prompt = _sanitize_user_input(request.prompt)

        user_message = AgentMessage(
            role="user",
            content=(
                f"### AVAILABLE TOOLS\n{available_tools_text}\n\n"
                f"### USER REQUEST\n{sanitized_prompt}\n\n"
                f"### CONTEXT (History)\n{history_text}\n\n"
                f"### METADATA\n{metadata_text}\n\n"
                "Create the execution plan now."
            ),
        )

        model_name = self._model_name
        if model_name is None:
            settings = getattr(self._litellm, "_settings", None)
            model_name = getattr(settings, "model_planner", None)

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
