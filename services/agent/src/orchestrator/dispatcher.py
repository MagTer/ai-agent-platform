import logging
import re
import shlex
import uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from re import Pattern
from typing import Any, TypedDict

from shared.models import AgentMessage, AgentRequest, Plan, PlanStep, RoutingDecision
from shared.streaming import AgentChunk
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from utils.template import substitute_variables

from core.core.litellm_client import LiteLLMClient
from core.db.models import Context, Conversation, Message

from .skill_loader import SkillLoader

LOGGER = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    """
    Result of the dispatching process.
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

    pattern: Pattern[str]
    tool: str
    args: dict[str, Any]
    arg_mapper: Callable[[re.Match], dict[str, Any]]
    description: str


class Dispatcher:
    def __init__(self, skill_loader: SkillLoader, litellm: LiteLLMClient):
        self.skill_loader = skill_loader
        self.litellm = litellm
        if not self.skill_loader.skills:
            self.skill_loader.load_skills()

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
        return {"title": match.group(1), "description": "Created via Fast Path"}

    async def stream_message(
        self,
        session_id: str,
        message: str,
        platform: str = "web",
        platform_id: str | None = None,
        db_session: AsyncSession | None = None,
        agent_service: Any = None,
    ) -> AsyncGenerator[AgentChunk, None]:
        """
        Routes and streams a user message.
        """
        stripped_message = message.strip()
        # 0. Conversation Management
        conversation_id = await self._resolve_conversation(
            session_id, platform, platform_id, db_session
        )

        # 1. Check Explicit Skills (Slash Commands)
        if stripped_message.startswith("/"):
            try:
                # Use shlex to handle quoted arguments: /skill "arg one" arg2
                parts = shlex.split(stripped_message)
                command = parts[0][1:]  # Remove '/'
                args = parts[1:]

                skill = self.skill_loader.skills.get(command)
                if skill:
                    LOGGER.info(f"Routing to skill: {skill.name} with args {args}")

                    # Variable Substitution
                    rendered_prompt = substitute_variables(skill.prompt_template, args)

                    # Yield detection event
                    yield {
                        "type": "thinking",
                        "content": f"Activated Skill: **{skill.name}**",
                        "tool_call": None,
                        "metadata": {"skill": skill.name, "args": args},
                    }

                    # Execute as AGENTIC but with specific plan/prompt
                    # We treat the rendered instruction as the prompt for the agent
                    # or if the skill is a plan, we inject the plan.
                    # Current SkillLoader assumes skills are just text instructions (or templates).
                    # If it's a plan-based skill, we might need more logic.
                    # For now, assume it's a prompt wrapper.

                    async for chunk in self._stream_agent_execution(
                        prompt=rendered_prompt,
                        conversation_id=conversation_id,
                        db_session=db_session,
                        agent_service=agent_service,
                        metadata={"skill": skill.name, "tools": skill.tools},
                    ):
                        yield chunk
                    return

            except ValueError as e:
                # Argument error matching
                yield {
                    "type": "error",
                    "content": f"Command usage error: {str(e)}",
                    "tool_call": None,
                    "metadata": None,
                }
                return
            except Exception as e:
                LOGGER.error(f"Command parsing error: {e}")
                # Fallthrough to normal classification if parsing fails?
                # Or report error? Report error is safer for explicit commands.
                yield {
                    "type": "error",
                    "content": f"Failed to parse command: {str(e)}",
                    "tool_call": None,
                    "metadata": None,
                }
                return

        # 2. Check Regex Fast Paths
        for path in self._fast_paths:
            match = path["pattern"].search(stripped_message)
            if match:
                LOGGER.info(f"Fast Path match: {path['description']}")
                yield {
                    "type": "thinking",
                    "content": f"Fast Path: {path['description']}",
                    "tool_call": None,
                    "metadata": {"fast_path": path["description"]},
                }

                tool_args = path.get("args", {})
                if "arg_mapper" in path:
                    tool_args = path["arg_mapper"](match)

                # Create a synthetic plan
                plan = Plan(
                    steps=[
                        PlanStep(
                            id=str(uuid.uuid4()),
                            label=f"Fast Path: {path['description']}",
                            executor="agent",
                            action="tool",
                            tool=path["tool"],
                            args=tool_args,
                            description=path["description"],
                        )
                    ],
                    description="Fast Path Plan",
                )

                # Execute with injected plan
                async for chunk in self._stream_agent_execution(
                    prompt=message,
                    conversation_id=conversation_id,
                    db_session=db_session,
                    agent_service=agent_service,
                    metadata={"plan": plan.model_dump()},
                ):
                    yield chunk
                return

        # 3. Intent Classification
        system_prompt = (
            "You are a router. Classify user input as 'CHAT' "
            "(greetings, simple chit-chat) "
            "or 'TASK' (actions, tools, planning, analyzing files, searching, verifying facts). "
            "If the user asks to check, verify, or search, MUST return TASK. "
            "Reply ONLY with the word CHAT or TASK."
        )

        try:
            # We assume classification is fast enough to not need streaming chunks itself
            classification = await self.litellm.generate(
                messages=[
                    AgentMessage(role="system", content=system_prompt),
                    AgentMessage(role="user", content=stripped_message),
                ],
            )
            classification = classification.strip().upper()
            LOGGER.info(f"Dispatcher classified intent as: {classification}")
        except Exception as e:
            LOGGER.error(f"Intent classification failed: {e}")
            classification = "TASK"  # Fallback

        if "CHAT" in classification:
            # Direct Streaming Chat
            # We need history? Dispatcher doesn't fetch history easily
            # We'll rely on AgentService-like logic or just fetch it if needed.
            # But the requirement is to use LLMClient.
            # For strict CHAT, we should append to DB history.

            # Fetch history
            history = []
            if agent_service and db_session:
                history = await agent_service.get_history(conversation_id, db_session)

            # Stream response
            full_content = ""
            async for chunk in self.litellm.stream_chat(
                messages=history + [AgentMessage(role="user", content=stripped_message)]
            ):
                yield chunk
                if chunk["type"] == "content" and chunk["content"]:
                    full_content += chunk["content"]

            # Persist if possible
            if db_session:
                db_session.add(
                    Message(session_id=None, role="user", content=message)
                )  # Simplified, need session ID
                # We need the persistent session object ID (not just conv ID).
                # TODO: Fix _resolve_conversation or handle persistence in AgentService.
                # For now, to avoid duplicating Persistence logic, we skip peristence for CHAT.
                # Ideally we call agent_service.handle_request but it is blocking.
                pass

        else:
            # AGENTIC / TASK
            async for chunk in self._stream_agent_execution(
                prompt=message,
                conversation_id=conversation_id,
                db_session=db_session,
                agent_service=agent_service,
                metadata={"routing_decision": RoutingDecision.AGENTIC},
            ):
                yield chunk

    async def _resolve_conversation(
        self,
        session_id: str,
        platform: str,
        platform_id: str | None,
        db_session: AsyncSession | None,
    ) -> str:
        """Helper to resolve conversation ID."""
        if not db_session:
            return session_id

        conversation_id = session_id
        if platform_id:
            stmt = select(Conversation).where(
                Conversation.platform == platform,
                Conversation.platform_id == platform_id,
            )
            result = await db_session.execute(stmt)
            conversation = result.scalar_one_or_none()
            if conversation:
                conversation_id = str(conversation.id)
            else:
                # Create logic (simplified from original)
                # We assume generic 'default' context exists or we create raw
                ctx_stmt = select(Context).where(Context.name == "default")
                ctx_res = await db_session.execute(ctx_stmt)
                context = ctx_res.scalar_one_or_none()

                new_conv = Conversation(
                    id=uuid.uuid4(),
                    platform=platform,
                    platform_id=platform_id,
                    context_id=context.id if context else None,
                    current_cwd=context.default_cwd if context else None,
                    conversation_metadata={},
                )
                db_session.add(new_conv)
                await db_session.flush()
                conversation_id = str(new_conv.id)
        return conversation_id

    async def _stream_agent_execution(
        self,
        prompt: str,
        conversation_id: str,
        db_session: AsyncSession | None,
        agent_service: Any,
        metadata: dict[str, Any],
    ) -> AsyncGenerator[AgentChunk, None]:
        """
        Execute agent service and yield chunks.
        Since AgentService is blocking, we simulate specific events or yield the final result.
        TODO: Refactor AgentService to be fully async generator.
        """
        if not agent_service or not db_session:
            yield {
                "type": "error",
                "content": "Agent Service not available",
                "tool_call": None,
                "metadata": None,
            }
            return

        request = AgentRequest(prompt=prompt, conversation_id=conversation_id, metadata=metadata)

        try:
            # We simulate "thinking" before calling the blocking service
            yield {
                "type": "thinking",
                "content": "Processing task...",
                "tool_call": None,
                "metadata": None,
            }

            # BLOCKING CALL - Limitation of current AgentService
            response = await agent_service.handle_request(request, session=db_session)

            # Replay steps as chunks
            if response.steps:
                for step in response.steps:
                    step_type = step.get("type")
                    if step_type == "plan":
                        yield {
                            "type": "thinking",
                            "content": f"Plan: {step.get('description')}",
                            "tool_call": None,
                            "metadata": step,
                        }
                    elif step_type == "tool":
                        yield {
                            "type": "tool_output",
                            "content": step.get("output"),
                            "tool_call": {"name": step.get("name")},
                            "metadata": step,
                        }

            # Yield final answer
            yield {
                "type": "content",
                "content": response.response,
                "tool_call": None,
                "metadata": {"done": True},
            }

        except Exception as e:
            LOGGER.exception("Agent execution failed")
            yield {
                "type": "error",
                "content": str(e),
                "tool_call": None,
                "metadata": None,
            }

    # Keep route_message for backward compatibility if needed, or remove if we are sure.
    # The instructions imply "Upgrade", so we can replace.
    # I'll leave a stub or remove it.
    # I will remove it to force usage of stream_message in the adapter.
