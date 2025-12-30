"""Step supervisor agent with LLM-based intelligent evaluation."""

from __future__ import annotations

import json
import logging
from typing import Literal

from shared.models import AgentMessage, PlanStep, StepResult

from core.core.litellm_client import LiteLLMClient
from core.models.pydantic_schemas import SupervisorDecision, TraceContext
from core.observability.logging import log_event
from core.observability.tracing import current_trace_ids, start_span

LOGGER = logging.getLogger(__name__)


class StepSupervisorAgent:
    """Validate step execution results using LLM-based semantic analysis.

    The supervisor evaluates whether a step's actual output satisfies its
    intended goal, detecting issues like empty results, hidden errors,
    or mismatched intent vs output.
    """

    def __init__(
        self,
        litellm: LiteLLMClient,
        model_name: str | None = "supervisor",
    ) -> None:
        """Initialize the supervisor with LLM client.

        Args:
            litellm: The LiteLLM client for making LLM calls.
            model_name: Model name to use for supervisor calls.
                       Defaults to "supervisor" which should be configured
                       in LiteLLM as a lightweight, fast model.
        """
        self._litellm = litellm
        self._model_name = model_name

    async def review(
        self,
        step: PlanStep,
        step_result: StepResult,
    ) -> tuple[Literal["ok", "adjust"], str]:
        """Evaluate whether a step execution satisfies its intended goal.

        Args:
            step: The plan step that was executed.
            step_result: The result from executing the step.

        Returns:
            A tuple of (decision, reason) where decision is "ok" or "adjust",
            and reason explains the decision.
        """
        # Extract key information for evaluation
        step_label = step.label
        step_args = step.args
        step_tool = step.tool
        execution_status = step_result.status
        output = step_result.result.get("output", "")
        output_preview = str(output)[:2000] if output else "(empty output)"

        # Build evaluation prompt
        system_prompt = AgentMessage(
            role="system",
            content=(
                "You are a Step Supervisor Agent. Your job is to evaluate whether "
                "a step execution truly satisfied its intended goal.\n\n"
                "## EVALUATION CRITERIA\n"
                "1. **Empty Results**: Did the step return empty or 'no results found'?\n"
                "2. **Hidden Errors**: Are there error messages hidden in the output text?\n"
                "3. **Intent Mismatch**: Does the output actually address what the step "
                "was supposed to do?\n"
                "4. **Hallucination Signs**: Does the output seem fabricated rather than "
                "retrieved from a real source?\n\n"
                "## RESPONSE FORMAT (Strict JSON Only)\n"
                "You must output a single JSON object with no additional text:\n"
                '{"decision": "ok" | "adjust", "reason": "Brief explanation"}\n\n'
                "## DECISION GUIDELINES\n"
                "- **ok**: The step executed successfully and the output is useful.\n"
                "- **adjust**: The step failed logically (even if no exception). "
                "Examples: empty search results, API rate limits, permission denied, "
                "tool returned 'not found'.\n\n"
                "Be concise. The reason should be 1-2 sentences max."
            ),
        )

        user_prompt = AgentMessage(
            role="user",
            content=(
                f"## STEP DETAILS\n"
                f"- **Label**: {step_label}\n"
                f"- **Tool**: {step_tool or 'N/A'}\n"
                f"- **Arguments**: {json.dumps(step_args, default=str)}\n\n"
                f"## EXECUTION RESULT\n"
                f"- **Status**: {execution_status}\n"
                f"- **Output**:\n```\n{output_preview}\n```\n\n"
                "Evaluate this step execution now."
            ),
        )

        with start_span(
            "supervisor.step_review",
            attributes={
                "step_id": step.id,
                "step_label": step_label,
                "step_tool": step_tool or "",
                "execution_status": execution_status,
            },
        ) as span:
            try:
                # Call LLM for evaluation
                response = await self._litellm.generate(
                    [system_prompt, user_prompt],
                    model=self._model_name,
                )

                # Parse JSON response
                decision, reason = self._parse_response(response)

                # Add decision to span
                span.set_attribute("decision", decision)
                span.set_attribute("reason", reason)

                # Log the decision
                log_event(
                    SupervisorDecision(
                        item_id=step.id,
                        decision=decision,
                        comments=reason,
                        trace=TraceContext(**current_trace_ids()),
                    )
                )

                LOGGER.info(
                    "Supervisor reviewed step '%s': %s - %s",
                    step_label,
                    decision,
                    reason,
                )

                return decision, reason

            except Exception as exc:
                LOGGER.exception("Supervisor review failed for step '%s'", step_label)
                span.set_attribute("error", str(exc))
                # On failure, assume OK to avoid blocking execution
                return "ok", f"Supervisor error (defaulting to ok): {exc}"

    def _parse_response(self, response: str) -> tuple[Literal["ok", "adjust"], str]:
        """Parse the LLM response into decision and reason.

        Args:
            response: Raw LLM response text.

        Returns:
            Tuple of (decision, reason).
        """
        try:
            # Try direct JSON parse
            data = json.loads(response)
        except json.JSONDecodeError:
            # Try to extract JSON from response
            start = response.find("{")
            end = response.rfind("}")
            if start != -1 and end != -1:
                try:
                    data = json.loads(response[start : end + 1])
                except json.JSONDecodeError:
                    LOGGER.warning("Failed to parse supervisor response: %s", response)
                    return "ok", "Could not parse supervisor response"
            else:
                LOGGER.warning("No JSON found in supervisor response: %s", response)
                return "ok", "No JSON in supervisor response"

        decision = data.get("decision", "ok")
        reason = data.get("reason", "No reason provided")

        # Validate decision value
        if decision not in ("ok", "adjust"):
            LOGGER.warning("Invalid decision '%s', defaulting to 'ok'", decision)
            decision = "ok"

        return decision, reason


__all__ = ["StepSupervisorAgent"]
