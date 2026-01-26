"""Unified Orchestrator - combines routing and planning in one LLM call.

This replaces the separate IntentClassifier + PlannerAgent flow with a single
model call that either:
1. Returns a direct answer (for simple questions)
2. Returns a plan (for requests needing skills)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from shared.models import AgentMessage, Plan, PlanStep

if TYPE_CHECKING:
    from core.core.litellm_client import LiteLLMClient

LOGGER = logging.getLogger(__name__)


@dataclass
class OrchestrationResult:
    """Result from the unified orchestrator."""

    # Either direct_answer OR plan will be set, not both
    direct_answer: str | None = None
    plan: Plan | None = None

    @property
    def is_direct(self) -> bool:
        """True if this is a direct answer (no plan needed)."""
        return self.direct_answer is not None


def _build_system_prompt(available_skills_text: str) -> str:
    """Build the system prompt for the unified orchestrator."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    year = datetime.now().year

    return f"""You are a smart orchestrator. Current time: {now}.

Based on the user's request, do ONE of:

## OPTION 1: DIRECT ANSWER
If you can answer immediately (translations, math, general knowledge, greetings),
respond with plain text.

## OPTION 2: PLAN (JSON)
If the request needs external tools/skills (web search, smart home, Azure DevOps,
CURRENT data from {year}), return a JSON plan:

```json
{{"description": "Brief summary", "steps": [{{"id": "1", "label": "Step label",
"executor": "skill", "action": "skill", "tool": "skill_name",
"args": {{"goal": "what to accomplish"}}}}]}}
```

## AVAILABLE SKILLS
{available_skills_text}

## SKILL ROUTING
- Web research: `researcher`
- Quick search (snippets): `search`
- Deep research: `deep_research`
- TIBP/internal wiki/policies: `work/tibp_researcher`
- Smart home: `general/homey`
- Price tracking: `general/priser`
- Azure DevOps READ: `backlog_manager`
- Azure DevOps PLAN: `requirements_drafter`
- Azure DevOps WRITE: `requirements_writer`

## RULES
1. Simple questions (translations, math, greetings) = DIRECT ANSWER
2. Anything needing current/live data = PLAN (your knowledge is outdated)
3. Single skill = just that skill step (no completion step needed)
4. Multiple skills = all skill steps (completion only if synthesizing results)
5. NEVER wrap direct answers in JSON
6. NEVER hallucinate current data - if unsure, use a skill

## EXAMPLES

User: "What is hello in French?"
Bonjour

User: "What's 15 * 7?"
105

User: "Research the latest AI news"
```json
{{"description": "AI news research", "steps": [
  {{"id": "1", "label": "Research", "executor": "skill", "action": "skill",
    "tool": "researcher", "args": {{"goal": "Latest AI news"}}}}
]}}
```

User: "Turn off the kitchen lights"
```json
{{"description": "Smart home control", "steps": [
  {{"id": "1", "label": "Control lights", "executor": "skill", "action": "skill",
    "tool": "general/homey", "args": {{"goal": "Turn off the kitchen lights"}}}}
]}}
```"""


class UnifiedOrchestrator:
    """Single LLM call that either answers directly or returns a plan."""

    def __init__(self, litellm: LiteLLMClient, model_name: str | None = None) -> None:
        self._litellm = litellm
        self._model_name = model_name

    async def process(
        self,
        prompt: str,
        *,
        history: list[AgentMessage] | None = None,
        available_skills_text: str = "",
    ) -> OrchestrationResult:
        """Process a user prompt and return either a direct answer or a plan.

        Args:
            prompt: The user's input
            history: Optional conversation history
            available_skills_text: Formatted list of available skills

        Returns:
            OrchestrationResult with either direct_answer or plan set
        """
        system_prompt = _build_system_prompt(available_skills_text)

        # Build messages
        messages = [AgentMessage(role="system", content=system_prompt)]

        # Add history if provided (last few messages for context)
        if history:
            for msg in history[-6:]:  # Last 6 messages for context
                messages.append(msg)

        # Add current user message
        messages.append(AgentMessage(role="user", content=prompt))

        # Get model name
        model_name = self._model_name
        if model_name is None:
            settings = getattr(self._litellm, "_settings", None)
            model_name = getattr(settings, "model_planner", None)

        LOGGER.info("UnifiedOrchestrator processing: %s...", prompt[:50])

        try:
            response = await self._litellm.generate(messages, model=model_name)
            return self._parse_response(response)
        except Exception as e:
            LOGGER.error("UnifiedOrchestrator failed: %s", e)
            # Fallback: treat as needing agentic handling
            return OrchestrationResult(
                plan=Plan(
                    description="Fallback plan due to orchestrator error",
                    steps=[
                        PlanStep(
                            id="1",
                            label="Research",
                            executor="skill",
                            action="skill",
                            tool="researcher",
                            args={"goal": prompt},
                        )
                    ],
                )
            )

    def _parse_response(self, response: str) -> OrchestrationResult:
        """Parse the LLM response into an OrchestrationResult."""
        response = response.strip()

        # Check if it's JSON (plan)
        plan = self._try_parse_plan(response)
        if plan:
            LOGGER.info(
                "UnifiedOrchestrator returned plan: %s (%d steps)",
                plan.description,
                len(plan.steps),
            )
            return OrchestrationResult(plan=plan)

        # Otherwise it's a direct answer
        LOGGER.info("UnifiedOrchestrator returned direct answer (%d chars)", len(response))
        return OrchestrationResult(direct_answer=response)

    def _try_parse_plan(self, response: str) -> Plan | None:
        """Try to parse response as a plan. Returns None if not a plan."""
        # Check for JSON indicators
        if not ("{" in response and "}" in response):
            return None

        # Extract JSON from response
        json_str = response

        # Handle markdown code fences
        if "```json" in json_str:
            try:
                json_str = json_str.split("```json")[1].split("```")[0]
            except IndexError:
                pass
        elif "```" in json_str:
            try:
                json_str = json_str.split("```")[1].split("```")[0]
            except IndexError:
                pass

        # Try to find JSON object
        start = json_str.find("{")
        end = json_str.rfind("}")
        if start == -1 or end == -1:
            return None

        json_str = json_str[start : end + 1]

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return None

        # Check if it looks like a plan
        if "steps" not in data and "plan" not in data:
            return None

        # Handle {"plan": [...]} format
        if "plan" in data and "steps" not in data:
            data["steps"] = data.pop("plan")

        # Validate and create Plan
        try:
            steps = data.get("steps", [])
            if not isinstance(steps, list) or not steps:
                return None

            plan_steps = []
            for i, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue

                # Ensure required fields with defaults
                plan_step = PlanStep(
                    id=str(step.get("id", i + 1)),
                    label=step.get("label", f"Step {i + 1}"),
                    executor=step.get("executor", "skill"),
                    action=step.get("action", "skill"),
                    tool=step.get("tool") or step.get("skill"),
                    args=step.get("args", {}),
                    description=step.get("description"),
                )
                plan_steps.append(plan_step)

            if not plan_steps:
                return None

            return Plan(
                description=data.get("description", "Generated plan"),
                steps=plan_steps,
            )

        except Exception as e:
            LOGGER.warning("Failed to parse plan: %s", e)
            return None


__all__ = ["UnifiedOrchestrator", "OrchestrationResult"]
