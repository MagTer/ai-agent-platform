"""Plan supervisor agent with validation logic."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

import orjson
from shared.models import AgentMessage, Plan

from core.models.pydantic_schemas import SupervisorDecision, TraceContext
from core.observability.logging import log_event
from core.observability.tracing import current_trace_ids, start_span

if TYPE_CHECKING:
    from core.core.litellm_client import LiteLLMClient
    from core.tools import ToolRegistry

LOGGER = logging.getLogger(__name__)


class PlanSupervisorAgent:
    """Supervisor that validates and can adjust plans before execution.

    Validates:
    - Plan has at least one step
    - Final step is a completion action (unless plan has skill steps)
    - Tool references exist in the registry
    - Skill references are valid

    Optionally uses LLM for semantic validation of plan quality.
    """

    def __init__(
        self,
        litellm: LiteLLMClient | None = None,
        model_name: str = "supervisor",
        tool_registry: ToolRegistry | None = None,
        skill_names: set[str] | None = None,
    ) -> None:
        """Initialize the plan supervisor.

        Args:
            litellm: LiteLLM client for LLM-based validation. If None, only
                rule-based validation is performed.
            model_name: Model name to use for supervisor calls.
                Defaults to "supervisor" which should be configured as a fast model.
            tool_registry: Registry of available tools for validation.
            skill_names: Set of valid skill names for consult_expert validation.
        """
        self._litellm = litellm
        self._model_name = model_name
        self._tool_registry = tool_registry
        self._skill_names = skill_names or set()

    async def review(self, plan: Plan) -> Plan:
        """Validate plan structure and tool/skill references.

        Also migrates deprecated consult_expert steps to skills-native format.

        Args:
            plan: The plan to validate.

        Returns:
            The validated (and potentially migrated) plan.
        """
        # Migrate deprecated consult_expert steps to skills-native format
        plan = self._migrate_consult_expert_steps(plan)

        with start_span(
            "supervisor.plan_review",
            attributes={"plan.steps": len(plan.steps) if plan.steps else 0},
        ) as span:
            issues: list[str] = []
            warnings: list[str] = []

            # 1. Validate steps exist
            if not plan.steps:
                issues.append("Plan has no steps")

            # 2. Validate final step is completion (skip for skill-based plans)
            if plan.steps and plan.steps[-1].action != "completion":
                has_skill_steps = any(
                    s.executor == "skill" or s.action == "skill" for s in plan.steps
                )
                if not has_skill_steps:
                    warnings.append(
                        f"Plan should end with completion step, "
                        f"but ends with '{plan.steps[-1].action}'"
                    )
                else:
                    LOGGER.debug("Plan ends with skill - completion step not required")

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

            # Optionally run LLM-based validation for deeper semantic checks
            if self._litellm and decision != "abort":
                llm_decision, llm_issues = await self._llm_review(plan)
                if llm_issues:
                    warnings.extend(llm_issues)
                    if llm_decision == "redo":
                        decision = "redo"
                        comments = f"LLM review suggests redo: {'; '.join(llm_issues)}"
                    elif llm_decision == "adjust" and decision == "ok":
                        decision = "adjust"
                        comments = f"LLM review: {'; '.join(llm_issues)}"

                    span.set_attribute("validation.llm_decision", llm_decision)
                    span.set_attribute("validation.llm_issues", str(llm_issues))

            # For now, return the plan even with warnings
            # Critical issues (like no steps) would have been caught earlier by the planner
            return plan

    async def _llm_review(self, plan: Plan) -> tuple[str, list[str]]:
        """Use LLM to validate plan quality and structure.

        Args:
            plan: The plan to validate.

        Returns:
            Tuple of (decision, issues) where decision is one of
            'ok', 'adjust', or 'redo', and issues is a list of problems found.
        """
        if not self._litellm:
            return "ok", []

        system_prompt = AgentMessage(
            role="system",
            content=(
                "You are a Plan Supervisor. Validate the execution plan.\n\n"
                "## VALIDATION RULES\n"
                "1. Plans with skill steps should NOT have a completion step\n"
                "   - Skills output IS the final answer, no synthesis needed\n"
                "2. Plans should have clear, actionable steps\n"
                "3. Tool/skill references should match available capabilities\n"
                "4. Step descriptions should be specific enough to execute\n\n"
                "## RESPONSE FORMAT (JSON only)\n"
                '{"decision": "ok" | "adjust" | "redo", '
                '"issues": ["issue1", "issue2"], '
                '"suggestions": ["suggestion1"]}\n\n'
                "Be LENIENT. Default to 'ok' unless there's a clear problem.\n"
                "'adjust' means minor issues that don't block execution.\n"
                "'redo' means the plan needs to be regenerated."
            ),
        )

        user_prompt = AgentMessage(
            role="user",
            content=f"Validate this plan:\n```json\n{plan.model_dump_json()}\n```",
        )

        try:
            response = await self._litellm.generate(
                [system_prompt, user_prompt],
                model=self._model_name,
            )

            # Parse JSON response
            try:
                data = orjson.loads(response)
            except orjson.JSONDecodeError:
                # Try to extract JSON from response
                start = response.find("{")
                end = response.rfind("}")
                if start != -1 and end != -1:
                    try:
                        data = orjson.loads(response[start : end + 1])
                    except orjson.JSONDecodeError:
                        LOGGER.warning("Failed to parse LLM review response: %s", response)
                        return "ok", []
                else:
                    LOGGER.warning("No JSON in LLM review response: %s", response)
                    return "ok", []

            decision = data.get("decision", "ok")
            issues = data.get("issues", [])

            LOGGER.info("LLM plan review: decision=%s, issues=%s", decision, issues)
            return decision, issues

        except Exception as exc:
            LOGGER.warning("LLM plan review failed: %s", exc)
            return "ok", []

    def _migrate_consult_expert_steps(self, plan: Plan) -> Plan:
        """Migrate deprecated consult_expert steps to skills-native format.

        Converts steps like:
            {"executor": "agent", "action": "tool", "tool": "consult_expert",
             "args": {"skill": "researcher", "goal": "..."}}

        To:
            {"executor": "skill", "action": "skill", "tool": "researcher",
             "args": {"goal": "..."}}

        Args:
            plan: The plan to migrate.

        Returns:
            Plan with migrated steps.
        """
        if not plan.steps:
            return plan

        migrated_steps = []
        migration_count = 0

        for step in plan.steps:
            if step.tool == "consult_expert" and step.args:
                # Extract skill name
                skill_name = step.args.get("skill")

                if skill_name:
                    # Create new args without 'skill' (skill is now in 'tool')
                    new_args = {k: v for k, v in step.args.items() if k != "skill"}

                    # Create migrated step
                    from shared.models import PlanStep

                    migrated_step = PlanStep(
                        id=step.id,
                        label=step.label,
                        executor="skill",
                        action="skill",
                        tool=skill_name,
                        args=new_args,
                        description=step.description,
                        provider=step.provider,
                        depends_on=step.depends_on,
                    )
                    migrated_steps.append(migrated_step)
                    migration_count += 1

                    LOGGER.warning(
                        "Migrated deprecated consult_expert step '%s' to skill '%s'",
                        step.label,
                        skill_name,
                    )
                else:
                    # No skill specified, keep original
                    migrated_steps.append(step)
            else:
                migrated_steps.append(step)

        if migration_count > 0:
            LOGGER.info(
                "Migrated %d deprecated consult_expert steps to skills-native format",
                migration_count,
            )

        return Plan(steps=migrated_steps, description=plan.description)


__all__ = ["PlanSupervisorAgent"]
