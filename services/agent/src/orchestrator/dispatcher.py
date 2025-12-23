import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from re import Pattern
from typing import Any, TypedDict

from core.core.litellm_client import LiteLLMClient
from core.db.models import Context, Conversation
from shared.models import AgentMessage, AgentRequest, Plan, PlanStep, RoutingDecision
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .skill_loader import SkillLoader

LOGGER = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    """
    Result of the dispatching process.

    If `plan` is present, it indicates a 'Fast Path' where the execution plan
    is already determined.

    If `plan` is None, it indicates a 'Slow Path' where the generic PlannerAgent
    should be invoked.
    """

    request_id: str
    original_message: str
    decision: RoutingDecision
    plan: Plan | None = None
    skill_name: str | None = None
    metadata: dict[str, Any] | None = None
    response: str | None = None  # The final agent response


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
        return {"title": match.group(1), "description": "Created via Fast Path"}

    async def route_message(
        self,
        session_id: str,
        message: str,
        platform: str = "web",
        platform_id: str | None = None,
        db_session: AsyncSession | None = None,
        agent_service: Any = None,  # Injected AgentService
    ) -> DispatchResult:
        """
        Routes a user message using Tri-State Logic:
        1. FAST_PATH: Regex or exact skill match.
        2. CHAT: General conversation (LLM Classified).
        3. AGENTIC: Complex task (LLM Classified fallback).

        Also handles Conversation resolution and persistence if db_session and agent_service are provided.
        """
        stripped_message = message.strip()
        request_id = str(uuid.uuid4())

        # 0. Conversation Management
        conversation_id = session_id
        if db_session and platform_id:
            # Resolve Conversation ID from Platform ID
            stmt = select(Conversation).where(
                Conversation.platform == platform, Conversation.platform_id == platform_id
            )
            result = await db_session.execute(stmt)
            conversation = result.scalar_one_or_none()

            if conversation:
                conversation_id = str(conversation.id)
            else:
                # Create new Conversation
                # We need a context. Use 'default'.
                # Retrieve default context
                ctx_stmt = select(Context).where(Context.name == "default")
                ctx_res = await db_session.execute(ctx_stmt)
                context = ctx_res.scalar_one_or_none()

                if context:
                    # Create conversation record immediately so we have the ID for future lookups
                    new_conv = Conversation(
                        id=uuid.uuid4(),
                        platform=platform,
                        platform_id=platform_id,
                        context_id=context.id,
                        current_cwd=context.default_cwd,
                        conversation_metadata={}
                    )
                    db_session.add(new_conv)
                    await db_session.flush()
                    conversation_id = str(new_conv.id)
                    LOGGER.info(f"Created new conversation {conversation_id} for platform {platform}:{platform_id}")
                else:
                    LOGGER.warning("Default context not found. Passing UUID to AgentService to bootstrap.")
                    conversation_id = str(uuid.uuid4())

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
        system_prompt = (
            "You are a router. Classify user input as 'CHAT' "
            "(greetings, simple chit-chat) "
            "or 'TASK' (actions, tools, planning, analyzing files, searching, verifying facts). "
            "If the user asks to check, verify, or search, MUST return TASK. "
            "Reply ONLY with the word CHAT or TASK."
        )

        decision = RoutingDecision.AGENTIC
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
                decision = RoutingDecision.CHAT
            else:
                decision = RoutingDecision.AGENTIC

        except Exception as e:
            LOGGER.error(f"Intent classification failed: {e}")
            decision = RoutingDecision.AGENTIC

        # 4. Execution Propagation (If Service Provided)
        response_text = None
        if agent_service and db_session:
            # Prepare Metadata
            metadata = {
                "routing_decision": decision,
                "platform": platform,
                "platform_id": platform_id
            }

            agent_req = AgentRequest(
                prompt=message,
                conversation_id=conversation_id,
                metadata=metadata
            )

            # AgentService handles persistence and execution
            agent_resp = await agent_service.handle_request(agent_req, session=db_session)
            response_text = agent_resp.response

        return DispatchResult(
            request_id=request_id,
            original_message=message,
            decision=decision,
            response=response_text
        )
