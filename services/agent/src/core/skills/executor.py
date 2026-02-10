"""Skill executor with scoped tool access.

This module provides the SkillExecutor class that runs skills with strict
tool scoping - skills can ONLY access tools explicitly listed in their
frontmatter. This provides security isolation and clear capability boundaries.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from shared.models import (
    AgentMessage,
    AgentRequest,
    AwaitingInputCategory,
    PlanStep,
    StepResult,
)

from core.observability.tracing import set_span_attributes, start_span
from core.skills.registry import SkillRegistry
from core.tools.activity_hints import build_activity_message

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from core.core.litellm_client import LiteLLMClient
    from core.tools import ToolRegistry
    from core.tools.base import Tool

from sqlalchemy import select

LOGGER = logging.getLogger(__name__)


class SkillExecutor:
    """Execute skills with scoped tool access.

    This executor runs skills as the primary execution unit, enforcing
    that skills can ONLY access tools explicitly defined in their frontmatter.

    Features:
    - Strict tool scoping (security isolation)
    - Streaming output support
    - Rate limiting and deduplication
    - Retry feedback support for self-correction
    """

    def __init__(
        self,
        skill_registry: SkillRegistry,
        tool_registry: ToolRegistry,
        litellm: LiteLLMClient,
    ) -> None:
        """Initialize the skill executor.

        Args:
            skill_registry: Registry of validated skills.
            tool_registry: Registry of available tools.
            litellm: LiteLLM client for LLM calls.
        """
        self._skill_registry = skill_registry
        self._tool_registry = tool_registry
        self._litellm = litellm
        # Cache for validated context ownership ((context_id, user_id) -> bool)
        self._validated_contexts: dict[tuple[UUID, UUID], bool] = {}

    async def _validate_context_ownership(
        self,
        claimed_context_id: UUID,
        authenticated_user_id: UUID,
        session: AsyncSession,
    ) -> bool:
        """Verify user has access to the claimed context.

        SECURITY: This prevents horizontal privilege escalation by ensuring
        users can only access contexts they own or have been granted access to.

        Args:
            claimed_context_id: The context_id from request metadata.
            authenticated_user_id: The authenticated user's ID.
            session: Database session for lookup.

        Returns:
            True if user has access, False otherwise.
        """
        # Check cache first (cache key is tuple of both IDs for security)
        cache_key = (claimed_context_id, authenticated_user_id)
        if cache_key in self._validated_contexts:
            return self._validated_contexts[cache_key]

        from core.db.models import UserContext

        stmt = select(UserContext).where(
            UserContext.user_id == authenticated_user_id,
            UserContext.context_id == claimed_context_id,
        )
        result = await session.execute(stmt)
        user_context = result.scalar_one_or_none()

        if user_context:
            # Cache the valid ownership
            self._validated_contexts[cache_key] = True
            return True

        # Cache the failed validation to avoid repeated DB queries
        self._validated_contexts[cache_key] = False
        LOGGER.warning(
            "Context ownership validation FAILED: user=%s claimed context=%s",
            authenticated_user_id,
            claimed_context_id,
        )
        return False

    async def execute_stream(
        self,
        step: PlanStep,
        request: AgentRequest,
        *,
        retry_feedback: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute a skill step and yield streaming events.

        Args:
            step: The plan step to execute (must have executor="skill").
            request: The original agent request (for context injection).
            retry_feedback: Optional feedback from previous failed attempt.

        Yields:
            Streaming events: thinking, content, skill_activity, result
        """
        skill_name = step.tool
        if not skill_name:
            yield {
                "type": "result",
                "result": StepResult(
                    step=step,
                    status="error",
                    result={"error": "No skill specified in step"},
                    messages=[],
                ),
            }
            return

        # Look up skill
        skill = self._skill_registry.get(skill_name)
        if not skill:
            yield {
                "type": "result",
                "result": StepResult(
                    step=step,
                    status="error",
                    result={"error": f"Skill '{skill_name}' not found"},
                    messages=[],
                ),
            }
            return

        # Build scoped tool set - ONLY tools defined in skill.tools
        scoped_tools: list[Tool] = []
        tool_schemas: list[dict[str, Any]] = []
        tool_lookup: dict[str, Tool] = {}

        for tool_name in skill.tools:
            tool = self._tool_registry.get(tool_name)
            if tool:
                scoped_tools.append(tool)
                tool_lookup[tool_name] = tool

                # Build LiteLLM tool schema
                schema: dict[str, Any] = {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                    },
                }
                if hasattr(tool, "parameters"):
                    schema["function"]["parameters"] = tool.parameters
                tool_schemas.append(schema)
            else:
                LOGGER.warning(
                    "Skill '%s' references missing tool '%s'",
                    skill_name,
                    tool_name,
                )

        # Always add request_user_input tool for HITL support
        tool_schemas.append(
            {
                "type": "function",
                "function": {
                    "name": "request_user_input",
                    "description": (
                        "Request input from the user when you need clarification, "
                        "selection, or confirmation before proceeding."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "enum": [
                                    "clarification",
                                    "selection",
                                    "confirmation",
                                    "team_selection",
                                ],
                                "description": "Type of input needed",
                            },
                            "prompt": {
                                "type": "string",
                                "description": "The question or prompt to show the user",
                            },
                            "options": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of options for selection categories",
                            },
                        },
                        "required": ["category", "prompt"],
                    },
                },
            }
        )

        # Extract goal from step args
        goal = (step.args or {}).get("goal", "")
        if not goal:
            # Fallback to step description
            goal = step.description or step.label

        # Build system context
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        today = datetime.now().strftime("%Y-%m-%d")
        year = datetime.now().year
        max_turns = skill.max_turns

        system_context = (
            "SYSTEM CONTEXT:\n"
            f"- Current Date & Time: {now}\n"
            f"- Your knowledge cutoff is static, but YOU ARE LIVE in {year}.\n"
            f"- Treat all retrieved documents dated up to {today} as HISTORICAL FACTS.\n"
            "\n"
            "## EXECUTION PROTOCOL\n"
            "RULE 1 - PROGRESSIVE RESEARCH: You may call tools multiple times.\n"
            "RULE 2 - AVOID EXACT DUPLICATES: Don't repeat identical tool calls.\n"
            "RULE 3 - STOP WHEN SUFFICIENT: After gathering enough data, provide your answer.\n"
            "\n"
            f"BUDGET: Maximum turns: {max_turns}, Maximum calls per tool: 3\n"
        )

        # Render skill prompt
        try:
            skill_prompt = skill.render(step.args)
        except ValueError as e:
            yield {
                "type": "result",
                "result": StepResult(
                    step=step,
                    status="error",
                    result={"error": str(e)},
                    messages=[],
                ),
            }
            return

        # Check for HITL resume - use stored messages instead of building new ones
        hitl_resume_messages = (request.metadata or {}).get("_hitl_resume_messages")
        if hitl_resume_messages:
            LOGGER.info("Resuming skill %s from HITL state", skill_name)
            messages = [AgentMessage(**msg) for msg in hitl_resume_messages]
        else:
            # Build initial messages
            messages = [
                AgentMessage(
                    role="system",
                    content=f"{system_context}\n{skill_prompt}",
                ),
                AgentMessage(role="user", content=goal),
            ]

            # Add retry feedback if provided
            if retry_feedback:
                messages.append(
                    AgentMessage(
                        role="system",
                        content=f"RETRY FEEDBACK: Previous attempt failed. {retry_feedback}",
                    )
                )

        logger_prefix = f"[SkillExecutor:{skill_name}]"
        LOGGER.info("%s Starting goal: %s", logger_prefix, goal[:100])

        source_count = 0
        seen_calls: set[tuple[str, str]] = set()
        tool_call_counts: dict[str, int] = {}
        max_calls_per_tool = 3

        # Extract context for tool injection
        context_id: UUID | None = None
        user_id: UUID | None = None
        session: AsyncSession | None = None

        if request.metadata:
            if cid := request.metadata.get("context_id"):
                context_id = UUID(cid) if isinstance(cid, str) else cid
            if uid := request.metadata.get("user_id"):
                user_id = UUID(uid) if isinstance(uid, str) else uid
            session = request.metadata.get("_db_session")

        # SECURITY: Validate context ownership before tool execution
        if context_id and user_id and session:
            if not await self._validate_context_ownership(context_id, user_id, session):
                yield {
                    "type": "result",
                    "result": StepResult(
                        step=step,
                        status="error",
                        result={"error": "Access denied: context ownership validation failed"},
                        messages=[],
                    ),
                }
                return

        with start_span(f"skill.execution.{skill_name}", attributes={"goal": goal[:200]}):
            yield {
                "type": "thinking",
                "content": f"Goal: {goal[:80]}...",
                "metadata": {"source": "skill_internal"},
            }
            await asyncio.sleep(0)

            for turn in range(max_turns):
                LOGGER.debug("%s Turn %d", logger_prefix, turn + 1)
                blocked_this_turn = False

                with start_span(f"skill.turn.{turn + 1}"):
                    set_span_attributes({"skill.turn": turn + 1, "skill.name": skill_name})

                    # Stream LLM response
                    full_content: list[str] = []
                    tool_calls_buffer: dict[int, Any] = {}

                    try:
                        async for chunk in self._litellm.stream_chat(
                            messages,
                            model=skill.model,
                            tools=tool_schemas if tool_schemas else None,
                        ):
                            if chunk["type"] == "content" and chunk["content"]:
                                content = chunk["content"]
                                full_content.append(content)

                                # Check for HITL marker and emit structured event
                                if "[AWAITING_USER_INPUT:" in content:
                                    match = re.search(r"\[AWAITING_USER_INPUT:(\w+)\]", content)
                                    if match:
                                        category_str = match.group(1).lower()
                                        # Clean content - remove the marker
                                        clean_prompt = re.sub(
                                            r"\[AWAITING_USER_INPUT:\w+\]", "", content
                                        ).strip()

                                        # Map legacy/alternative category names
                                        category_aliases = {
                                            "type_selection": "selection",
                                        }

                                        # Apply alias mapping
                                        if category_str in category_aliases:
                                            mapped = category_aliases[category_str]
                                            LOGGER.warning(
                                                "HITL category '%s' mapped to '%s' "
                                                "- consider updating skill",
                                                category_str,
                                                mapped,
                                            )
                                            category_str = mapped

                                        # Map category string to enum
                                        try:
                                            category = AwaitingInputCategory(category_str)
                                        except ValueError:
                                            LOGGER.warning(
                                                "Invalid HITL category '%s' in skill '%s', "
                                                "falling back to CLARIFICATION",
                                                category_str,
                                                skill_name,
                                            )
                                            category = AwaitingInputCategory.CLARIFICATION

                                        # Emit structured awaiting_input event
                                        yield {
                                            "type": "awaiting_input",
                                            "content": None,
                                            "tool_call": None,
                                            "metadata": {
                                                "category": category.value,
                                                "prompt": clean_prompt,
                                                "skill_name": skill_name,
                                                "context": {},
                                                "required": True,
                                            },
                                        }
                                        # Continue to next chunk (don't yield marker in content)
                                        continue

                                # Mark skill content for filtering in adapter
                                yield {
                                    "type": "content",
                                    "content": content,
                                    "metadata": {"source": "skill_stream"},
                                }

                            elif chunk["type"] == "thinking" and chunk["content"]:
                                # Forward reasoning/thinking from LLM (gpt-oss, etc.)
                                # Preserve source metadata for filtering in adapter
                                meta = chunk.get("metadata") or {"source": "reasoning_model"}
                                yield {
                                    "type": "thinking",
                                    "content": chunk["content"],
                                    "metadata": meta,
                                }

                            elif chunk["type"] == "tool_start":
                                tc = chunk.get("tool_call")
                                if tc:
                                    idx = tc.get("index", 0)
                                    if idx not in tool_calls_buffer:
                                        tool_calls_buffer[idx] = tc
                                    else:
                                        self._merge_tool_calls(tool_calls_buffer, dict(chunk))

                            elif chunk["type"] == "error":
                                yield {
                                    "type": "result",
                                    "result": StepResult(
                                        step=step,
                                        status="error",
                                        result={"error": f"LLM error: {chunk['content']}"},
                                        messages=[],
                                    ),
                                }
                                return

                    except Exception as e:
                        LOGGER.error("%s Stream error: %s", logger_prefix, e, exc_info=True)
                        yield {
                            "type": "result",
                            "result": StepResult(
                                step=step,
                                status="error",
                                result={"error": f"Stream error: {e}"},
                                messages=[],
                            ),
                        }
                        return

                    content = "".join(full_content)
                    tool_calls = list(tool_calls_buffer.values())

                    # Add assistant message to history
                    # Use None for empty content when tool_calls are present
                    # (OpenAI spec requires content=null, not "")
                    assistant_msg = AgentMessage(
                        role="assistant",
                        content=content or None,
                        tool_calls=tool_calls or None,
                    )
                    messages.append(assistant_msg)

                    # No tool calls - return final content
                    if not tool_calls:
                        if content:
                            LOGGER.info(
                                "%s Final result with source_count=%d",
                                logger_prefix,
                                source_count,
                            )
                            yield {
                                "type": "result",
                                "result": StepResult(
                                    step=step,
                                    status="ok",
                                    result={
                                        "output": content,
                                        "source_count": source_count,
                                    },
                                    messages=[
                                        AgentMessage(
                                            role="system",
                                            content=f"Skill {skill_name} output:\n{content}",
                                        )
                                    ],
                                ),
                            }
                            return

                        yield {
                            "type": "result",
                            "result": StepResult(
                                step=step,
                                status="ok",
                                result={
                                    "output": "Skill produced empty response.",
                                    "source_count": source_count,
                                },
                                messages=[],
                            ),
                        }
                        return

                    # Process tool calls
                    source_count += len(tool_calls)
                    LOGGER.info(
                        "%s Turn %d: %d tool calls (total: %d)",
                        logger_prefix,
                        turn + 1,
                        len(tool_calls),
                        source_count,
                    )

                    for tc in tool_calls:
                        func = tc.get("function", {})
                        fname = func.get("name", "")
                        call_id = tc.get("id", "")

                        try:
                            fargs = json.loads(func.get("arguments", "{}"))
                        except json.JSONDecodeError:
                            LOGGER.warning(
                                "%s Failed to parse tool arguments for %s, using empty dict",
                                logger_prefix,
                                fname,
                                exc_info=True,
                            )
                            fargs = {}

                        # Build activity message
                        tool_obj = tool_lookup.get(fname)
                        activity_msg = build_activity_message(tool_obj, fname, fargs)

                        # Check for duplicates
                        call_key = (fname, json.dumps(fargs, sort_keys=True))
                        current_count = tool_call_counts.get(fname, 0)

                        output_str = ""
                        if call_key in seen_calls:
                            LOGGER.warning("%s Blocking duplicate call to %s", logger_prefix, fname)
                            output_str = (
                                f"BLOCKED: Duplicate call to '{fname}'. "
                                "Use the data from your previous call."
                            )
                            yield {
                                "type": "thinking",
                                "content": f"Skipping duplicate {fname}",
                                "metadata": {"source": "skill_internal"},
                            }
                            blocked_this_turn = True

                        elif current_count >= max_calls_per_tool:
                            LOGGER.warning(
                                "%s Rate limiting %s (called %d times)",
                                logger_prefix,
                                fname,
                                current_count,
                            )
                            output_str = (
                                f"BLOCKED: Max calls ({max_calls_per_tool}) to '{fname}' reached."
                            )
                            blocked_this_turn = True

                        elif fname == "request_user_input":
                            # HITL: User input requested via structured tool call
                            LOGGER.info("%s HITL: User input requested", logger_prefix)

                            # Map category string to enum for validation
                            category_str = fargs.get("category", "clarification")
                            try:
                                category = AwaitingInputCategory(category_str)
                            except ValueError:
                                LOGGER.warning(
                                    "%s Invalid HITL category '%s', using CLARIFICATION",
                                    logger_prefix,
                                    category_str,
                                )
                                category = AwaitingInputCategory.CLARIFICATION

                            # Yield awaiting_input event with skill state for resume
                            yield {
                                "type": "awaiting_input",
                                "content": fargs.get("prompt", ""),
                                "tool_call": None,
                                "metadata": {
                                    "category": category.value,
                                    "prompt": fargs.get("prompt", ""),
                                    "options": fargs.get("options"),
                                    "skill_name": skill_name,
                                    "skill_messages": [m.model_dump() for m in messages],
                                    "step": step.model_dump(),
                                    "tool_call_id": call_id,
                                },
                            }
                            # Stop execution - service.py will store state and resume later
                            return

                        elif fname not in tool_lookup:
                            # Tool not in scoped set - SECURITY: reject
                            LOGGER.warning(
                                "%s Rejecting out-of-scope tool '%s'",
                                logger_prefix,
                                fname,
                            )
                            output_str = f"ERROR: Tool '{fname}' is not available to this skill."

                        else:
                            # Execute tool (tool_obj is guaranteed to exist here)
                            assert tool_obj is not None
                            seen_calls.add(call_key)
                            tool_call_counts[fname] = current_count + 1

                            yield {
                                "type": "thinking",
                                "content": activity_msg,
                                "metadata": {"source": "skill_internal"},
                            }
                            yield {
                                "type": "skill_activity",
                                "content": activity_msg,
                                "metadata": {
                                    "tool": fname,
                                    "skill": skill_name,
                                    "search_query": fargs.get("query"),
                                    "fetch_url": fargs.get("url"),
                                },
                            }
                            await asyncio.sleep(0)

                            # Inject context into tool args
                            tool_args = fargs.copy()
                            if context_id and fname in ("homey",):
                                tool_args["context_id"] = context_id
                            if user_id and session and fname in ("azure_devops",):
                                tool_args["user_id"] = user_id
                                tool_args["session"] = session

                            try:
                                with start_span(f"skill.tool.{fname}"):
                                    tool_start = time.perf_counter()
                                    output_str = str(await tool_obj.run(**tool_args))
                                    tool_duration_ms = (time.perf_counter() - tool_start) * 1000
                                    set_span_attributes(
                                        {
                                            "tool.output_preview": output_str[:500],
                                            "tool.status": "success",
                                            "tool.duration_ms": round(tool_duration_ms, 1),
                                        }
                                    )
                            except Exception as e:
                                output_str = f"Error: {e}"
                                LOGGER.error(
                                    "%s Tool %s failed: %s",
                                    logger_prefix,
                                    fname,
                                    e,
                                )

                        # Add tool result to messages
                        messages.append(
                            AgentMessage(
                                role="tool",
                                tool_call_id=call_id,
                                name=fname,
                                content=output_str,
                            )
                        )

                    if blocked_this_turn:
                        LOGGER.info("%s Blocked calls, terminating", logger_prefix)
                        break

            # Reached max turns
            LOGGER.warning(
                "%s Reached max_turns (%d) with source_count=%d",
                logger_prefix,
                max_turns,
                source_count,
            )

            # Extract tool outputs for summary
            tool_outputs: list[str] = []
            for msg in messages:
                if msg.role == "tool" and msg.content:
                    if (
                        not msg.content.startswith("Error:")
                        and not msg.content.startswith("BLOCKED:")
                        and len(msg.content) > 50
                    ):
                        tool_outputs.append(msg.content)

            if tool_outputs:
                combined = "\n\n---\n\n".join(tool_outputs[-3:])
                output_msg = (
                    f"Skill '{skill_name}' reached max turns ({max_turns}). "
                    f"Collected data from {source_count} sources:\n\n{combined}"
                )
            else:
                output_msg = (
                    f"Skill '{skill_name}' reached max turns ({max_turns}). "
                    f"Used {source_count} sources but no substantial results."
                )

            yield {
                "type": "result",
                "result": StepResult(
                    step=step,
                    status="ok",
                    result={"output": output_msg, "source_count": source_count},
                    messages=[
                        AgentMessage(
                            role="system",
                            content=f"Skill {skill_name} output:\n{output_msg}",
                        )
                    ],
                ),
            }

    def _merge_tool_calls(
        self,
        buffer: dict[int, Any],
        chunk: dict[str, Any],
    ) -> None:
        """Merge streaming tool call deltas into the buffer."""
        tc = chunk.get("tool_call", {})
        idx = tc.get("index", 0)

        if idx not in buffer:
            return

        prev = buffer[idx]
        func = tc.get("function", {})

        if "name" in func and func["name"]:
            prev_func = prev.setdefault("function", {})
            prev_func["name"] = (prev_func.get("name") or "") + func["name"]

        if "arguments" in func and func["arguments"]:
            prev_func = prev.setdefault("function", {})
            prev_func["arguments"] = (prev_func.get("arguments") or "") + func["arguments"]


__all__ = ["SkillExecutor"]
