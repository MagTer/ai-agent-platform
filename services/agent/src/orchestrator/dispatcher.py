import logging
import shlex
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.core.litellm_client import LiteLLMClient
from core.core.routing import registry
from core.db.models import Context, Conversation, Message, Session
from core.routing import IntentClassifier
from shared.models import AgentMessage, AgentRequest, Plan, PlanStep, RoutingDecision
from shared.streaming import AgentChunk
from utils.template import substitute_variables

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


class Dispatcher:
    def __init__(self, skill_loader: SkillLoader, litellm: LiteLLMClient):
        self.skill_loader = skill_loader
        self.litellm = litellm
        self._intent_classifier = IntentClassifier(litellm)
        if not self.skill_loader.skills:
            self.skill_loader.load_skills()

    async def stream_message(
        self,
        session_id: str,
        message: str,
        platform: str = "web",
        platform_id: str | None = None,
        db_session: AsyncSession | None = None,
        agent_service: Any = None,
        history: list | None = None,
        metadata: dict[str, Any] | None = None,
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

                    # Merge metadata
                    agent_metadata = {"skill": skill.name, "tools": skill.tools}
                    if metadata:
                        agent_metadata.update(metadata)

                    async for chunk in self._stream_agent_execution(
                        prompt=rendered_prompt,
                        conversation_id=conversation_id,
                        db_session=db_session,
                        agent_service=agent_service,
                        metadata=agent_metadata,
                        history=history,
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
        path_match = registry.get_match(stripped_message)
        if path_match:
            path, match = path_match
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

            # Merge metadata
            agent_metadata = {"plan": plan.model_dump()}
            if metadata:
                agent_metadata.update(metadata)

            # Execute with injected plan
            async for chunk in self._stream_agent_execution(
                prompt=message,
                conversation_id=conversation_id,
                db_session=db_session,
                agent_service=agent_service,
                metadata=agent_metadata,
                history=history,
            ):
                yield chunk
            return

        # 3. Intent Classification (Structured Output)
        intent = await self._intent_classifier.classify(stripped_message)
        LOGGER.info(
            "Intent classified: route=%s confidence=%.2f",
            intent.route,
            intent.confidence,
        )

        # Yield classification event for observability
        yield {
            "type": "thinking",
            "content": f"Intent: {intent.route} (confidence: {intent.confidence:.0%})",
            "tool_call": None,
            "metadata": {
                "intent_route": intent.route,
                "intent_confidence": intent.confidence,
                "intent_reasoning": intent.reasoning,
            },
        }

        if intent.route == "chat":
            # Direct Streaming Chat
            # Use injected history from OpenWebUI if available, otherwise fall back to DB
            chat_history = history or []
            if not chat_history and agent_service and db_session:
                chat_history = await agent_service.get_history(conversation_id, db_session)

            # Stream response
            full_content = ""
            # Inject Date Context for CHAT
            from datetime import datetime

            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            year = datetime.now().year
            system_msg = AgentMessage(
                role="system", content=f"Current Date: {now}. You are live in {year}."
            )

            chat_messages = [system_msg] + chat_history
            chat_messages.append(AgentMessage(role="user", content=stripped_message))

            async for chunk in self.litellm.stream_chat(messages=chat_messages):
                yield chunk
                if chunk["type"] == "content" and chunk["content"]:
                    full_content += chunk["content"]

            # Persist chat history
            if db_session:
                try:
                    # 1. Get or Create Active Session
                    try:
                        conv_uuid = uuid.UUID(conversation_id)
                    except ValueError:
                        conv_uuid = None

                    if conv_uuid:
                        stmt = select(Session).where(
                            Session.conversation_id == conv_uuid,
                            Session.active.is_(True),
                        )
                        result = await db_session.execute(stmt)
                        session_obj = result.scalar_one_or_none()

                        if not session_obj:
                            session_obj = Session(
                                id=uuid.uuid4(), conversation_id=conv_uuid, active=True
                            )
                            db_session.add(session_obj)
                            await db_session.flush()

                        # 2. Save Messages
                        db_session.add(
                            Message(session_id=session_obj.id, role="user", content=message)
                        )
                        db_session.add(
                            Message(
                                session_id=session_obj.id,
                                role="assistant",
                                content=full_content,
                            )
                        )
                        await db_session.commit()
                except Exception as e:
                    LOGGER.error(f"Failed to persist CHAT history: {e}")

        else:
            # AGENTIC / TASK
            # Merge metadata
            agent_metadata = {"routing_decision": RoutingDecision.AGENTIC}
            if metadata:
                agent_metadata.update(metadata)

            async for chunk in self._stream_agent_execution(
                prompt=message,
                conversation_id=conversation_id,
                db_session=db_session,
                agent_service=agent_service,
                metadata=agent_metadata,
                history=history,
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
        history: list | None = None,
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

        request = AgentRequest(
            prompt=prompt,
            conversation_id=conversation_id,
            metadata=metadata,
            messages=history,
        )

        try:
            # Stream events from AgentService
            async for chunk in agent_service.execute_stream(request, session=db_session):

                # Normalize chunk to AgentChunk
                # AgentService yields dicts that look like AgentChunks mostly
                # We need to ensure types match what OpenWebUI adapter expects

                c_type = chunk.get("type", "content")

                if c_type == "plan":
                    yield {
                        "type": "thinking",
                        "content": f"Plan: {chunk.get('description')}",
                        "tool_call": None,
                        "metadata": chunk,
                    }
                elif c_type == "step_start":
                    yield {
                        "type": "step_start",
                        "content": chunk.get("content"),
                        "tool_call": None,
                        "metadata": chunk.get("metadata"),
                    }
                elif c_type == "tool_start":
                    yield {
                        "type": "tool_start",
                        "content": None,
                        "tool_call": chunk.get("tool_call"),
                        "metadata": chunk.get("metadata"),
                    }
                elif c_type == "tool_output":
                    yield {
                        "type": "tool_output",
                        "content": chunk.get("content"),
                        "tool_call": chunk.get("tool_call"),
                        "metadata": chunk.get("metadata"),
                    }
                elif c_type == "skill_activity":
                    yield {
                        "type": "skill_activity",
                        "content": chunk.get("content"),
                        "tool_call": None,
                        "metadata": chunk.get("metadata"),
                    }
                elif c_type == "content":
                    yield {
                        "type": "content",
                        "content": chunk.get("content"),
                        "tool_call": None,
                        "metadata": chunk.get("metadata"),
                    }
                elif c_type == "thinking":
                    yield {
                        "type": "thinking",
                        "content": chunk.get("content"),
                        "tool_call": None,
                        "metadata": chunk.get("metadata"),
                    }
                elif c_type == "history_snapshot":
                    yield {
                        "type": "history_snapshot",
                        "content": None,
                        "tool_call": None,
                        "metadata": chunk,
                    }
                else:
                    # Fallback
                    yield {
                        "type": "thinking",
                        "content": f"Event: {c_type}",
                        "tool_call": None,
                        "metadata": chunk,
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
