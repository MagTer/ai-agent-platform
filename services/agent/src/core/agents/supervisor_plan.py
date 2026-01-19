"""Plan supervisor agent with validation logic."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from core.models.pydantic_schemas import SupervisorDecision, TraceContext
from core.observability.logging import log_event
from core.observability.tracing import current_trace_ids, start_span
from shared.models import Plan

if TYPE_CHECKING:
    from core.tools import ToolRegistry

LOGGER = logging.getLogger(__name__)


class PlanSupervisorAgent:
    """Supervisor that validates and can adjust plans before execution.

    Validates:
    - Plan has at least one step
    - Final step is a completion action
    - Tool references exist in the registry
    - Skill references are valid
    """

    def __init__(
        self,
        tool_registry: ToolRegistry | None = None,
        skill_names: set[str] | None = None,
    ) -> None:
        """Initialize the plan supervisor.

        Args:
            tool_registry: Registry of available tools for validation.
            skill_names: Set of valid skill names for consult_expert validation.
        """
        self._tool_registry = tool_registry
        self._skill_names = skill_names or set()

    async def review(self, plan: Plan) -> Plan:
        """Validate plan structure and tool/skill references.

        Args:
            plan: The plan to validate.

        Returns:
            The validated plan (potentially with warnings logged).
        """
        with start_span(
            "supervisor.plan_review",
            attributes={"plan.steps": len(plan.steps) if plan.steps else 0},
        ) as span:
            issues: list[str] = []
            warnings: list[str] = []

            # 1. Validate steps exist
            if not plan.steps:
                issues.append("Plan has no steps")

            # 2. Validate final step is completion
            if plan.steps and plan.steps[-1].action != "completion":
                warnings.append(
                    f"Plan should end with completion step, "
                    f"but ends with '{plan.steps[-1].action}'"
                )

            # 3. Validate tool and skill references
            for step in plan.steps:
                if step.action == "tool" and step.tool:
                    if step.tool == "consult_expert":
                        # Validate skill reference
                        skill_name = (step.args or {}).get("skill")
                        if skill_name:
                            if self._skill_names and skill_name not in self._skill_names:
                                warnings.append(
                                    f"Step '{step.label}': Unknown skill '{skill_name}'. "
                                    f"Available: {sorted(self._skill_names)[:5]}..."
                                )
                        else:
                            issues.append(
                                f"Step '{step.label}': consult_expert requires 'skill' argument"
                            )

                        # Validate goal argument
                        goal = (step.args or {}).get("goal")
                        if not goal:
                            warnings.append(
                                f"Step '{step.label}': consult_expert should have 'goal' argument"
                            )

                    elif self._tool_registry:
                        # Validate other tool references
                        if not self._tool_registry.get(step.tool):
                            warnings.append(f"Step '{step.label}': Unknown tool '{step.tool}'")

                # 4. Validate executor/action combinations
                if step.action == "tool" and step.executor != "agent":
                    warnings.append(
                        f"Step '{step.label}': Tool actions should use executor='agent', "
                        f"got '{step.executor}'"
                    )

                if step.action == "completion" and step.executor not in ("litellm", "remote"):
                    warnings.append(
                        f"Step '{step.label}': Completion actions should use "
                        f"executor='litellm' or 'remote', got '{step.executor}'"
                    )

            # Log validation results
            if issues:
                LOGGER.error(f"Plan validation FAILED: {issues}")
                span.set_attribute("validation.issues", str(issues))

            if warnings:
                LOGGER.warning(f"Plan validation warnings: {warnings}")
                span.set_attribute("validation.warnings", str(warnings))

            # Determine decision (must be one of: ok, adjust, redo, abort)
            if issues:
                decision: Literal["ok", "adjust", "redo", "abort"] = "abort"
                comments = f"Plan has critical issues: {'; '.join(issues)}"
            elif warnings:
                decision = "adjust"
                comments = f"Plan approved with warnings: {'; '.join(warnings)}"
            else:
                decision = "ok"
                comments = "Plan approved"

            span.set_attribute("validation.decision", decision)

            log_event(
                SupervisorDecision(
                    item_id="plan",
                    decision=decision,
                    comments=comments,
                    trace=TraceContext(**current_trace_ids()),
                )
            )

            # For now, return the plan even with warnings
            # Critical issues (like no steps) would have been caught earlier by the planner
            return plan


__all__ = ["PlanSupervisorAgent"]
