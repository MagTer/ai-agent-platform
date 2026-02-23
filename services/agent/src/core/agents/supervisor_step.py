"""Step supervisor agent with LLM-based intelligent evaluation."""

from __future__ import annotations

import logging
from typing import Literal

import orjson

from core.models.pydantic_schemas import SupervisorDecision, TraceContext
from core.observability.logging import log_event
from core.observability.tracing import current_trace_ids, start_span
from core.runtime.litellm_client import LiteLLMClient
from shared.models import AgentMessage, PlanStep, StepOutcome, StepResult

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
        retry_count: int = 0,
    ) -> tuple[StepOutcome, str, str | None]:
        """Evaluate whether a step execution satisfies its intended goal.

        Returns a 4-level StepOutcome to drive the self-correction loop:
        - SUCCESS: Step completed, proceed to next
        - RETRY: Transient error, retry with feedback (only if retry_count < 1)
        - REPLAN: Step failed, generate new plan
        - ABORT: Critical failure, stop execution

        Args:
            step: The plan step that was executed.
            step_result: The result from executing the step.
            retry_count: Number of times this step has been retried.

        Returns:
            A tuple of (outcome, reason, suggested_fix) where:
            - outcome is a StepOutcome enum value
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
                "a TOOL step execution succeeded or encountered issues.\n\n"
                "## OUTCOME LEVELS\n"
                "You must choose ONE of these outcomes:\n"
                "- **success**: Step completed successfully. Use for:\n"
                "  - Tool executed and returned useful data\n"
                "  - 'No results found' (this IS valid information)\n"
                "  - Partial information (some data is better than none)\n"
                "  - Skill asking user a clarifying question (needs user input to proceed)\n"
                "  - Output contains [AWAITING_USER_INPUT:*] marker\n"
                "  - Output asks user to choose between options\n\n"
                "- **retry**: Transient error that might succeed on retry. Use for:\n"
                "  - Timeout errors\n"
                "  - Rate limits (429 errors)\n"
                "  - Temporary network issues\n"
                "  Note: Only suggest retry if the error seems transient.\n\n"
                "- **replan**: Step failed in a way that needs a different approach. Use for:\n"
                "  - Authentication/permission errors (need different credentials)\n"
                "  - Resource not found (need to search differently)\n"
                "  - Invalid arguments (need different parameters)\n"
                "  - NOTE: Do NOT use replan when skill is asking user for input!\n\n"
                "- **abort**: Critical failure, should stop execution. Use for:\n"
                "  - Security violations\n"
                "  - Data corruption risks\n"
                "  - Unrecoverable system errors\n\n"
                "## RESPONSE FORMAT (Strict JSON Only)\n"
                '{"outcome": "success" | "retry" | "replan" | "abort", '
                '"reason": "Brief explanation", '
                '"suggested_fix": "Optional: specific action to fix the issue"}\n\n'
                "## IMPORTANT\n"
                "Be LENIENT. Default to 'success' unless there's a clear failure. "
                "'No results' is valid information, not a failure. "
                "A skill asking for user input is SUCCESS, not a failure. "
                "When in doubt, choose 'success'.\n\n"
                "If you choose 'retry' or 'replan', provide a specific suggested_fix."
            ),
        )

        user_prompt = AgentMessage(
            role="user",
            content=(
                f"## STEP DETAILS\n"
                f"- **Label**: {step_label}\n"
                f"- **Tool**: {step_tool or 'N/A'}\n"
                f"- **Arguments**: {orjson.dumps(step_args, default=str).decode()}\n\n"
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
                outcome, reason, suggested_fix = self._parse_response(response, retry_count)

                # Add outcome to span
                span.set_attribute("outcome", outcome.value)
                if suggested_fix:
                    span.set_attribute("suggested_fix", suggested_fix)
                span.set_attribute("reason", reason)

                # Log the decision (map outcome to legacy decision format for logging)
                legacy_decision: Literal["ok", "adjust"] = (
                    "ok" if outcome == StepOutcome.SUCCESS else "adjust"
                )
                log_event(
                    SupervisorDecision(
                        item_id=step.id,
                        decision=legacy_decision,
                        comments=f"[{outcome.value}] {reason}",
                        trace=TraceContext(**current_trace_ids()),
                    )
                )

                LOGGER.info(
                    "Supervisor reviewed step '%s': %s - %s%s",
                    step_label,
                    outcome.value,
                    reason,
                    f" (fix: {suggested_fix})" if suggested_fix else "",
                )

                return outcome, reason, suggested_fix

            except Exception as exc:
                from core.observability.error_codes import classify_exception

                LOGGER.exception("Supervisor review failed for step '%s'", step_label)
                error_code = classify_exception(exc)
                span.set_attribute("error", str(exc))
                span.set_attribute("error_code", error_code.value)
                # On failure, be conservative - suggest retry first, then replan
                # This ensures failures are surfaced but gives transient errors a chance.
                if retry_count < 1:
                    return (
                        StepOutcome.RETRY,
                        f"Supervisor unavailable - retry recommended: {exc}",
                        "Verify the step output is correct or retry the operation",
                    )
                return (
                    StepOutcome.REPLAN,
                    f"Supervisor unavailable after retry - replan needed: {exc}",
                    "Generate a new plan with a different approach",
                )

    def _parse_response(
        self, response: str, retry_count: int = 0
    ) -> tuple[StepOutcome, str, str | None]:
        """Parse the LLM response into outcome, reason, and suggested_fix.

        Args:
            response: Raw LLM response text.
            retry_count: Current retry count (affects whether RETRY is allowed).

        Returns:
            Tuple of (outcome, reason, suggested_fix).
        """
        try:
            # Try direct JSON parse
            data = orjson.loads(response)
        except orjson.JSONDecodeError:
            # Try to extract JSON from response
            start = response.find("{")
            end = response.rfind("}")
            if start != -1 and end != -1:
                try:
                    data = orjson.loads(response[start : end + 1])
                except orjson.JSONDecodeError:
                    LOGGER.warning("Failed to parse supervisor response: %s", response)
                    return StepOutcome.SUCCESS, "Could not parse supervisor response", None
            else:
                LOGGER.warning("No JSON found in supervisor response: %s", response)
                return StepOutcome.SUCCESS, "No JSON in supervisor response", None

        # Support both new 'outcome' and legacy 'decision' fields
        raw_outcome = data.get("outcome") or data.get("decision", "success")
        reason = data.get("reason", "No reason provided")
        suggested_fix = data.get("suggested_fix")  # Optional field

        # Map raw outcome to StepOutcome enum
        outcome_map = {
            "success": StepOutcome.SUCCESS,
            "ok": StepOutcome.SUCCESS,  # Legacy compatibility
            "retry": StepOutcome.RETRY,
            "replan": StepOutcome.REPLAN,
            "adjust": StepOutcome.REPLAN,  # Legacy compatibility
            "abort": StepOutcome.ABORT,
        }

        outcome = outcome_map.get(raw_outcome.lower(), StepOutcome.SUCCESS)

        # If RETRY but we've already retried, escalate to REPLAN
        if outcome == StepOutcome.RETRY and retry_count >= 1:
            LOGGER.info("RETRY requested but retry_count=%d, escalating to REPLAN", retry_count)
            outcome = StepOutcome.REPLAN

        return outcome, reason, suggested_fix


__all__ = ["StepSupervisorAgent"]
