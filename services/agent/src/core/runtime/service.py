from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncGenerator
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
from sqlalchemy.ext.asyncio import AsyncSession

from core.agents import (
    PlannerAgent,
    PlanSupervisorAgent,
    StepExecutorAgent,
    StepSupervisorAgent,
)
from core.command_loader import get_available_skill_names, get_registry_index
from core.context_manager import ContextManager
from core.db import Context, Conversation, Message, Session
from core.db.engine import AsyncSessionLocal
from core.models.pydantic_schemas import SupervisorDecision, TraceContext
from core.observability.debug_logger import DebugLogger
from core.observability.logging import log_event
from core.observability.tracing import (
    current_trace_ids,
    set_span_attributes,
    set_span_status,
    start_span,
)
from core.runtime.config import Settings
from core.runtime.context_injector import ContextInjector
from core.runtime.hitl import HITLCoordinator
from core.runtime.litellm_client import LiteLLMClient
from core.runtime.memory import MemoryRecord, MemoryStore
from core.runtime.persistence import ConversationPersistence
from core.runtime.tool_runner import ToolRunner
from core.skills import SkillExecutor, SkillRegistryProtocol
from core.system_commands import handle_system_command
from core.tools import ToolRegistry
from core.tools.base import ToolConfirmationError

LOGGER = logging.getLogger(__name__)

