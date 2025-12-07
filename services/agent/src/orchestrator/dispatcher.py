import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from re import Pattern
from typing import Any, TypedDict

from core.core.litellm_client import LiteLLMClient
from shared.models import AgentMessage, Plan, PlanStep, RoutingDecision

from .skill_loader import SkillLoader

LOGGER = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    """
    Result of the dispatching process.

    If `plan` is present, it indicates a 'Fast Path' where the execution plan
    is already determined (e.g. via skill match or regex).

    If `plan` is None, it indicates a 'Slow Path' where the generic PlannerAgent
    should be invoked to generate a plan.
    """

    request_id: str
    original_message: str
    decision: RoutingDecision
    plan: Plan | None = None
    skill_name: str | None = None
    metadata: dict[str, Any] | None = None


class FastPathEntry(TypedDict, total=False):
    """Defines the structure for a fast path entry."""

    pattern: Pattern[str]  # re.Pattern
    tool: str
    args: dict[str, Any]
    arg_mapper: Callable[[re.Match], dict[str, Any]]
    description: str


class Dispatcher:
    def __init__(self, skill_loader: SkillLoader, litellm: LiteLLMClient):
        self.skill_loader = skill_loader
        self.litellm = litellm
        # Ensure skills are loaded
        if not self.skill_loader.skills:
            self.skill_loader.load_skills()

        # Define fast path regex patterns
        # These map regex patterns to specific tools and actions
        self._fast_paths: list[FastPathEntry] = [
            {
                "pattern": re.compile(r"^tänd lampan", re.IGNORECASE),
                "tool": "home_automation",
                "args": {"action": "turn_on", "device": "lamp"},
                "description": "Direct command to turn on the lamp.",
            },
            {
                "pattern": re.compile(r"^/ado\s+(.+)", re.IGNORECASE),
                "tool": "azure_devops",
                "arg_mapper": lambda m: self._map_ado_args(m),
                "description": "Create Azure DevOps work item.",
            },
        ]

    def _map_ado_args(self, match: re.Match) -> dict:
        """Map regex match to tool arguments for Azure DevOps."""
        # Naive mapping: assume the whole group is the title
        # In reality, we might want more complex parsing or just pass raw text
        return {"title": match.group(1), "description": "Created via Fast Path"}

    async def route_message(self, session_id: str, message: str) -> DispatchResult:
        """
        Routes a user message using Tri-State Logic:
        1. FAST_PATH: Regex or exact skill match.
        2. CHAT: General conversation (LLM Classified).
        3. AGENTIC: Complex task (LLM Classified fallback).
        """
        stripped_message = message.strip()
        request_id = str(uuid.uuid4())

        # 1. Check Explicit Skills (Slash Commands) -> FAST_PATH
        if stripped_message.startswith("/"):
            parts = stripped_message.split(" ", 1)
            command = parts[0][1:]  # Remove '/'
            skill = self.skill_loader.skills.get(command)
            if skill:
                LOGGER.info(f"Routing to skill (Fast Path): {skill.name}")
                return DispatchResult(
                    request_id=request_id,
                    original_message=message,
                    decision=RoutingDecision.FAST_PATH,
                    skill_name=skill.name,
                    metadata={"tools": skill.tools},
                )

        # 2. Check Regex Fast Paths -> FAST_PATH
        for path in self._fast_paths:
            match = path["pattern"].search(stripped_message)
            if match:
                LOGGER.info(f"Fast Path match: {path['description']}")

                tool_args: dict[str, Any] = {}
                if "args" in path:
                    tool_args = path["args"]
                elif "arg_mapper" in path:
                    tool_args = path["arg_mapper"](match)

                plan_step = PlanStep(
                    id=str(uuid.uuid4()),
                    label=f"Fast Path: {path['description']}",
                    executor="agent",
                    action="tool",
                    tool=path["tool"],
                    args=tool_args,
                    description=path["description"],
                )

                plan = Plan(steps=[plan_step], description="Fast Path Plan")

                return DispatchResult(
                    request_id=request_id,
                    original_message=message,
                    decision=RoutingDecision.FAST_PATH,
                    plan=plan,
                )

        # 3. Intent Classification (LLM) -> CHAT or AGENTIC
        # System prompt optimized for small models
        system_prompt = (
            "You are a router. Classify user input as 'CHAT' "
            "(greetings, simple questions, knowledge) "
            "or 'TASK' (actions, tools, planning, analyzing files). "
            "Reply ONLY with the word CHAT or TASK."
        )

        try:
            # Use a very low max_tokens to force brevity and speed
            classification = await self.litellm.generate(
                messages=[
                    AgentMessage(role="system", content=system_prompt),
                    AgentMessage(role="user", content=stripped_message),
                ],
            )
            classification = classification.strip().upper()
            LOGGER.info(f"Dispatcher classified intent as: {classification}")

            if "CHAT" in classification:
                return DispatchResult(
                    request_id=request_id,
                    original_message=message,
                    decision=RoutingDecision.CHAT,
                )
            else:
                # Default to AGENTIC for TASK or any uncertainty
                return DispatchResult(
                    request_id=request_id,
                    original_message=message,
                    decision=RoutingDecision.AGENTIC,
                )

        except Exception as e:
            LOGGER.error(f"Intent classification failed: {e}")
            # Safety fallback
            return DispatchResult(
                request_id=request_id,
                original_message=message,
                decision=RoutingDecision.AGENTIC,
            )
