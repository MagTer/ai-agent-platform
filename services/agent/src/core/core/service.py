from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from shared.models import (
    AgentMessage,
    AgentRequest,
    AgentResponse,
    AwaitingInputCategory,
    AwaitingInputRequest,
    Plan,
    PlanStep,
    RoutingDecision,
    StepOutcome,
    StepResult,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.agents import (
    PlannerAgent,
    PlanSupervisorAgent,
    StepExecutorAgent,
    StepSupervisorAgent,
)
from core.command_loader import get_available_skill_names, get_registry_index
from core.context_manager import ContextManager
from core.core.config import Settings
from core.core.litellm_client import LiteLLMClient
from core.core.memory import MemoryRecord, MemoryStore
from core.db import Context, Conversation, Message, Session
from core.debug import DebugLogger
from core.models.pydantic_schemas import SupervisorDecision, ToolCallEvent, TraceContext
from core.observability.logging import log_event
from core.observability.tracing import (
    current_trace_ids,
    set_span_attributes,
    set_span_status,
    start_span,
)
from core.skills import SkillExecutor, SkillRegistry
from core.system_commands import handle_system_command
from core.tools import ToolRegistry
from core.tools.base import ToolConfirmationError

LOGGER = logging.getLogger(__name__)


async def _persist_memory_background(
    memory: MemoryStore,
    conversation_id: str,
    text: str,
    logger: logging.Logger,
) -> None:
    """Background task to persist memory without blocking response."""
    try:
        await memory.add_records([MemoryRecord(conversation_id=conversation_id, text=text)])
        logger.debug("Background memory persistence complete for %s", conversation_id)
    except Exception as e:
        logger.warning("Background memory persistence failed: %s", e)


class AgentService:
    """Coordinate the memory, LLM and metadata layers for agent execution.

    Handles the full lifecycle of agent requests including planning, skill execution,
    and self-correction via the supervisor loop. This is the main orchestration layer
    that coordinates all agents, tools, and skills.
    """

    _settings: Settings
    _litellm: LiteLLMClient
    _memory: MemoryStore
    _tool_registry: ToolRegistry
    _skill_registry: SkillRegistry | None
    context_manager: ContextManager

    def __init__(
        self,
        settings: Settings,
        litellm: LiteLLMClient,
        memory: MemoryStore,
        tool_registry: ToolRegistry | None = None,
        skill_registry: SkillRegistry | None = None,
    ):
        """Initialize the agent service with required dependencies.

        Args:
            settings: Application configuration settings.
            litellm: LiteLLM client for LLM calls.
            memory: Vector store for conversation memory.
            tool_registry: Registry of available tools (optional).
            skill_registry: Registry of validated skills (optional).
        """
        self._settings = settings
        self._litellm = litellm
        self._memory = memory
        self._tool_registry = tool_registry or ToolRegistry([])
        self._skill_registry = skill_registry

        self.context_manager = ContextManager(settings)

    # ────────────────────────────────────────────────────────────────────────────
    # Extracted methods from execute_stream for better testability
    # ────────────────────────────────────────────────────────────────────────────

    async def _setup_conversation_and_context(
        self,
        session: AsyncSession,
        conversation_id: str,
        request: AgentRequest,
    ) -> tuple[Conversation, Context | None, Session]:
        """Create or get conversation, context, and session hierarchy.

        Args:
            session: Database session
            conversation_id: UUID for the conversation
            request: The incoming agent request

        Returns:
            Tuple of (Conversation, Context, Session)
        """
        # Create/get conversation
        db_conversation = await self._ensure_conversation_exists(session, conversation_id, request)

        # Resolve active context
        db_context = await session.get(Context, db_conversation.context_id)

        # Create session for this request
        db_session = await self._get_or_create_session(session, conversation_id)

        return db_conversation, db_context, db_session

    async def _load_and_prepare_history(
        self,
        session: AsyncSession,
        db_session: Session,
        db_context: Context | None,
        db_conversation: Conversation,
        request: AgentRequest,
    ) -> tuple[list[AgentMessage], str]:
        """Load conversation history and prepare request metadata.

        Args:
            session: Database session
            db_session: The active Session
            db_context: The active Context (may be None)
            db_conversation: The Conversation
            request: The incoming agent request

        Returns:
            Tuple of (history, history_source)
        """
        # Load from request or database
        if request.messages:
            history = list(request.messages)
            history_source = "request"
            LOGGER.info(
                "Using %d messages from request.messages: %s",
                len(history),
                [(m.role, (m.content or "")[:50]) for m in history],
            )
        else:
            history = await self._load_conversation_history(session, db_session)
            history_source = "database"
            LOGGER.info("Loaded %d messages from database", len(history))

        # Inject context into request metadata
        if request.metadata is None:
            request.metadata = {}
        if db_conversation.current_cwd:
            request.metadata["cwd"] = db_conversation.current_cwd
        request.metadata["_db_session"] = session

        # Inject pinned files
        if db_context:
            await self._inject_pinned_files(history, db_context.pinned_files)

        # Inject workspace rules
        if db_conversation.current_cwd:
            await self._inject_workspace_rules(history, db_conversation.current_cwd)

        return history, history_source

    def _setup_agents_and_executors(
        self,
    ) -> tuple[
        PlannerAgent,
        PlanSupervisorAgent,
        StepExecutorAgent,
        StepSupervisorAgent,
        SkillExecutor | None,
        list[str],
    ]:
        """Instantiate all agents and executors.

        Returns:
            Tuple of (planner, plan_supervisor, executor, step_supervisor,
                      skill_executor, skill_names)
        """
        planner = PlannerAgent(self._litellm, model_name=self._settings.model_planner)

        skill_names_result = (
            self._skill_registry.get_skill_names()
            if self._skill_registry
            else get_available_skill_names()
        )
        skill_names = (
            list(skill_names_result) if isinstance(skill_names_result, set) else skill_names_result
        )
        plan_supervisor = PlanSupervisorAgent(
            litellm=self._litellm,
            model_name=self._settings.model_supervisor,
            tool_registry=self._tool_registry,
            skill_names=set(skill_names) if skill_names else None,
        )
        executor = StepExecutorAgent(self._memory, self._litellm, self._tool_registry)
        step_supervisor = StepSupervisorAgent(
            self._litellm, model_name=self._settings.model_supervisor
        )

        skill_executor: SkillExecutor | None = None
        if self._skill_registry:
            skill_executor = SkillExecutor(
                skill_registry=self._skill_registry,
                tool_registry=self._tool_registry,
                litellm=self._litellm,
            )

        return planner, plan_supervisor, executor, step_supervisor, skill_executor, skill_names

    async def _route_chat_request(
        self,
        request: AgentRequest,
        history: list[AgentMessage],
        session: AsyncSession,
        db_session: Session,
        conversation_id: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Handle direct chat routing (non-agentic path).

        Args:
            request: The agent request
            history: Conversation history
            session: Database session
            db_session: The active Session
            conversation_id: The conversation ID

        Yields:
            Event dictionaries for the chat response
        """
        # Direct LLM completion
        completion_text = await self._litellm.generate(history)

        # Record and persist
        session.add(
            Message(
                session_id=db_session.id,
                role="assistant",
                content=completion_text,
                trace_id=current_trace_ids().get("trace_id"),
            )
        )
        await session.commit()

        # Yield completion event
        yield {
            "type": "completion",
            "provider": "litellm",
            "model": self._settings.model_agentchat,
            "status": "ok",
            "trace": current_trace_ids(),
        }
        yield {"type": "content", "content": completion_text}

    async def _execute_step(
        self,
        plan_step: PlanStep,
        skill_executor: SkillExecutor | None,
        executor: StepExecutorAgent,
        request: AgentRequest,
        prompt_history: list[AgentMessage],
        retry_feedback: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute a single step, yielding events. Returns result as final yield.

        Args:
            plan_step: The step to execute
            skill_executor: Optional skill executor
            executor: The step executor agent
            request: The agent request
            prompt_history: Conversation history
            retry_feedback: Optional retry feedback from supervisor

        Yields:
            Event dictionaries, with final event being step_result
        """
        is_skill_step = plan_step.executor == "skill" or plan_step.action == "skill"
        step_result: StepResult | None = None
        awaiting_input_request: AwaitingInputRequest | None = None

        if is_skill_step and skill_executor:
            async for event in skill_executor.execute_stream(
                plan_step,
                request=request,
                retry_feedback=retry_feedback,
            ):
                if event["type"] == "content":
                    content = event["content"]
                    meta = event.get("metadata") or {}
                    yield {"type": "content", "content": content, "metadata": meta}

                    # Backward compatibility: detect text marker
                    if content and "[AWAITING_USER_INPUT" in content:
                        import re

                        match = re.search(r"\[AWAITING_USER_INPUT:(\w+)\]", content)
                        if match:
                            category_str = match.group(1).lower()

                            # Map legacy/alternative category names
                            category_aliases = {
                                "type_selection": "selection",
                            }

                            if category_str in category_aliases:
                                mapped = category_aliases[category_str]
                                LOGGER.warning(
                                    "HITL category '%s' mapped to '%s' - consider updating skill",
                                    category_str,
                                    mapped,
                                )
                                category_str = mapped

                            try:
                                category = AwaitingInputCategory(category_str)
                            except ValueError:
                                LOGGER.warning(
                                    "Invalid HITL category '%s' in skill '%s', "
                                    "falling back to CLARIFICATION",
                                    category_str,
                                    plan_step.tool or "unknown",
                                )
                                category = AwaitingInputCategory.CLARIFICATION
                            clean_prompt = re.sub(
                                r"\[AWAITING_USER_INPUT:\w+\]",
                                "",
                                content,
                            ).strip()
                            awaiting_input_request = AwaitingInputRequest(
                                category=category,
                                prompt=clean_prompt,
                                skill_name=plan_step.tool or "unknown",
                                context={},
                            )
                elif event["type"] == "awaiting_input":
                    meta = event.get("metadata") or {}
                    category_str = meta.get("category", "clarification")
                    try:
                        category = AwaitingInputCategory(category_str)
                    except ValueError:
                        LOGGER.warning(
                            "Invalid HITL category '%s', using CLARIFICATION", category_str
                        )
                        category = AwaitingInputCategory.CLARIFICATION
                    awaiting_input_request = AwaitingInputRequest(
                        category=category,
                        prompt=meta.get("prompt", ""),
                        skill_name=meta.get("skill_name", ""),
                        context=meta.get("context", {}),
                        options=meta.get("options"),
                        required=meta.get("required", True),
                    )
                    yield event
                elif event["type"] == "thinking":
                    meta = (event.get("metadata") or {}).copy()
                    meta["id"] = plan_step.id
                    yield {"type": "thinking", "content": event["content"], "metadata": meta}
                elif event["type"] == "skill_activity":
                    yield event
                elif event["type"] == "result":
                    step_result = event["result"]

                await asyncio.sleep(0)  # Force flush
        else:
            # Use legacy StepExecutorAgent
            async for event in executor.run_stream(
                plan_step,
                request=request,
                conversation_id=request.conversation_id or str(uuid.uuid4()),
                prompt_history=prompt_history,
            ):
                if event["type"] == "content":
                    yield {"type": "content", "content": event["content"]}
                elif event["type"] == "thinking":
                    meta = (event.get("metadata") or {}).copy()
                    meta["id"] = plan_step.id
                    yield {"type": "thinking", "content": event["content"], "metadata": meta}
                elif event["type"] == "result":
                    step_result = event["result"]

                await asyncio.sleep(0)  # Force flush

        # Yield result as final event
        yield {
            "type": "step_result",
            "result": step_result,
            "awaiting_input": awaiting_input_request,
        }

    def _should_auto_replan(self, step_result: StepResult, plan_step: PlanStep) -> tuple[bool, str]:
        """Check if step result indicates obvious failure requiring replan.

        This method detects common failure patterns that should trigger an automatic
        replan without requiring supervisor LLM evaluation. This saves latency and cost
        for obvious failures like authentication errors, timeouts, or resource not found.

        Only checks text patterns when the step explicitly reported an error status.
        Successful results (status="ok") are always sent to the step supervisor for
        proper evaluation, avoiding false positives from output text that incidentally
        contains error-like keywords (e.g., work item titles mentioning "timeout").

        Args:
            step_result: The result from step execution
            plan_step: The plan step that was executed

        Returns:
            Tuple of (should_replan, reason). If should_replan is True,
            the step should trigger a replan immediately.
        """
        # Only auto-replan on explicit error status. Successful results should
        # be evaluated by the step supervisor to avoid false positives.
        if step_result.status != "error":
            LOGGER.debug(
                "Auto-replan skipped for step '%s' (status=%s, not error)",
                plan_step.label,
                step_result.status,
            )
            return False, ""

        output = str(step_result.result.get("output", "") or step_result.result.get("error", ""))
        LOGGER.info(
            "Auto-replan checking error output for step '%s': %s",
            plan_step.label,
            output[:200],
        )

        # Check for specific error patterns to provide better feedback
        output_lower = output.lower()

        # Pattern 1: Authentication/authorization failures
        auth_patterns = [
            "401 Unauthorized",
            "403 Forbidden",
            "authentication failed",
            "invalid credentials",
            "token expired",
            "access denied",
        ]
        for pattern in auth_patterns:
            if pattern.lower() in output_lower:
                return True, f"Authentication/authorization error detected: {pattern}"

        # Pattern 2: Resource not found (tool target doesn't exist)
        if "404 Not Found" in output or "resource not found" in output_lower:
            return True, f"Resource not found: {output[:200]}"

        # Pattern 3: Timeout
        if "timed out" in output_lower or "timeout" in output_lower:
            return True, f"Step timed out: {output[:200]}"

        # Generic error fallback
        return True, f"Tool returned error: {output[:200]}"

    async def _execute_step_with_retry(
        self,
        plan_step: PlanStep,
        skill_executor: SkillExecutor | None,
        executor: StepExecutorAgent,
        step_supervisor: StepSupervisorAgent,
        request: AgentRequest,
        prompt_history: list[AgentMessage],
        step_index: int,
        session: AsyncSession,
        db_session: Session,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute step with retry logic, yielding all events.

        Args:
            plan_step: The step to execute
            skill_executor: Optional skill executor
            executor: The step executor agent
            step_supervisor: The step supervisor
            request: The agent request
            prompt_history: Conversation history
            step_index: Index of the step in the plan
            session: Database session
            db_session: The active Session

        Yields:
            Event dictionaries including step outcome
        """
        retry_count = 0
        retry_feedback: str | None = None
        trace_id = current_trace_ids().get("trace_id", "unknown")
        debug_logger = DebugLogger(session)
        conversation_id = request.conversation_id or str(uuid.uuid4())

        while True:
            # Yield step start
            yield {
                "type": "step_start",
                "content": plan_step.label,
                "metadata": {
                    "role": "Executor",
                    "id": plan_step.id,
                    "action": plan_step.action,
                    "tool": plan_step.tool,
                    "executor": plan_step.executor,
                    "args": plan_step.args,
                    "retry_count": retry_count,
                },
            }

            if plan_step.action == "tool" or plan_step.action == "skill":
                yield {
                    "type": "tool_start",
                    "content": None,
                    "tool_call": {
                        "name": plan_step.tool,
                        "arguments": plan_step.args,
                    },
                    "metadata": {"role": "Executor", "id": plan_step.id},
                }

            # Execute step
            step_execution_result: StepResult | None = None
            awaiting_input_request: AwaitingInputRequest | None = None

            try:
                async for event in self._execute_step(
                    plan_step, skill_executor, executor, request, prompt_history, retry_feedback
                ):
                    if event["type"] == "step_result":
                        step_execution_result = event["result"]
                        awaiting_input_request = event.get("awaiting_input")
                    else:
                        # Track skill content for smart completion skip
                        if event["type"] == "content":
                            content = event.get("content", "")
                            if content and len(content) > 100:
                                yield {"type": "skill_content_yielded", "value": True}
                        yield event

                if not step_execution_result:
                    LOGGER.error("Executor failed to yield result (Stream ended prematurely)")
                    yield {"type": "error", "content": "Step execution ended without result."}
                    return

                # Debug: Log tool call result
                if plan_step.action in ("tool", "skill"):
                    await debug_logger.log_tool_call(
                        trace_id=trace_id,
                        tool_name=plan_step.tool or "unknown",
                        args=plan_step.args or {},
                        result=step_execution_result.result,
                        conversation_id=conversation_id,
                    )

            except ToolConfirmationError as exc:
                LOGGER.info(f"Step {plan_step.id} paused for confirmation")
                msg_content = (
                    f"Action paused. Tool '{exc.tool_name}' needs confirmation.\n"
                    f"Arguments: {exc.tool_args}\nReply 'CONFIRM' to proceed."
                )
                session.add(
                    Message(
                        session_id=db_session.id,
                        role="system",
                        content=msg_content,
                        trace_id=current_trace_ids().get("trace_id"),
                    )
                )
                await session.commit()
                yield {
                    "type": "content",
                    "content": msg_content,
                    "metadata": {"status": "confirmation_required"},
                }
                return

            # Skip supervision for completion steps
            if plan_step.action == "completion":
                outcome = StepOutcome.SUCCESS
                reason = "Completion step (skipped review)"
                suggested_fix = None
            else:
                # Check for auto-detectable failures BEFORE calling supervisor
                with start_span(
                    "auto_replan.check",
                    attributes={
                        "step_label": plan_step.label,
                        "step_status": step_execution_result.status,
                    },
                ) as auto_span:
                    should_auto_replan, auto_reason = self._should_auto_replan(
                        step_execution_result, plan_step
                    )
                    auto_span.set_attribute("triggered", should_auto_replan)
                    if auto_reason:
                        auto_span.set_attribute("reason", auto_reason)

                if should_auto_replan:
                    LOGGER.info(
                        "Auto-replan triggered for step '%s': %s",
                        plan_step.label,
                        auto_reason,
                    )
                    outcome = StepOutcome.REPLAN
                    reason = auto_reason
                    suggested_fix = None
                else:
                    # Normal supervisor evaluation
                    outcome, reason, suggested_fix = await step_supervisor.review(
                        plan_step, step_execution_result, retry_count=retry_count
                    )

            # Debug: Log supervisor decision
            await debug_logger.log_supervisor(
                trace_id=trace_id,
                step_label=plan_step.label,
                outcome=outcome.value,
                reason=reason,
                conversation_id=conversation_id,
            )

            # Handle outcome
            if outcome == StepOutcome.SUCCESS:
                # Yield step result for history update
                yield {
                    "type": "step_outcome",
                    "outcome": "success",
                    "result": step_execution_result,
                    "awaiting_input": awaiting_input_request,
                }
                return

            elif outcome == StepOutcome.RETRY and retry_count < 1:
                retry_count += 1
                retry_feedback = (
                    f"Previous attempt failed: {reason}. {suggested_fix or 'Please try again.'}"
                )
                yield {
                    "type": "thinking",
                    "content": f"Retrying step: {reason}",
                    "metadata": {
                        "role": "Supervisor",
                        "outcome": "retry",
                        "retry_count": retry_count,
                    },
                }
                LOGGER.info("Step '%s' retry %d: %s", plan_step.label, retry_count, reason)
                continue  # Retry the step

            elif outcome == StepOutcome.ABORT:
                LOGGER.error("Step '%s' ABORTED: %s", plan_step.label, reason)
                yield {
                    "type": "error",
                    "content": f"Execution aborted: {reason}",
                    "metadata": {"outcome": "abort"},
                }
                yield {"type": "step_outcome", "outcome": "abort", "reason": reason}
                return

            else:
                # REPLAN or max retries reached
                yield {
                    "type": "step_outcome",
                    "outcome": "replan",
                    "reason": reason,
                    "suggested_fix": suggested_fix,
                }
                return

    async def _generate_plan(
        self,
        planner: PlannerAgent,
        plan_supervisor: PlanSupervisorAgent,
        request: AgentRequest,
        history: list[AgentMessage],
        tool_descriptions: list[dict[str, Any]],
        available_skills_text: str,
        replan_count: int,
        max_replans: int,
        retry_feedback: str | None = None,
    ) -> AsyncGenerator[dict[str, Any] | Plan, None]:
        """Generate and validate execution plan, yielding thinking events.

        Args:
            planner: The planner agent
            plan_supervisor: The plan supervisor
            request: The agent request
            history: Conversation history
            tool_descriptions: List of available tool descriptions
            available_skills_text: Available skills registry index
            replan_count: Current replan count
            max_replans: Maximum replans allowed
            retry_feedback: Optional feedback from previous plan failure

        Yields:
            Event dictionaries and final Plan object
        """
        # Check for injected plan
        if request.metadata and request.metadata.get("plan") and replan_count == 0:
            LOGGER.info("Using injected plan from metadata")
            injected_plan = Plan(**request.metadata["plan"])
            yield {"type": "plan", "status": "created", "description": injected_plan.description}
            yield injected_plan
            return

        # Generate plan
        trace_id = current_trace_ids().get("trace_id", "unknown")
        if replan_count == 0:
            yield {
                "type": "thinking",
                "content": f"Generating plan... [TraceID: {trace_id}]",
                "metadata": {"role": "Planner"},
            }
        else:
            yield {
                "type": "thinking",
                "content": f"Re-planning (attempt {replan_count}/{max_replans})...",
                "metadata": {"role": "Planner", "replan": True, "attempt": replan_count},
            }

        plan: Plan | None = None
        async for event in planner.generate_stream(
            request,
            history=history,
            tool_descriptions=tool_descriptions,
            available_skills_text=available_skills_text,
        ):
            if event["type"] == "token":
                # Do not show raw JSON plan to user
                pass
            elif event["type"] == "plan":
                plan = event["plan"]

        if plan is None:
            raise ValueError("Planner returned no plan")

        reviewed_plan = await plan_supervisor.review(plan)
        assert reviewed_plan is not None  # Mypy guard

        if reviewed_plan is None:  # Runtime guard
            raise ValueError("Plan became None after review")

        plan = reviewed_plan

        # Enrich trace with plan details
        set_span_attributes(
            {
                "plan.description": plan.description,
                "plan.steps_count": len(plan.steps) if plan.steps else 0,
            }
        )

        # Bridge gap between plan and execution
        yield {
            "type": "thinking",
            "content": "Plan approved. Starting execution...",
            "metadata": {
                "role": "Supervisor",
                "step": "init",
                "status": "planning_complete",
                "stream": False,
                "bold": True,
            },
        }
        await asyncio.sleep(0)  # Force flush

        yield {
            "type": "plan",
            "status": "created",
            "description": plan.description,
            "plan": plan.model_dump(),
            "replan_count": replan_count,
            **current_trace_ids(),
        }

        yield plan

    async def _maybe_generate_completion(
        self,
        prompt_history: list[AgentMessage],
        skill_content_yielded: bool,
        has_completion_step: bool,
        existing_completion: str,
        session: AsyncSession,
        awaiting_input_request: AwaitingInputRequest | None = None,
        has_skill_steps: bool = False,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Generate completion if needed, yielding content events.

        Args:
            prompt_history: Conversation history
            skill_content_yielded: Whether skill already yielded substantial content
            has_completion_step: Whether plan had explicit completion step
            existing_completion: Existing completion text from completion step
            session: Database session
            awaiting_input_request: If set, skill is awaiting user input - skip completion
            has_skill_steps: If True, plan had skill steps - skill output IS the answer

        Yields:
            Event dictionaries with completion_ready as final event
        """
        # Skip completion if skill is awaiting user input
        if awaiting_input_request:
            LOGGER.info("Skipping completion generation - skill awaiting user input")
            # Use skill output as completion text for database storage
            for msg in reversed(prompt_history):
                if msg.role == "tool" and msg.content:
                    yield {"type": "completion_ready", "text": msg.content}
                    return
            yield {"type": "completion_ready", "text": ""}
            return

        # Skip completion if plan had skill steps - skill output IS the answer
        if has_skill_steps:
            LOGGER.info("Skipping completion - plan had skill steps")
            for msg in reversed(prompt_history):
                if msg.role == "tool" and msg.content:
                    yield {"type": "completion_ready", "text": msg.content}
                    return
            yield {"type": "completion_ready", "text": ""}
            return

        if existing_completion:
            yield {"type": "completion_ready", "text": existing_completion}
            return

        if skill_content_yielded and not has_completion_step:
            # Get the last skill's output from prompt history for database storage
            for msg in reversed(prompt_history):
                if msg.role == "tool" and msg.content:
                    completion_text = msg.content
                    LOGGER.info("Using skill output as final response (no completion step)")
                    yield {"type": "completion_ready", "text": completion_text}
                    return
            # Fallback to empty if no tool message found
            yield {"type": "completion_ready", "text": ""}
            return

        # Detect work items for language preservation
        work_item_detected = self._detect_work_items(prompt_history)

        if work_item_detected:
            LOGGER.info("Work item draft detected. Injecting language preservation instruction.")
            prompt_history.append(
                AgentMessage(
                    role="system",
                    content=(
                        "IMPORTANT: The conversation contains Azure DevOps work item "
                        "drafts that MUST remain in English. Do not translate work "
                        "item titles, descriptions, or acceptance criteria. "
                        "Only translate your commentary."
                    ),
                )
            )

        yield {
            "type": "thinking",
            "content": "Generating final answer...",
            "metadata": {"role": "Executor"},
        }

        # Add completion instruction
        prompt_history.append(
            AgentMessage(
                role="system",
                content=(
                    "Based on the tool outputs above, provide a brief, helpful "
                    "response to the user. Report what actions were taken and "
                    "their results. Be concise and direct."
                ),
            )
        )

        # Debug: Log the full prompt being sent to completion LLM
        debug_logger = DebugLogger(session)
        trace_id = current_trace_ids().get("trace_id", "unknown")
        conversation_id = str(uuid.uuid4())  # Fallback, should be passed in
        await debug_logger.log_completion_prompt(
            trace_id=trace_id,
            prompt_history=prompt_history,
            conversation_id=conversation_id,
        )

        # Use composer model for generating final answers
        completion_text = await self._litellm.generate(
            prompt_history, model=self._settings.model_composer
        )

        # Debug: Log the completion response
        await debug_logger.log_completion_response(
            trace_id=trace_id,
            response=completion_text,
            model=self._settings.model_composer,
            conversation_id=conversation_id,
        )

        # Yield content
        yield {
            "type": "content",
            "content": completion_text,
            "metadata": {
                "provider": "litellm",
                "model": self._settings.model_composer,
            },
        }

        yield {"type": "completion_ready", "text": completion_text}

    async def _finalize_and_persist(
        self,
        session: AsyncSession,
        db_session: Session,
        conversation_id: str,
        completion_text: str,
        prompt_history: list[AgentMessage],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Persist results and emit final events.

        Args:
            session: Database session
            db_session: The active Session
            conversation_id: The conversation ID
            completion_text: Final completion text
            prompt_history: Full conversation history

        Yields:
            Final events including history snapshot
        """
        # Record assistant message
        if completion_text:
            session.add(
                Message(
                    session_id=db_session.id,
                    role="assistant",
                    content=completion_text,
                    trace_id=current_trace_ids().get("trace_id"),
                )
            )

        # Background memory persistence (fire-and-forget)
        if self._memory and completion_text:
            asyncio.create_task(
                _persist_memory_background(self._memory, conversation_id, completion_text, LOGGER)
            )

        # Commit transaction
        await session.commit()

        # Log event
        LOGGER.info("Completed conversation %s", conversation_id)
        log_event(
            SupervisorDecision(
                item_id=conversation_id,
                decision="ok",
                comments="Conversation complete",
                trace=TraceContext(**current_trace_ids()),
            )
        )

        # Yield history snapshot
        final_history = list(prompt_history)
        if completion_text:
            final_history.append(AgentMessage(role="assistant", content=completion_text))

        yield {"type": "history_snapshot", "messages": final_history}

    async def _execute_agentic(
        self,
        request: AgentRequest,
        history: list[AgentMessage],
        session: AsyncSession,
        db_session: Session,
        db_conversation: Conversation,
        planner: PlannerAgent,
        plan_supervisor: PlanSupervisorAgent,
        executor: StepExecutorAgent,
        step_supervisor: StepSupervisorAgent,
        skill_executor: SkillExecutor | None,
        skill_names: list[str],
        conversation_id: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute agentic workflow with planning and step execution.

        Args:
            request: The agent request
            history: Conversation history
            session: Database session
            db_session: The active Session
            db_conversation: The Conversation
            planner: The planner agent
            plan_supervisor: The plan supervisor
            executor: The step executor agent
            step_supervisor: The step supervisor
            skill_executor: Optional skill executor
            skill_names: List of available skill names
            conversation_id: The conversation ID

        Yields:
            Event dictionaries for the agentic execution
        """
        debug_logger = DebugLogger(session)
        trace_id = current_trace_ids().get("trace_id", str(uuid.uuid4()))

        # Execute metadata tools
        request_metadata = request.metadata or {}
        metadata_tool_results = await self._execute_tools(request_metadata)
        for tool_res in metadata_tool_results:
            yield {
                "type": "tool_output",
                "content": tool_res.get("output"),
                "tool_call": {"name": tool_res.get("name")},
                "metadata": tool_res,
            }
            await asyncio.sleep(0)  # Force flush

        # Build history with tool results
        history_with_tools = list(history)
        for tool_res in metadata_tool_results:
            if tool_res.get("status") == "ok" and tool_res.get("output"):
                msg_content = f"Tool {tool_res['name']} output:\n{tool_res['output']}"
                history_with_tools.append(AgentMessage(role="system", content=msg_content))

        # Prepare tool descriptions for planning
        allowlist = self._parse_tool_allowlist(request_metadata.get("tools"))
        target_tools = allowlist or {
            t.name
            for t in self._tool_registry.list_tools()
            if getattr(t, "category", "domain") == "orchestration"
        }
        tool_descriptions = self._describe_tools(target_tools)
        available_skills_text = get_registry_index()

        # Initialize adaptive execution state
        max_replans = 3
        replans_remaining = max_replans
        prompt_history = list(history_with_tools)
        completion_text = ""
        completion_provider = "litellm"
        completion_model = self._settings.model_composer
        execution_complete = False
        skill_content_yielded = False
        awaiting_input_request: AwaitingInputRequest | None = None

        # Adaptive execution loop
        while replans_remaining >= 0 and not execution_complete:
            replan_count = max_replans - replans_remaining
            set_span_attributes({"replan_count": replan_count})

            # Apply exponential backoff on re-plan attempts
            if replan_count > 0:
                backoff_delay = 0.5 * (2 ** (replan_count - 1))
                LOGGER.info(
                    "Re-plan backoff: waiting %ss before attempt %d",
                    backoff_delay,
                    replan_count,
                )
                await asyncio.sleep(backoff_delay)

            # Generate plan
            plan: Plan | None = None
            async for event in self._generate_plan(
                planner,
                plan_supervisor,
                request,
                prompt_history,
                tool_descriptions,
                available_skills_text,
                replan_count,
                max_replans,
            ):
                if isinstance(event, Plan):
                    plan = event
                else:
                    yield event

            # Debug: Log the generated plan
            if plan:
                await debug_logger.log_plan(
                    trace_id=trace_id,
                    plan=plan,
                    conversation_id=conversation_id,
                )

            if plan is None:
                raise ValueError("Plan is None after generation")

            if not plan.steps:
                plan = self._fallback_plan(request.prompt)

            # Execute steps
            needs_replan = False
            abort_execution = False

            for step_index, plan_step in enumerate(plan.steps):
                # Skip completion step if skill is awaiting user input
                if plan_step.action == "completion" and awaiting_input_request:
                    LOGGER.info("Skipping completion step - skill awaiting user input")
                    execution_complete = True
                    break

                # Execute step with retry logic
                step_outcome = None
                step_result = None
                replan_reason: str | None = None
                replan_suggested_fix: str | None = None
                async for event in self._execute_step_with_retry(
                    plan_step,
                    skill_executor,
                    executor,
                    step_supervisor,
                    request,
                    prompt_history,
                    step_index,
                    session,
                    db_session,
                ):
                    if event["type"] == "step_outcome":
                        step_outcome = event["outcome"]
                        step_result = event.get("result")
                        awaiting_input_request = event.get("awaiting_input")
                        replan_reason = event.get("reason")
                        replan_suggested_fix = event.get("suggested_fix")

                        # Handle completion steps
                        if plan_step.action == "completion" and step_outcome == "success":
                            if step_result:
                                completion_text = step_result.result.get("completion", "")
                                completion_provider = plan_step.provider or completion_provider
                                completion_model = step_result.result.get("model", completion_model)
                            execution_complete = True
                    elif event["type"] == "skill_content_yielded":
                        skill_content_yielded = event["value"]
                    elif event["type"] == "awaiting_input":
                        # HITL: Store state for resume and mark execution complete
                        meta = event.get("metadata") or {}
                        await self._store_pending_hitl(session, db_conversation, meta)
                        awaiting_input_request = AwaitingInputRequest(
                            category=AwaitingInputCategory(meta.get("category", "clarification")),
                            prompt=meta.get("prompt", ""),
                            skill_name=meta.get("skill_name", ""),
                            options=meta.get("options"),
                        )
                        execution_complete = True
                        yield event
                    else:
                        yield event

                if not step_outcome:
                    LOGGER.error("Step execution ended without outcome")
                    yield {"type": "error", "content": "Step execution ended without outcome."}
                    return

                # Emit step result for compatibility
                if plan_step.action in ("tool", "skill") and step_result:
                    chunk_type = "tool_output"
                    tool_call = {"name": plan_step.tool}
                    content_str = str(step_result.result.get("output") or step_result.status)

                    # Check for trivial status content
                    is_trivial = content_str.lower() in ("ok", "completed step")

                    if not is_trivial:
                        meta = {
                            "status": step_result.status,
                            "decision": "ok" if step_outcome == "success" else "adjust",
                            "outcome": step_outcome,
                            "id": plan_step.id,
                            "action": plan_step.action,
                            "tool": plan_step.tool,
                            "name": plan_step.tool,
                            "executor": plan_step.executor,
                            "output": str(step_result.result.get("output") or ""),
                            "source_count": step_result.result.get("source_count", 0),
                        }
                        if plan_step.executor == "skill" or plan_step.action == "skill":
                            meta["skill"] = plan_step.tool

                        yield {
                            "type": chunk_type,
                            "content": content_str,
                            "tool_call": tool_call,
                            "metadata": meta,
                        }

                # Update prompt history
                if step_result:
                    prompt_history.extend(step_result.messages)
                    if plan_step.action in ("tool", "skill"):
                        session.add(
                            Message(
                                session_id=db_session.id,
                                role="tool",
                                content=str(step_result.result.get("output", "")),
                                trace_id=current_trace_ids().get("trace_id"),
                            )
                        )

                # Handle replan outcome
                if step_outcome == "replan":
                    reason = replan_reason or "Step failed"
                    suggested_fix = replan_suggested_fix

                    LOGGER.warning(
                        "Supervisor requested replan for step '%s': %s",
                        plan_step.label,
                        reason,
                    )

                    if replans_remaining > 0:
                        # Inject feedback for re-planning
                        feedback_msg = (
                            f"Step '{plan_step.label}' failed validation. "
                            f"Supervisor feedback: {reason}."
                        )
                        if suggested_fix:
                            feedback_msg += f"\n\nSuggested approach: {suggested_fix}"
                        feedback_msg += "\n\nPlease generate a new plan to address this issue."
                        prompt_history.append(AgentMessage(role="system", content=feedback_msg))

                        yield {
                            "type": "thinking",
                            "content": f"Step needs replan: {reason}",
                            "metadata": {
                                "role": "Supervisor",
                                "outcome": "replan",
                                "reason": reason,
                                "suggested_fix": suggested_fix,
                                "replans_remaining": replans_remaining - 1,
                            },
                        }

                        needs_replan = True
                        replans_remaining -= 1
                        break  # Exit step loop to trigger re-plan
                    else:
                        # Max replans reached
                        LOGGER.error(
                            "Max replans (%d) reached. Continuing despite failure.", max_replans
                        )
                        yield {
                            "type": "thinking",
                            "content": (
                                f"Step issue: {reason}. "
                                f"Max re-plans ({max_replans}) reached. Continuing..."
                            ),
                            "metadata": {"role": "Supervisor", "max_replans_reached": True},
                        }

                elif step_outcome == "abort":
                    abort_execution = True
                    break  # Exit step loop

            # If no replan needed and loop completed normally
            if not needs_replan and not abort_execution:
                execution_complete = True

            # If abort was triggered, stop the entire adaptive loop
            if abort_execution:
                break

        # Check if plan had an explicit completion step
        has_completion_step = bool(
            plan is not None
            and plan.steps is not None
            and any(s.action == "completion" for s in plan.steps)
        )

        # Check if plan had skill steps - if so, skill output IS the answer
        has_skill_steps = bool(
            plan is not None
            and plan.steps is not None
            and any(s.executor == "skill" or s.action == "skill" for s in plan.steps)
        )

        # Generate completion if needed
        async for event in self._maybe_generate_completion(
            prompt_history,
            skill_content_yielded,
            has_completion_step,
            completion_text,
            session,
            awaiting_input_request,
            has_skill_steps=has_skill_steps,
        ):
            if event["type"] == "completion_ready":
                completion_text = event["text"]
            else:
                yield event

        # Finalize and persist
        async for event in self._finalize_and_persist(
            session,
            db_session,
            conversation_id,
            completion_text,
            prompt_history,
        ):
            yield event

    async def execute_stream(
        self, request: AgentRequest, session: AsyncSession
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute agent request and yield streaming events for real-time updates.

        This is the main entry point for agent execution. It handles:
        - Conversation and context setup
        - HITL (human-in-the-loop) resume if pending
        - System command interception
        - Router-based execution path (chat vs agentic)
        - Streaming events for UI updates

        Args:
            request: The agent request containing prompt and metadata.
            session: Database session for persistence.

        Yields:
            Event dictionaries with type, content, and metadata.
        """
        conversation_id = request.conversation_id or str(uuid.uuid4())
        LOGGER.info("Processing prompt for conversation %s", conversation_id)

        with start_span(
            "agent.request",
            attributes={
                "conversation_id": conversation_id,
                "input_size": len(request.prompt),
                "prompt": request.prompt[:500] if request.prompt else "",
            },
        ):
            try:
                # Phase 1: Setup conversation and context
                (
                    db_conversation,
                    db_context,
                    db_session,
                ) = await self._setup_conversation_and_context(session, conversation_id, request)

                # Phase 1.5: Check for pending HITL and resume if present
                pending_hitl = (db_conversation.conversation_metadata or {}).get("pending_hitl")
                if pending_hitl:
                    LOGGER.info("Found pending HITL for conversation %s", conversation_id)
                    # Yield trace_id first
                    trace_id = current_trace_ids().get("trace_id", str(uuid.uuid4()))
                    yield {
                        "type": "trace_info",
                        "trace_id": trace_id,
                        "conversation_id": conversation_id,
                    }

                    async for event in self._resume_hitl(
                        request,
                        session,
                        db_session,
                        db_conversation,
                        pending_hitl,
                    ):
                        yield event
                    return

                # System Command Interceptor
                sys_output = await handle_system_command(
                    request.prompt, self, session, conversation_id
                )
                if sys_output:
                    await session.commit()
                    yield {
                        "type": "content",
                        "content": sys_output,
                        "metadata": {"system_command": True},
                    }
                    return

                # Validate context exists
                if not db_context:
                    LOGGER.warning("Context missing for conversation %s", conversation_id)
                    yield {"type": "error", "content": "Error: Context missing."}
                    return

                # Phase 2: Load and prepare history
                history, history_source = await self._load_and_prepare_history(
                    session, db_session, db_context, db_conversation, request
                )

                # Debug logging
                debug_logger = DebugLogger(session)
                trace_id = current_trace_ids().get("trace_id", str(uuid.uuid4()))

                # Yield trace_id as first event for debugging/observability
                yield {
                    "type": "trace_info",
                    "trace_id": trace_id,
                    "conversation_id": conversation_id,
                }

                await debug_logger.log_request(
                    trace_id=trace_id,
                    prompt=request.prompt,
                    messages=request.messages,
                    metadata=request.metadata,
                    conversation_id=conversation_id,
                )
                await debug_logger.log_history(
                    trace_id=trace_id,
                    source=history_source,
                    messages=history,
                    conversation_id=conversation_id,
                )

                # Phase 3: Setup agents and executors
                (
                    planner,
                    plan_supervisor,
                    executor,
                    step_supervisor,
                    skill_executor,
                    skill_names,
                ) = self._setup_agents_and_executors()

                # Phase 4: Route request
                routing_decision = (request.metadata or {}).get(
                    "routing_decision", RoutingDecision.AGENTIC
                )
                LOGGER.info(f"Handling request with routing decision: {routing_decision}")
                set_span_attributes({"routing_decision": routing_decision})

                # Record user message
                user_message = AgentMessage(role="user", content=request.prompt)
                history.append(user_message)
                session.add(
                    Message(
                        session_id=db_session.id,
                        role="user",
                        content=request.prompt,
                        trace_id=current_trace_ids().get("trace_id"),
                    )
                )

                if routing_decision == RoutingDecision.CHAT:
                    async for event in self._route_chat_request(
                        request, history, session, db_session, conversation_id
                    ):
                        yield event
                    return

                # Phase 5: Agentic execution
                async for event in self._execute_agentic(
                    request,
                    history,
                    session,
                    db_session,
                    db_conversation,
                    planner,
                    plan_supervisor,
                    executor,
                    step_supervisor,
                    skill_executor,
                    skill_names,
                    conversation_id,
                ):
                    yield event

            except Exception as e:
                set_span_status("ERROR", str(e))
                raise e

    async def handle_request(self, request: AgentRequest, session: AsyncSession) -> AgentResponse:
        """Process agent request and return complete response (non-streaming).

        Backward compatibility wrapper around execute_stream that collects
        all streaming events into a single AgentResponse object.

        Args:
            request: The agent request to process.
            session: Database session for persistence.

        Returns:
            Complete agent response with final text and metadata.
        """
        # Backward compatibility wrapper
        steps = []
        response_text = ""
        conversation_id = request.conversation_id or str(uuid.uuid4())

        # Metadata copy to support updates
        response_metadata = dict(request.metadata or {})
        messages = []

        # Stateful aggregation
        current_step = None

        async for chunk in self.execute_stream(request, session):
            c_type = chunk.get("type")

            if c_type == "content":
                response_text = chunk.get("content", "")

            # Collect Plan
            elif c_type == "plan":
                steps.append(chunk)
                # Inject plan into metadata for legacy tests
                if "plan" in chunk:
                    response_metadata["plan"] = chunk["plan"]

            # Map step_start back to legacy 'plan_step'
            elif c_type == "step_start":
                meta = chunk.get("metadata", {})
                legacy_step = {
                    "type": "plan_step",
                    "id": meta.get("id"),
                    "label": chunk.get("content"),
                    "action": meta.get("action"),
                    "tool": meta.get("tool"),
                    "executor": meta.get("executor"),
                    "args": meta.get("args"),
                    "result": {},  # Initialize result container
                }
                steps.append(legacy_step)
                current_step = legacy_step

            # Capture output/result into the current step
            elif c_type == "tool_output":
                # Restore legacy tool_results in metadata
                if "tool_results" not in response_metadata:
                    response_metadata["tool_results"] = []
                response_metadata["tool_results"].append(chunk.get("metadata"))

                if current_step:
                    # Update the result of the current step
                    current_step["result"] = {
                        "status": chunk.get("metadata", {}).get("status", "ok"),
                        "output": chunk.get("content"),
                        "decision": chunk.get("metadata", {}).get("decision"),
                        # "completion": ... if needed
                    }
                else:
                    # Agentic flow: append as a tool step
                    steps.append(
                        {
                            "type": "tool",
                            "name": chunk.get("metadata", {}).get("name"),
                            "tool": chunk.get("metadata", {}).get("name"),
                            "output": chunk.get("content"),
                            "status": chunk.get("metadata", {}).get("status"),
                            "metadata": chunk.get("metadata"),
                        }
                    )

            elif c_type == "thinking":
                if current_step:
                    current_step["result"] = {
                        "status": chunk.get("metadata", {}).get("status", "ok"),
                        "output": chunk.get("content"),
                        "decision": chunk.get("metadata", {}).get("decision"),
                    }

            # Collect explicit legacy types
            elif c_type in ["tool", "completion"]:
                steps.append(chunk)

            # Capture history snapshot
            elif c_type == "history_snapshot":
                messages = chunk.get("messages", [])

        # Fallback if no history snapshot (e.g. error or empty stream)
        if not messages:
            # Try DB fetch (might fail in mocks, so catch generic)
            # Try DB fetch (might fail in mocks, so catch generic)
            with contextlib.suppress(Exception):
                messages = await self.get_history(conversation_id, session)

        # Add trace_id to metadata for debugging/observability
        trace_ids = current_trace_ids()
        if trace_ids.get("trace_id"):
            response_metadata["trace_id"] = trace_ids["trace_id"]

        return AgentResponse(
            response=response_text,
            conversation_id=conversation_id,
            steps=steps,
            metadata=response_metadata,
            messages=messages,
        )

    async def list_models(self) -> dict[str, Any]:
        """List available models compatible with OpenAI API format.

        Returns:
            Dict with 'data' list containing model metadata.
        """
        return {
            "data": [
                {
                    "id": "ai-agent",
                    "object": "model",
                    "created": 1700000000,
                    "owned_by": "system",
                }
            ],
            "object": "list",
        }

    async def get_history(self, conversation_id: str, session: AsyncSession) -> list[AgentMessage]:
        """Retrieve conversation history from database.

        Args:
            conversation_id: UUID of the conversation.
            session: Database session.

        Returns:
            List of messages in chronological order.
        """
        stmt = (
            select(Message)
            .join(Session)
            .where(Session.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
        )
        result = await session.execute(stmt)
        db_messages = result.scalars().all()

        return [AgentMessage(role=msg.role, content=msg.content) for msg in db_messages]

    async def _execute_tools(self, metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Execute requested tools and return a structured result list."""

        if not metadata:
            return []

        allowlist = self._parse_tool_allowlist(metadata.get("tools"))
        raw_calls = metadata.get("tool_calls")
        if not raw_calls:
            return []
        if isinstance(raw_calls, dict):
            call_items = [raw_calls]
        elif isinstance(raw_calls, list):
            call_items = list(raw_calls)
        else:
            LOGGER.warning("Ignoring tool_calls because it is not a list or dict")
            return []

        results: list[dict[str, Any]] = []
        for entry in call_items:
            tool_name: str | None = None
            call_args: dict[str, Any] = {}
            if isinstance(entry, str):
                tool_name = entry
            elif isinstance(entry, dict):
                tool_name = entry.get("name")
                args_field = entry.get("args")
                if isinstance(args_field, dict):
                    call_args = args_field
                elif args_field:
                    LOGGER.warning("Ignoring non-dict args for tool %s", tool_name)
            else:  # pragma: no cover - defensive path for unexpected structures
                LOGGER.warning("Skipping malformed tool call entry: %s", entry)
                continue

            if not tool_name:
                LOGGER.warning("Encountered tool call without a name; skipping")
                continue

            result = await self._run_tool_call(str(tool_name), call_args, allowlist=allowlist)
            results.append(result)
        return results

    async def _run_tool_call(
        self,
        tool_name: str,
        call_args: dict[str, Any],
        *,
        allowlist: set[str] | None = None,
    ) -> dict[str, Any]:
        """Run a single tool invocation while normalizing the output."""

        result: dict[str, Any] = {"name": tool_name}
        if allowlist is not None and tool_name not in allowlist:
            result.update({"status": "skipped", "reason": "not-allowed"})
            return result

        if not tool_name:
            result.update({"status": "error", "error": "missing tool name"})
            return result

        tool = self._tool_registry.get(tool_name) if self._tool_registry else None
        if not tool:
            LOGGER.warning("Requested tool %s is not registered", tool_name)
            result.update({"status": "missing"})
            return result

        sanitized_args = call_args if isinstance(call_args, dict) else {}
        with start_span(f"tool.call.{tool_name}"):
            # Observability: Capture arguments
            set_span_attributes({"args": str(sanitized_args)})

            try:
                output = await tool.run(**sanitized_args)
                status = "ok"
                set_span_status("OK")
            except Exception as exc:  # pragma: no cover - depends on tool implementation
                LOGGER.exception("Tool %s execution failed", tool_name)
                # Observability: Capture failure
                set_span_status("ERROR", str(exc))

                result.update({"status": "error", "error": str(exc)})
                status = "error"
                log_event(
                    ToolCallEvent(
                        name=tool_name,
                        args=sanitized_args,
                        status=status,
                        output_preview=str(exc),
                        trace=TraceContext(**current_trace_ids()),
                    )
                )
                return result

        output_text = str(output)
        trimmed_output = output_text[: self._settings.tool_result_max_chars]
        result.update(
            {
                "status": status,
                "output": trimmed_output,
            }
        )
        log_event(
            ToolCallEvent(
                name=tool_name,
                args=sanitized_args,
                status=status,
                output_preview=trimmed_output,
                trace=TraceContext(**current_trace_ids()),
            )
        )
        return result

    def _tool_result_entry(self, result: dict[str, Any], *, source: str = "plan") -> dict[str, Any]:
        """Turn a tool result into a structured step entry."""

        entry: dict[str, Any] = {
            "type": "tool",
            "source": source,
            "name": result.get("name"),
            "status": result.get("status"),
        }
        output = result.get("output")
        if output:
            entry["output"] = output
        reason = result.get("reason") or result.get("error")
        if reason:
            entry["reason"] = reason
        return entry

    def _fallback_plan(self, prompt: str) -> Plan:
        return Plan(
            steps=[
                PlanStep(
                    id=str(uuid.uuid4()),
                    label="Retrieve relevant memories",
                    executor="agent",
                    action="memory",
                    args={"query": prompt},
                    description="Default memory lookup before the completion.",
                ),
                PlanStep(
                    id=str(uuid.uuid4()),
                    label="Generate final answer",
                    executor="litellm",
                    action="completion",
                    description="Fallback completion step.",
                ),
            ],
            description="Fallback plan generated when the planner response was invalid.",
        )

    def _describe_tools(self, allowlist: set[str] | None = None) -> list[dict[str, Any]]:
        tool_list = []

        # 1. Registry Tools
        if self._tool_registry:
            for tool in self._tool_registry.list_tools():
                if allowlist is not None and tool.name not in allowlist:
                    continue
                info = {
                    "name": tool.name,
                    "description": getattr(tool, "description", tool.__class__.__name__),
                }
                if hasattr(tool, "parameters"):
                    info["parameters"] = tool.parameters
                elif hasattr(tool, "schema"):
                    info["schema"] = tool.schema
                tool_list.append(info)

        return tool_list

    # ────────────────────────────────────────────────────────────────────────────
    # Helper methods extracted from execute_stream to improve readability
    # ────────────────────────────────────────────────────────────────────────────

    async def _ensure_conversation_exists(
        self,
        session: AsyncSession,
        conversation_id: str,
        request: AgentRequest,
    ) -> Conversation:
        """Ensure a Conversation exists, creating one if needed.

        Args:
            session: Database session
            conversation_id: UUID for the conversation
            request: The incoming agent request

        Returns:
            The existing or newly created Conversation
        """
        db_conversation = await session.get(Conversation, conversation_id)
        if db_conversation:
            return db_conversation

        # Auto-create attached to 'default' context if new
        stmt = select(Context).where(Context.name == "default")
        result = await session.execute(stmt)
        db_context = result.scalar_one_or_none()

        if not db_context:
            # Bootstrap default context
            db_context = await self.context_manager.create_context(
                session, "default", "virtual", {}
            )

        db_conversation = Conversation(
            id=conversation_id,
            platform=(request.metadata or {}).get("platform", "api"),
            platform_id=(request.metadata or {}).get("platform_id", "generic"),
            context_id=db_context.id,
            current_cwd=db_context.default_cwd,
        )
        session.add(db_conversation)
        await session.flush()
        return db_conversation

    async def _get_or_create_session(
        self,
        session: AsyncSession,
        conversation_id: str,
    ) -> Session:
        """Get active session or create a new one.

        Args:
            session: Database session
            conversation_id: UUID for the conversation

        Returns:
            The active Session for this conversation
        """
        session_stmt = select(Session).where(
            Session.conversation_id == conversation_id, Session.active.is_(True)
        )
        session_result = await session.execute(session_stmt)
        db_session = session_result.scalar_one_or_none()

        if not db_session:
            db_session = Session(conversation_id=conversation_id, active=True)
            session.add(db_session)
            await session.flush()

        return db_session

    async def _load_conversation_history(
        self,
        session: AsyncSession,
        db_session: Session,
    ) -> list[AgentMessage]:
        """Load message history for a session.

        Args:
            session: Database session
            db_session: The active Session

        Returns:
            List of AgentMessage objects representing conversation history
        """
        history_stmt = (
            select(Message)
            .where(Message.session_id == db_session.id)
            .order_by(Message.created_at.asc())
        )
        history_result = await session.execute(history_stmt)
        db_messages = history_result.scalars().all()

        history = [AgentMessage(role=msg.role, content=msg.content) for msg in db_messages]

        # Inject current date as system context
        current_date_str = datetime.now().strftime("%Y-%m-%d")
        history.insert(
            0,
            AgentMessage(role="system", content=f"Current Date: {current_date_str}"),
        )

        return history

    def _is_path_safe(self, file_path: str, allowed_bases: list[str]) -> bool:
        """Check if a file path is safe to read (no path traversal).

        SECURITY: Prevents reading sensitive files outside allowed directories.

        Args:
            file_path: The file path to validate
            allowed_bases: List of allowed base directories

        Returns:
            True if path is within an allowed directory, False otherwise
        """
        from pathlib import Path

        try:
            resolved = Path(file_path).resolve()
            for base in allowed_bases:
                base_resolved = Path(base).resolve()
                # Check if the resolved path starts with the allowed base
                if str(resolved).startswith(str(base_resolved) + "/"):
                    return True
                if resolved == base_resolved:
                    return True
            return False
        except Exception:
            LOGGER.debug(
                "Path resolution failed during sandbox check for %s (allowed: %s), "
                "assuming not sandboxed",
                file_path,
                allowed_bases,
                exc_info=True,
            )
            return False

    async def _inject_pinned_files(
        self,
        history: list[AgentMessage],
        pinned_files: list[str] | None,
        workspace_path: str | None = None,
    ) -> None:
        """Inject pinned file contents into the conversation history.

        Args:
            history: The conversation history to modify in-place
            pinned_files: List of file paths to inject
            workspace_path: Optional workspace path for path validation
        """
        if not pinned_files:
            return

        from pathlib import Path

        # SECURITY: Define allowed base directories for pinned files
        allowed_bases: list[str] = []
        if workspace_path:
            allowed_bases.append(workspace_path)
        # Also allow user's home directory as a reasonable default
        home = Path.home()
        if await asyncio.to_thread(home.exists):
            allowed_bases.append(str(home))

        async def _read_pinned_file(pf: str) -> str | None:
            """Read a single pinned file asynchronously."""
            try:
                # SECURITY: Validate path is within allowed directories
                if allowed_bases and not self._is_path_safe(pf, allowed_bases):
                    LOGGER.warning(f"Blocked pinned file outside allowed paths: {pf}")
                    return None

                p = Path(pf)
                if await asyncio.to_thread(p.exists) and await asyncio.to_thread(p.is_file):
                    file_content = await asyncio.to_thread(p.read_text, encoding="utf-8")
                    return f"### FILE: {pf}\n{file_content}"
                return None
            except Exception as e:
                LOGGER.warning(f"Failed to read pinned file {pf}: {e}")
                return None

        # Read all pinned files in parallel
        results = await asyncio.gather(
            *[_read_pinned_file(pf) for pf in pinned_files],
            return_exceptions=True,
        )

        pinned_content: list[str] = []
        for result in results:
            if isinstance(result, BaseException):
                LOGGER.warning(f"Failed to read pinned file: {result}")
            elif result is not None:
                pinned_content.append(result)

        if pinned_content:
            combined_pinned = "\n\n".join(pinned_content)
            history.append(
                AgentMessage(
                    role="system",
                    content=(
                        f"## PINNED FILES (Active Context)\n"
                        f"The following files are pinned to your context:\n\n{combined_pinned}"
                    ),
                )
            )

    async def _inject_workspace_rules(
        self,
        history: list[AgentMessage],
        workspace_path: str,
    ) -> None:
        """Inject workspace rules from .agent/rules.md into the conversation history.

        Args:
            history: The conversation history to modify in-place
            workspace_path: Path to the workspace directory
        """
        from pathlib import Path

        # SECURITY: Validate workspace_path and ensure rules file stays within it
        try:
            workspace_resolved = Path(workspace_path).resolve()
        except Exception:
            LOGGER.warning("Invalid workspace path: %s", workspace_path, exc_info=True)
            return

        rules_path = Path(workspace_path) / ".agent" / "rules.md"

        # SECURITY: Ensure resolved rules_path is within workspace
        try:
            rules_resolved = rules_path.resolve()
            if not str(rules_resolved).startswith(str(workspace_resolved) + "/"):
                LOGGER.warning(f"Blocked rules path traversal: {rules_path}")
                return
        except Exception:
            LOGGER.warning("Failed to validate rules path: %s", rules_path, exc_info=True)
            return

        if not await asyncio.to_thread(rules_path.exists) or not await asyncio.to_thread(
            rules_path.is_file
        ):
            return

        try:
            rules_content = await asyncio.to_thread(rules_path.read_text, encoding="utf-8")
            rules_content = rules_content.strip()
            if not rules_content:
                return

            # Insert at the beginning of history as a system message
            history.insert(
                0,
                AgentMessage(
                    role="system",
                    content=(
                        f"## WORKSPACE RULES\n"
                        f"These rules apply to this workspace and must be followed:\n\n"
                        f"{rules_content}"
                    ),
                ),
            )
            LOGGER.info(f"Injected workspace rules from {rules_path}")
        except Exception as e:
            LOGGER.warning(f"Failed to read workspace rules from {rules_path}: {e}")

    @staticmethod
    def _parse_tool_allowlist(raw: Any) -> set[str] | None:
        if raw is None:
            return None
        if isinstance(raw, list | tuple | set):
            return {str(item) for item in raw if isinstance(item, str)}
        return None

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

    async def _store_pending_hitl(
        self,
        session: AsyncSession,
        db_conversation: Conversation,
        hitl_metadata: dict[str, Any],
    ) -> None:
        """Store HITL state in conversation metadata for resume.

        Args:
            session: Database session
            db_conversation: The conversation to update
            hitl_metadata: HITL metadata including skill_messages, step, etc.
        """
        # Update conversation_metadata with pending_hitl
        current_meta = dict(db_conversation.conversation_metadata or {})
        current_meta["pending_hitl"] = hitl_metadata
        db_conversation.conversation_metadata = current_meta
        await session.flush()
        LOGGER.info(
            "Stored pending HITL for conversation %s: %s",
            db_conversation.id,
            hitl_metadata.get("skill_name"),
        )

    async def _clear_pending_hitl(
        self,
        session: AsyncSession,
        db_conversation: Conversation,
    ) -> None:
        """Clear pending HITL state after resume.

        Args:
            session: Database session
            db_conversation: The conversation to update
        """
        current_meta = dict(db_conversation.conversation_metadata or {})
        if "pending_hitl" in current_meta:
            del current_meta["pending_hitl"]
            db_conversation.conversation_metadata = current_meta
            await session.flush()
            LOGGER.info("Cleared pending HITL for conversation %s", db_conversation.id)

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
        await self._clear_pending_hitl(session, db_conversation)

        # Check for handoff: requirements_drafter confirmation -> requirements_writer
        user_response_lower = request.prompt.lower().strip()
        is_approval = "approve" in user_response_lower or user_response_lower in ("yes", "ja", "ok")

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
                await self._store_pending_hitl(session, db_conversation, meta)
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
        import re

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

            # Extract Team
            team_match = re.search(r"Team:\s*(.+?)(?:\n|$)", content)
            if team_match:
                draft["team_alias"] = team_match.group(1).strip()

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
            if draft.get("title") and draft.get("team_alias"):
                LOGGER.info("Extracted draft: %s", draft)
                return draft

        LOGGER.warning("Could not extract draft from skill messages")
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

    @staticmethod
    def _coerce_tool_call_args(raw_args: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw_args, dict):
            return {}
        if isinstance(raw_args.get("tool_args"), dict):
            return raw_args["tool_args"]
        return {
            key: value
            for key, value in raw_args.items()
            if key not in {"tool_args", "allowed_tools"}
        }

    def _group_steps_for_parallel_execution(
        self,
        steps: list[PlanStep],
    ) -> list[list[PlanStep]]:
        """Group plan steps into batches for parallel execution.

        Steps with no dependencies or satisfied dependencies are grouped together.
        Each batch can be executed in parallel, batches are executed sequentially.

        Args:
            steps: List of plan steps with optional depends_on fields.

        Returns:
            List of step batches. Each batch contains steps that can run in parallel.
        """
        if not steps:
            return []

        batches: list[list[PlanStep]] = []
        completed_ids: set[str] = set()
        remaining = list(steps)

        while remaining:
            # Find all steps whose dependencies are satisfied
            ready: list[PlanStep] = []
            still_pending: list[PlanStep] = []

            for step in remaining:
                deps = step.depends_on or []
                if all(dep in completed_ids for dep in deps):
                    ready.append(step)
                else:
                    still_pending.append(step)

            if not ready:
                # No steps ready but we have remaining steps - this means
                # circular dependency or missing step IDs. Fall back to sequential.
                LOGGER.warning(
                    "Dependency cycle detected or missing step IDs. "
                    "Falling back to sequential execution for remaining %d steps.",
                    len(still_pending),
                )
                # Add all remaining as individual batches
                for step in still_pending:
                    batches.append([step])
                break

            batches.append(ready)
            completed_ids.update(s.id for s in ready)
            remaining = still_pending

        return batches
