"""Human-in-the-loop (HITL) coordination module - handles skill pause/resume workflow."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from pydantic import ValidationError
from shared.models import AgentMessage, AgentRequest, DraftOutput, PlanStep, UserIntent
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import Conversation, Message, Session
from core.observability.tracing import current_trace_ids
from core.runtime.litellm_client import LiteLLMClient
from core.skills import SkillExecutor, SkillRegistryProtocol
from core.tools import ToolRegistry

LOGGER = logging.getLogger(__name__)


class HITLCoordinator:
    """Handles human-in-the-loop workflow for skill execution."""

    # Fast-path keywords for instant intent detection (no LLM call needed)
    _APPROVE_KEYWORDS = {
        "yes",
        "approve",
        "ok",
        "okay",
        "confirm",
        "accept",
        "go ahead",
        "do it",
        "looks good",
        "sure",
    }
    _REJECT_KEYWORDS = {"no", "cancel", "reject", "abort", "stop", "don't", "nevermind"}
    _REQUEST_CHANGES_KEYWORDS = {
        "request changes",
        "revise",
        "edit",
        "change",
        "update",
        "modify",
        "not quite",
        "needs work",
    }
    # Confidence threshold for LLM-based classification
    _CONFIDENCE_THRESHOLD = 0.7
    # Timeout for intent classification (seconds)
    _INTENT_CLASSIFICATION_TIMEOUT = 5.0

    def __init__(
        self,
        skill_registry: SkillRegistryProtocol | None,
        tool_registry: ToolRegistry,
        litellm: LiteLLMClient,
    ):
        """Initialize the HITL coordinator.

        Args:
            skill_registry: Registry of available skills
            tool_registry: Registry of available tools
            litellm: LiteLLM client for LLM calls
        """
        self._skill_registry = skill_registry
        self._tool_registry = tool_registry
        self._litellm = litellm

    def _detect_work_items(self, prompt_history: list[AgentMessage]) -> bool:
        """Detect work item drafts in conversation history.

        Heuristic: Look for common work item markers in the last few messages.

        Args:
            prompt_history: The conversation history

        Returns:
            True if work item detected, False otherwise
        """
        for msg in reversed(prompt_history[-5:]):  # Check last 5 messages
            content = str(msg.content or "")
            if (
                "User Story" in content
                or "Feature" in content
                or "TYPE:" in content
                or "TITLE:" in content
            ) and ("Acceptance Criteria" in content or "Success Metrics" in content):
                return True
        return False

    async def _classify_user_intent(self, user_response: str) -> tuple[UserIntent, float]:
        """Classify user response intent using semantic detection.

        First checks for exact keyword matches (fast path).
        If no keyword matches, uses LLM for semantic classification.

        Args:
            user_response: The user's natural language response

        Returns:
            Tuple of (intent, confidence) where confidence is:
            - 1.0 for fast-path keyword matches
            - LLM-reported confidence for semantic classification
            - 0.0 for errors/timeouts
        """
        response_lower = user_response.lower().strip()

        # Fast path: exact keyword matching
        if any(kw in response_lower for kw in self._APPROVE_KEYWORDS):
            LOGGER.debug("Fast-path intent detection: APPROVE (keyword match)")
            return UserIntent.APPROVE, 1.0
        if any(kw in response_lower for kw in self._REJECT_KEYWORDS):
            LOGGER.debug("Fast-path intent detection: REJECT (keyword match)")
            return UserIntent.REJECT, 1.0
        if any(kw in response_lower for kw in self._REQUEST_CHANGES_KEYWORDS):
            LOGGER.debug("Fast-path intent detection: REQUEST_CHANGES (keyword match)")
            return UserIntent.REQUEST_CHANGES, 1.0

        # Semantic classification via LLM
        try:
            LOGGER.debug("No keyword match, using LLM for intent classification")
            messages = [
                AgentMessage(
                    role="system",
                    content=(
                        "You are an intent classifier. Analyze the user's response to a HITL "
                        "(Human-in-the-Loop) approval request and classify it into exactly one "
                        "category.\n\n"
                        "Categories:\n"
                        "- APPROVE: User accepts or confirms the action\n"
                        "- REJECT: User declines or cancels the action\n"
                        "- REQUEST_CHANGES: User wants modifications before proceeding\n"
                        "- UNCLEAR: Cannot determine intent from the response\n\n"
                        "Respond with exactly one word: APPROVE, REJECT, REQUEST_CHANGES, "
                        "or UNCLEAR."
                    ),
                ),
                AgentMessage(
                    role="user",
                    content=f"Classify this response: '{user_response}'",
                ),
            ]

            start_time = asyncio.get_event_loop().time()
            result = await asyncio.wait_for(
                self._litellm.generate(messages, model="agentchat"),
                timeout=self._INTENT_CLASSIFICATION_TIMEOUT,
            )
            elapsed = asyncio.get_event_loop().time() - start_time

            raw_intent = result.strip().upper()

            # Parse the LLM response
            if "APPROVE" in raw_intent:
                intent = UserIntent.APPROVE
                confidence = 0.95
            elif "REJECT" in raw_intent:
                intent = UserIntent.REJECT
                confidence = 0.95
            elif "REQUEST_CHANGES" in raw_intent or "CHANGES" in raw_intent:
                intent = UserIntent.REQUEST_CHANGES
                confidence = 0.90
            else:
                intent = UserIntent.UNCLEAR
                confidence = 0.50

            LOGGER.debug(
                "LLM intent classification: %s (confidence=%.2f, raw='%s', time=%.2fs)",
                intent.value,
                confidence,
                raw_intent,
                elapsed,
            )
            return intent, confidence

        except TimeoutError:
            LOGGER.warning(
                "Intent classification timed out after %.1fs, falling back to keyword matching",
                self._INTENT_CLASSIFICATION_TIMEOUT,
            )
            return UserIntent.UNCLEAR, 0.0
        except Exception as e:
            LOGGER.warning("Intent classification failed: %s, falling back to keyword matching", e)
            return UserIntent.UNCLEAR, 0.0

    async def _resume_hitl(
        self,
        request: AgentRequest,
        session: AsyncSession,
        db_session: Session,
        db_conversation: Conversation,
        pending_hitl: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Resume a skill execution after user provides HITL input.

        Args:
            request: The new request containing user's input
            session: Database session
            db_session: The active Session
            db_conversation: The conversation with pending HITL
            pending_hitl: The stored HITL state

        Yields:
            Event dictionaries for the resumed execution
        """
        skill_name = pending_hitl.get("skill_name", "unknown")
        skill_messages = pending_hitl.get("skill_messages", [])
        step_data = pending_hitl.get("step", {})
        tool_call_id = pending_hitl.get("tool_call_id", "")
        category = pending_hitl.get("category", "")
        trace_id = current_trace_ids().get("trace_id", str(uuid.uuid4()))

        LOGGER.info(
            "Resuming HITL for skill %s (category=%s) with user input: %s",
            skill_name,
            category,
            request.prompt[:100],
        )

        # Record user message
        session.add(
            Message(
                session_id=db_session.id,
                role="user",
                content=request.prompt,
                trace_id=trace_id,
            )
        )

        # Clear pending HITL now that we're resuming
        # Update conversation_metadata directly (extracted from persistence layer)
        current_meta = dict(db_conversation.conversation_metadata or {})
        if "pending_hitl" in current_meta:
            del current_meta["pending_hitl"]
            db_conversation.conversation_metadata = current_meta
            await session.flush()
            LOGGER.info("Cleared pending HITL for conversation %s", db_conversation.id)

        # Check for handoff: requirements_drafter confirmation -> requirements_writer
        intent, confidence = await self._classify_user_intent(request.prompt)

        LOGGER.debug(
            "HITL intent classification: %s (confidence=%.2f) for prompt: %s",
            intent.value,
            confidence,
            request.prompt[:100],
        )

        # Handle UNCLEAR intent with low confidence - ask for clarification
        if intent == UserIntent.UNCLEAR or confidence < self._CONFIDENCE_THRESHOLD:
            LOGGER.info("HITL: Intent unclear, requesting clarification")
            clarification_msg = (
                "I'm not sure what you'd like to do. Please respond with:\n"
                "- **approve** (or yes/ok) to proceed with creating the work item\n"
                "- **reject** (or no/cancel) to abort\n"
                "- **request changes** to revise the draft"
            )
            yield {"type": "content", "content": clarification_msg}
            session.add(
                Message(
                    session_id=db_session.id,
                    role="assistant",
                    content=clarification_msg,
                    trace_id=trace_id,
                )
            )
            await session.commit()
            return

        is_approval = intent == UserIntent.APPROVE
        is_request_changes = intent == UserIntent.REQUEST_CHANGES
        is_cancel = intent == UserIntent.REJECT

        if skill_name == "requirements_drafter" and category == "confirmation" and is_cancel:
            LOGGER.info("HITL: requirements_drafter cancelled by user")
            yield {"type": "content", "content": "Work item creation cancelled."}
            session.add(
                Message(
                    session_id=db_session.id,
                    role="assistant",
                    content="Work item creation cancelled.",
                    trace_id=trace_id,
                )
            )
            await session.commit()
            return

        if skill_name == "requirements_drafter" and category == "confirmation" and is_approval:
            LOGGER.info("HITL handoff: requirements_drafter -> requirements_writer")
            yield {
                "type": "thinking",
                "content": "Creating work item in Azure DevOps...",
                "metadata": {"role": "Executor", "hitl_handoff": True},
            }

            # Extract draft data from skill messages
            draft_data = self._extract_draft_from_messages(skill_messages)

            if not draft_data:
                yield {
                    "type": "error",
                    "content": "Could not extract draft data from conversation.",
                }
                return

            # Execute requirements_writer with draft data
            async for event in self._execute_requirements_writer(
                draft_data, request, session, db_session, trace_id
            ):
                yield event
            return

        if (
            skill_name == "requirements_drafter"
            and category == "confirmation"
            and is_request_changes
        ):  # noqa: E501
            # Resume the drafter with an explicit revision instruction as the tool result
            LOGGER.info("HITL: requirements_drafter revision requested")
            yield {
                "type": "thinking",
                "content": "Revising the draft...",
                "metadata": {"role": "Executor", "hitl_revision": True},
            }
            # Fall through to normal resume - the user's "Request Changes" text
            # is forwarded as the tool result so the drafter can act on it.

        # Normal HITL resume - continue the original skill
        yield {
            "type": "thinking",
            "content": f"Resuming {skill_name} with your input...",
            "metadata": {"role": "Executor", "hitl_resume": True},
        }

        # Reconstruct skill messages and add user response as tool result
        messages: list[AgentMessage] = [AgentMessage(**msg) for msg in skill_messages]

        # Add user's response as tool result for request_user_input
        messages.append(
            AgentMessage(
                role="tool",
                tool_call_id=tool_call_id,
                name="request_user_input",
                content=f"User response: {request.prompt}",
            )
        )

        # Reconstruct the step
        step = PlanStep(**step_data)

        # Get skill executor
        if not self._skill_registry:
            yield {"type": "error", "content": "Skill registry not available"}
            return

        skill_executor = SkillExecutor(
            skill_registry=self._skill_registry,
            tool_registry=self._tool_registry,
            litellm=self._litellm,
        )

        # Continue skill execution by calling LLM with updated messages
        # We need to pass the messages to the skill executor via request metadata
        resume_request = AgentRequest(
            prompt=request.prompt,
            conversation_id=request.conversation_id,
            metadata={
                **(request.metadata or {}),
                "_hitl_resume_messages": [m.model_dump() for m in messages],
            },
        )

        # Execute the skill continuation
        completion_text = ""
        async for event in skill_executor.execute_stream(
            step,
            request=resume_request,
        ):
            if event["type"] == "content":
                content = event.get("content", "")
                yield {"type": "content", "content": content, "metadata": event.get("metadata")}
                completion_text += content
            elif event["type"] == "awaiting_input":
                # Another HITL request - store and yield
                meta = event.get("metadata") or {}
                # Update conversation_metadata directly
                current_meta = dict(db_conversation.conversation_metadata or {})
                current_meta["pending_hitl"] = meta
                db_conversation.conversation_metadata = current_meta
                await session.flush()
                LOGGER.info(
                    "Stored pending HITL for conversation %s: %s",
                    db_conversation.id,
                    meta.get("skill_name"),
                )
                yield event
                return
            elif event["type"] == "thinking":
                yield event
            elif event["type"] == "skill_activity":
                yield event
            elif event["type"] == "result":
                # Skill completed
                step_result = event.get("result")
                if step_result and step_result.result:
                    output = step_result.result.get("output", "")
                    if output and not completion_text:
                        completion_text = output
                        yield {"type": "content", "content": output}

        # Record assistant response
        if completion_text:
            session.add(
                Message(
                    session_id=db_session.id,
                    role="assistant",
                    content=completion_text,
                    trace_id=trace_id,
                )
            )

        await session.commit()
        LOGGER.info("HITL resume completed for skill %s", skill_name)

    def _extract_draft_from_messages(
        self, skill_messages: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Extract draft data from requirements_drafter skill messages.

        First attempts structured JSON parsing using DraftOutput Pydantic model.
        Falls back to regex parsing for backward compatibility.

        Args:
            skill_messages: List of message dicts from the skill execution

        Returns:
            Dict with draft fields, or None if extraction fails
        """
        # First try structured JSON extraction
        for msg in reversed(skill_messages):
            content = msg.get("content", "")
            if not content:
                continue

            # Try to find JSON in code blocks
            json_str: str | None = None

            # Pattern 1: JSON in triple backticks
            json_block_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
            if json_block_match:
                json_str = json_block_match.group(1)
            else:
                # Pattern 2: Raw JSON starting with {
                json_match = re.search(r"(\{[\s\S]*\"draft\"[\s\S]*\})", content)
                if json_match:
                    json_str = json_match.group(1)

            if json_str:
                try:
                    draft_output = DraftOutput.model_validate_json(json_str)
                    draft_data: dict[str, Any] = {
                        "type": draft_output.draft.type,
                        "team_alias": draft_output.draft.team_alias,
                        "title": draft_output.draft.title,
                        "description": draft_output.draft.description,
                        "acceptance_criteria": draft_output.draft.acceptance_criteria,
                        "tags": draft_output.draft.tags,
                    }
                    LOGGER.info("Successfully extracted draft via structured JSON parsing")
                    return draft_data
                except (ValidationError, json.JSONDecodeError) as e:
                    LOGGER.warning(
                        "JSON parsing failed for DraftOutput, falling back to regex: %s", e
                    )
                    # Continue to regex fallback

        # Fallback: regex-based extraction for backward compatibility
        LOGGER.info("Attempting regex fallback for draft extraction")
        return self._extract_draft_from_messages_regex(skill_messages)

    def _extract_draft_from_messages_regex(
        self, skill_messages: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Extract draft data using regex patterns (backward compatibility fallback).

        Parses the conversation to find the draft structure including:
        - type (User Story, Feature, Bug)
        - team
        - title
        - description
        - acceptance_criteria
        - tags

        Args:
            skill_messages: List of message dicts from the skill execution

        Returns:
            Dict with draft fields, or None if extraction fails
        """
        # Look for assistant messages containing the draft
        for msg in reversed(skill_messages):
            content = msg.get("content", "")
            if not content:
                continue

            # Look for draft markers
            if "DRAFT READY" not in content and "Type:" not in content:
                continue

            draft: dict[str, Any] = {}

            # Extract Type
            type_match = re.search(r"Type:\s*(.+?)(?:\n|$)", content)
            if type_match:
                draft["type"] = type_match.group(1).strip()

            # Extract Team - strip display name suffix (e.g., "infra - Infrastructure" -> "infra")
            team_match = re.search(r"Team:\s*(.+?)(?:\n|$)", content)
            if team_match:
                team_raw = team_match.group(1).strip()
                draft["team_alias"] = team_raw.split(" - ")[0].strip()

            # Extract Title
            title_match = re.search(r"Title:\s*(.+?)(?:\n|$)", content)
            if title_match:
                draft["title"] = title_match.group(1).strip()

            # Extract Description (multi-line)
            desc_match = re.search(
                r"Description:\s*\n(.*?)(?=\n(?:Acceptance Criteria|Tags|={3,})|$)",
                content,
                re.DOTALL,
            )
            if desc_match:
                draft["description"] = desc_match.group(1).strip()

            # Extract Acceptance Criteria (multi-line)
            ac_match = re.search(
                r"Acceptance Criteria:?\s*(?:\(if applicable\))?\s*\n(.*?)(?=\n(?:Tags|={3,})|$)",
                content,
                re.DOTALL,
            )
            if ac_match:
                draft["acceptance_criteria"] = ac_match.group(1).strip()

            # Extract Tags
            tags_match = re.search(r"Tags:\s*(.+?)(?:\n|$)", content)
            if tags_match:
                tags_str = tags_match.group(1).strip()
                # Parse comma or semicolon separated tags
                draft["tags"] = [t.strip() for t in re.split(r"[,;]", tags_str) if t.strip()]

            # Validate minimum required fields
            if draft.get("title") and draft.get("team_alias") and draft.get("type"):
                LOGGER.info("Extracted draft via regex fallback: %s", draft)
                return draft
            missing = [f for f in ("title", "team_alias", "type") if not draft.get(f)]
            LOGGER.warning("Regex fallback draft missing required fields: %s", missing)

        LOGGER.warning("Could not extract draft from skill messages via regex fallback")
        return None

    async def _execute_requirements_writer(
        self,
        draft_data: dict[str, Any],
        request: AgentRequest,
        session: AsyncSession,
        db_session: Session,
        trace_id: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute requirements_writer to create the work item.

        Args:
            draft_data: Extracted draft data from drafter
            request: Original request
            session: Database session
            db_session: The active Session
            trace_id: Current trace ID

        Yields:
            Event dictionaries for the execution
        """
        if not self._skill_registry:
            yield {"type": "error", "content": "Skill registry not available"}
            return

        # Create a step for requirements_writer
        writer_step = PlanStep(
            id=str(uuid.uuid4()),
            label="Create work item in Azure DevOps",
            executor="skill",
            action="skill",
            tool="requirements_writer",
            args={
                "goal": (
                    f"Create a {draft_data.get('type', 'work item')} " "with the following details"
                ),
                **draft_data,
            },
        )

        skill_executor = SkillExecutor(
            skill_registry=self._skill_registry,
            tool_registry=self._tool_registry,
            litellm=self._litellm,
        )

        # Build the writer request with draft data in metadata
        writer_request = AgentRequest(
            prompt=f"Create work item: {draft_data.get('title', '')}",
            conversation_id=request.conversation_id,
            metadata={
                **(request.metadata or {}),
                "draft_data": draft_data,
            },
        )

        completion_text = ""
        async for event in skill_executor.execute_stream(
            writer_step,
            request=writer_request,
        ):
            if event["type"] == "content":
                content = event.get("content", "")
                yield {"type": "content", "content": content, "metadata": event.get("metadata")}
                completion_text += content
            elif event["type"] == "thinking":
                yield event
            elif event["type"] == "skill_activity":
                yield event
            elif event["type"] == "result":
                step_result = event.get("result")
                if step_result and step_result.result:
                    output = step_result.result.get("output", "")
                    if output and not completion_text:
                        completion_text = output
                        yield {"type": "content", "content": output}

        # Record assistant response
        if completion_text:
            session.add(
                Message(
                    session_id=db_session.id,
                    role="assistant",
                    content=completion_text,
                    trace_id=trace_id,
                )
            )

        await session.commit()
        LOGGER.info("Requirements writer completed")
