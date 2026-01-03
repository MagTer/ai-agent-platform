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
    ) -> tuple[Literal["ok", "adjust"], str, str | None]:
        """Evaluate whether a step execution satisfies its intended goal.

        Args:
            step: The plan step that was executed.
            step_result: The result from executing the step.

        Returns:
            A tuple of (decision, reason, suggested_fix) where:
            - decision is "ok" or "adjust"
            - reason explains the decision
            - suggested_fix is an optional actionable suggestion for how to fix the issue
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
                "a TOOL step execution encountered a REAL failure that requires re-planning.\n\n"
                "## WHAT TO REJECT (decision: adjust)\n"
                "ONLY reject if there is a CLEAR technical failure:\n"
                "- Error messages (API errors, rate limits, authentication failures)\n"
                "- Tool crashes or exceptions\n"
                "- Permission denied / access restricted\n"
                "- Connection timeouts\n\n"
                "## WHAT TO ACCEPT (decision: ok)\n"
                "Accept these as valid outcomes - they are NOT failures:\n"
                "- 'No results found' - this is valid information, not a failure\n"
                "- Empty search results - the tool worked, just found nothing\n"
                "- Partial information - some data is better than none\n"
                "- The tool executed and returned a response (even if brief)\n\n"
                "## RESPONSE FORMAT (Strict JSON Only)\n"
                '{"decision": "ok" | "adjust", "reason": "Brief explanation", '
                '"suggested_fix": "Optional: specific action to fix the issue"}\n\n'
                "## IMPORTANT\n"
                "Be LENIENT. Only reject for TECHNICAL failures. "
                "'No results' is a valid answer, not a failure. "
                "When in doubt, choose 'ok'.\n\n"
                "If you choose 'adjust', provide a specific suggested_fix that tells "
                "the planner exactly what to try differently "
                "(e.g., 'Use a different API endpoint', "
                "'Check authentication token', 'Retry with smaller batch size')."
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
                decision, reason, suggested_fix = self._parse_response(response)

                # Add decision to span
                span.set_attribute("decision", decision)
                if suggested_fix:
                    span.set_attribute("suggested_fix", suggested_fix)
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
                    "Supervisor reviewed step '%s': %s - %s%s",
                    step_label,
                    decision,
                    reason,
                    f" (fix: {suggested_fix})" if suggested_fix else "",
                )

                return decision, reason, suggested_fix

            except Exception as exc:
                LOGGER.exception("Supervisor review failed for step '%s'", step_label)
                span.set_attribute("error", str(exc))
                # On failure, assume OK to avoid blocking execution
                return "ok", f"Supervisor error (defaulting to ok): {exc}", None

    def _parse_response(self, response: str) -> tuple[Literal["ok", "adjust"], str, str | None]:
        """Parse the LLM response into decision, reason, and suggested_fix.

        Args:
            response: Raw LLM response text.

        Returns:
            Tuple of (decision, reason, suggested_fix).
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
                    return "ok", "Could not parse supervisor response", None
            else:
                LOGGER.warning("No JSON found in supervisor response: %s", response)
                return "ok", "No JSON in supervisor response", None

        decision = data.get("decision", "ok")
        reason = data.get("reason", "No reason provided")
        suggested_fix = data.get("suggested_fix")  # Optional field

        # Validate decision value
        if decision not in ("ok", "adjust"):
            LOGGER.warning("Invalid decision '%s', defaulting to 'ok'", decision)
            decision = "ok"

        return decision, reason, suggested_fix


__all__ = ["StepSupervisorAgent"]