# Cap prompt history to prevent unbounded context growth
MAX_PROMPT_HISTORY_MESSAGES = 50


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
    _skill_registry: SkillRegistryProtocol | None
    context_manager: ContextManager

    # Extracted modules
    _persistence: ConversationPersistence
    _context_injector: ContextInjector
    _tool_runner: ToolRunner
    _hitl_coordinator: HITLCoordinator

    def __init__(
        self,
        settings: Settings,
        litellm: LiteLLMClient,
        memory: MemoryStore,
        tool_registry: ToolRegistry | None = None,
        skill_registry: SkillRegistryProtocol | None = None,
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

        # Initialize extracted modules
        self._persistence = ConversationPersistence(
            context_manager=self.context_manager,
            memory=memory,
        )
        self._context_injector = ContextInjector()
        self._tool_runner = ToolRunner(
            tool_registry=self._tool_registry,
            settings=settings,
        )
        self._hitl_coordinator = HITLCoordinator(
            skill_registry=skill_registry,
            tool_registry=self._tool_registry,
            litellm=litellm,
        )

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
        request_metadata = request.metadata or {}
        db_conversation = await self._persistence._ensure_conversation_exists(
            session, conversation_id, request_metadata
        )

        # Resolve active context
        db_context = await session.get(Context, db_conversation.context_id)

        # Create session for this request
        db_session = await self._persistence._get_or_create_session(session, conversation_id)

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
            history = await self._persistence._load_conversation_history(session, db_session)
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
            await self._context_injector._inject_pinned_files(history, db_context.pinned_files)

        # Inject workspace rules
        if db_conversation.current_cwd:
            await self._context_injector._inject_workspace_rules(
                history, db_conversation.current_cwd
            )

        # Cap history to prevent unbounded context growth
        if len(history) > MAX_PROMPT_HISTORY_MESSAGES:
            # Preserve system messages at the start, trim older user/assistant messages
            system_msgs = [m for m in history if m.role == "system"]
            non_system = [m for m in history if m.role != "system"]
            keep_count = MAX_PROMPT_HISTORY_MESSAGES - len(system_msgs)
            history = system_msgs + non_system[-keep_count:]
            LOGGER.info(
                "Capped prompt history from %d to %d messages",
                len(system_msgs) + len(non_system),
                len(history),
            )

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
                skill_name=plan_step.tool if plan_step.executor == "skill" else None,
            )

            # Record OTel metrics
            if plan_step.executor == "skill":
                from core.observability.metrics import record_skill_step

                record_skill_step(
                    skill_name=plan_step.tool or "unknown",
                    outcome=outcome.value,
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
        work_item_detected = self._hitl_coordinator._detect_work_items(prompt_history)

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
        metadata_tool_results = await self._tool_runner._execute_tools(request_metadata)
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
        allowlist = self._tool_runner._parse_tool_allowlist(request_metadata.get("tools"))
        target_tools = allowlist or {
            t.name
            for t in self._tool_registry.list_tools()
            if getattr(t, "category", "domain") == "orchestration"
        }
        tool_descriptions = self._tool_runner._describe_tools(target_tools)
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

        # Accumulate step outcomes for post-mortem analysis
        # Each entry: (skill_name, outcome, reason)
        # Only skill steps are recorded (tool/completion steps are ignored)
        step_outcome_history: list[tuple[str, str, str]] = []

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
                        await self._persistence._store_pending_hitl(session, db_conversation, meta)
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

                # Record skill step outcome for post-mortem
                _is_skill_step = plan_step.executor == "skill" or plan_step.action == "skill"
                if _is_skill_step and plan_step.tool:
                    step_outcome_history.append(
                        (
                            plan_step.tool,
                            step_outcome or "unknown",
                            replan_reason or "",
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

        # Post-mortem: accumulate skill failure weights in background
        # Only for agentic plans with skill steps that had non-success outcomes
        if step_outcome_history:
            plan_succeeded = execution_complete and not abort_execution
            _context_id: uuid.UUID | None = None
            if db_conversation and db_conversation.context_id:
                _context_id = db_conversation.context_id

            if _context_id is not None:
                asyncio.create_task(
                    _request_post_mortem(
                        context_id=_context_id,
                        trace_id=trace_id,
                        plan_succeeded=plan_succeeded,
                        step_outcome_history=step_outcome_history,
                        litellm=self._litellm,
                        skill_registry=self._skill_registry,
                    )
                )

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
        async for event in self._persistence._finalize_and_persist(
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
                async with asyncio.timeout(self._settings.agent_execution_timeout):
                    # Phase 1: Setup conversation and context
                    (
                        db_conversation,
                        db_context,
                        db_session,
                    ) = await self._setup_conversation_and_context(
                        session, conversation_id, request
                    )

                    # Enrich root span with context_id for per-tenant trace filtering
                    if db_context:
                        set_span_attributes(
                            {
                                "context_id": str(db_context.id),
                                "context_name": db_context.name or "",
                            }
                        )
                    # Enrich root span with DB-resolved conversation_id
                    if db_conversation:
                        set_span_attributes({"conversation_id": str(db_conversation.id)})

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

                        async for event in self._hitl_coordinator._resume_hitl(
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

            except TimeoutError:
                LOGGER.error(
                    "Agent execution timed out after %ss for conversation %s",
                    self._settings.agent_execution_timeout,
                    conversation_id,
                )
                set_span_status("ERROR", "Agent execution timed out")
                set_span_attributes({"error.type": "TimeoutError"})
                yield {
                    "type": "error",
                    "content": "The request timed out. Please try again with a simpler query.",
                }
            except Exception as e:
                from core.observability.error_codes import classify_exception

                error_code = classify_exception(e)
                set_span_status("ERROR", str(e))
                set_span_attributes({"error_code": error_code.value})
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
                messages = await self._persistence.get_history(conversation_id, session)

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
        return await self._persistence.get_history(conversation_id, session)

    # Tool execution - delegated to ToolRunner
    async def _execute_tools(self, metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Execute requested tools and return a structured result list. Delegates to ToolRunner."""
        return await self._tool_runner._execute_tools(metadata)

    async def _run_tool_call(
        self,
        tool_name: str,
        call_args: dict[str, Any],
        *,
        allowlist: set[str] | None = None,
    ) -> dict[str, Any]:
        """Run a single tool invocation. Delegates to ToolRunner."""
        return await self._tool_runner._run_tool_call(tool_name, call_args, allowlist=allowlist)

    def _tool_result_entry(self, result: dict[str, Any], *, source: str = "plan") -> dict[str, Any]:
        """Turn a tool result into a structured step entry. Delegates to ToolRunner."""
        return self._tool_runner._tool_result_entry(result, source=source)

    @staticmethod
    def _parse_tool_allowlist(raw: Any) -> set[str] | None:
        """Parse tool allowlist from metadata. Delegates to ToolRunner."""
        return ToolRunner._parse_tool_allowlist(raw)

    def _describe_tools(self, allowlist: set[str] | None = None) -> list[dict[str, Any]]:
        """Generate tool descriptions for LLM. Delegates to ToolRunner."""
        return self._tool_runner._describe_tools(allowlist)

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


# --- Post-mortem skill quality analysis ---

# Weight constants for failure severity
_WEIGHT_ABORT_DIRECT = 1.0  # Skill was the final failing step (ABORT)
_WEIGHT_REPLAN_ABORT = 0.5  # Skill caused REPLAN, plan ultimately aborted
_WEIGHT_REPLAN_SUCCESS = 0.1  # Skill caused REPLAN, plan ultimately succeeded (self-corrected)

# Accumulated weight threshold to trigger SkillQualityAnalyser
_ANALYSIS_THRESHOLD = 3.0

# Maximum number of failure signal entries to keep per skill (prevents unbounded growth)
_MAX_SIGNALS_PER_SKILL = 50


async def _request_post_mortem(
    context_id: uuid.UUID,
    trace_id: str,
    plan_succeeded: bool,
    step_outcome_history: list[tuple[str, str, str]],
    litellm: LiteLLMClient,
    skill_registry: SkillRegistryProtocol | None,
) -> None:
    """Background task: assign failure weights and trigger analysis if threshold crossed.

    Opens its own DB session because the request session is closed after the response.

    Args:
        context_id: Context UUID.
        trace_id: Trace ID for this request (for failure signal records).
        plan_succeeded: Whether the plan completed successfully (no abort).
        step_outcome_history: List of (skill_name, outcome, reason) tuples
            collected during plan execution. Only skill steps are included.
        litellm: LiteLLM client (for triggering analyser).
        skill_registry: Skill registry (for triggering analyser).
    """
    try:
        async with AsyncSessionLocal() as session:
            skills_to_analyse = await _accumulate_weights(
                session=session,
                context_id=context_id,
                trace_id=trace_id,
                plan_succeeded=plan_succeeded,
                step_outcome_history=step_outcome_history,
            )
            await session.commit()

            # Trigger analysis for any skills that crossed the threshold
            if skills_to_analyse:
                from core.runtime.skill_quality import SkillQualityAnalyser

                analyser = SkillQualityAnalyser(
                    litellm=litellm,
                    skill_registry=skill_registry,
                )
                for skill_name, signals in skills_to_analyse:
                    try:
                        LOGGER.info(
                            "Triggering skill quality analysis for '%s' (context %s) "
                            "-- weight threshold crossed",
                            skill_name,
                            context_id,
                        )
                        await analyser.analyse_single_skill(
                            context_id=context_id,
                            skill_name=skill_name,
                            failure_signals=signals,
                            session=session,
                        )
                        await session.commit()
                    except Exception:
                        LOGGER.exception("Post-mortem analysis failed for skill '%s'", skill_name)
                        await session.rollback()

    except Exception:
        LOGGER.exception("Post-mortem hook failed for context %s", context_id)


async def _accumulate_weights(
    session: AsyncSession,
    context_id: uuid.UUID,
    trace_id: str,
    plan_succeeded: bool,
    step_outcome_history: list[tuple[str, str, str]],
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Accumulate failure weights and return skills that crossed the threshold.

    For each skill step with a non-success outcome, compute a weight based on
    the step outcome and the overall plan outcome, then add it to the
    skill_failure_weights table.

    Args:
        session: Database session (caller manages commit).
        context_id: Context UUID.
        trace_id: Trace ID for signal attribution.
        plan_succeeded: Whether the overall plan succeeded.
        step_outcome_history: List of (skill_name, outcome, reason) tuples.

    Returns:
        List of (skill_name, failure_signals) for skills that crossed the
        analysis threshold. The failure_signals are the accumulated signals
        that were stored in the DB row before it was reset.
    """
    from sqlalchemy import select

    from core.db.models import SkillFailureWeight

    # Compute per-skill weight deltas from this plan execution
    skill_deltas: dict[str, list[tuple[float, str]]] = {}
    for skill_name, outcome, reason in step_outcome_history:
        if outcome == "success":
            continue

        if outcome == "abort":
            weight = _WEIGHT_ABORT_DIRECT
        elif outcome == "replan" and not plan_succeeded:
            weight = _WEIGHT_REPLAN_ABORT
        elif outcome == "replan" and plan_succeeded:
            weight = _WEIGHT_REPLAN_SUCCESS
        else:
            # retry or unknown -- treat as low-weight signal
            weight = _WEIGHT_REPLAN_SUCCESS

        skill_deltas.setdefault(skill_name, []).append((weight, reason))

    if not skill_deltas:
        return []

    triggered: list[tuple[str, list[dict[str, Any]]]] = []

    for skill_name, deltas in skill_deltas.items():
        total_delta = sum(w for w, _ in deltas)

        # Upsert: fetch existing row or create new one
        stmt = select(SkillFailureWeight).where(
            SkillFailureWeight.context_id == context_id,
            SkillFailureWeight.skill_name == skill_name,
        )
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()

        new_signals = [
            {
                "trace_id": trace_id,
                "reason": reason[:200],
                "outcome": "abort" if w >= _WEIGHT_ABORT_DIRECT else "replan",
                "weight": w,
            }
            for w, reason in deltas
            if reason  # Only record signals that have a reason string
        ]

        if row is None:
            row = SkillFailureWeight(
                context_id=context_id,
                skill_name=skill_name,
                accumulated_weight=total_delta,
                failure_signals=new_signals[-_MAX_SIGNALS_PER_SKILL:],
            )
            session.add(row)
        else:
            row.accumulated_weight += total_delta
            # Append signals, keeping bounded
            existing = row.failure_signals or []
            combined = existing + new_signals
            row.failure_signals = combined[-_MAX_SIGNALS_PER_SKILL:]

        # Flush to ensure row.accumulated_weight is current
        await session.flush()

        # Check threshold
        if row.accumulated_weight >= _ANALYSIS_THRESHOLD:
            # Collect signals before reset
            triggered.append((skill_name, list(row.failure_signals or [])))
            # Reset weight and signals
            row.accumulated_weight = 0.0
            row.failure_signals = []

    return triggered
