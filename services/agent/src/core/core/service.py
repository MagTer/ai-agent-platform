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
    Plan,
    PlanStep,
    RoutingDecision,
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
from core.core.memory import MemoryStore
from core.db import Context, Conversation, Message, Session
from core.models.pydantic_schemas import SupervisorDecision, ToolCallEvent, TraceContext
from core.observability.logging import log_event
from core.observability.tracing import (
    current_trace_ids,
    set_span_attributes,
    set_span_status,
    start_span,
)
from core.system_commands import handle_system_command
from core.tools import SkillDelegateTool, ToolRegistry
from core.tools.base import ToolConfirmationError

from .memory import MemoryRecord

LOGGER = logging.getLogger(__name__)


class AgentService:
    """Coordinate the memory, LLM and metadata layers."""

    _settings: Settings
    _litellm: LiteLLMClient
    _memory: MemoryStore
    _tool_registry: ToolRegistry
    context_manager: ContextManager

    def __init__(
        self,
        settings: Settings,
        litellm: LiteLLMClient,
        memory: MemoryStore,
        tool_registry: ToolRegistry | None = None,
    ):
        self._settings = settings
        self._litellm = litellm
        self._memory = memory
        self._tool_registry = tool_registry or ToolRegistry([])

        # Instantiate and register SkillDelegateTool (requires dependency injection)
        skill_delegate = SkillDelegateTool(self._litellm, self._tool_registry)
        self._tool_registry.register(skill_delegate)

        self.context_manager = ContextManager(settings)

    async def execute_stream(
        self, request: AgentRequest, session: AsyncSession
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute request and yield intermediate events."""
        conversation_id = request.conversation_id or str(uuid.uuid4())
        LOGGER.info("Processing prompt for conversation %s", conversation_id)

        # 1. Ensure Conversation exists (Strict Hierarchy)
        db_conversation = await self._ensure_conversation_exists(session, conversation_id, request)

        # 2. System Command Interceptor
        sys_output = await handle_system_command(request.prompt, self, session, conversation_id)
        if sys_output:
            await session.commit()
            yield {
                "type": "content",
                "content": sys_output,
                "metadata": {"system_command": True},
            }
            return

        # 3. Resolve Active Context
        db_context = await session.get(Context, db_conversation.context_id)
        if not db_context:
            LOGGER.warning("Context missing for conversation %s", conversation_id)
            yield {"type": "error", "content": "Error: Context missing."}
            return

        # 4. Get active session
        db_session = await self._get_or_create_session(session, conversation_id)

        # 5. Load History
        history = await self._load_conversation_history(session, db_session)

        # 6. Request Prep
        request_metadata: dict[str, Any] = dict(request.metadata or {})
        if db_conversation.current_cwd:
            request_metadata["cwd"] = db_conversation.current_cwd

        # 6.1. Inject Pinned Files
        self._inject_pinned_files(history, db_context.pinned_files)

        # 6.2. Inject Workspace Rules (if .agent/rules.md exists)
        if db_conversation.current_cwd:
            self._inject_workspace_rules(history, db_conversation.current_cwd)

        planner = PlannerAgent(self._litellm, model_name=self._settings.model_planner)
        plan_supervisor = PlanSupervisorAgent(
            tool_registry=self._tool_registry,
            skill_names=get_available_skill_names(),
        )
        executor = StepExecutorAgent(self._memory, self._litellm, self._tool_registry)
        step_supervisor = StepSupervisorAgent(
            self._litellm, model_name=self._settings.model_supervisor
        )

        # Adaptive execution settings
        max_replans = 3
        replans_remaining = max_replans

        # Extract routing decision
        routing_decision = request_metadata.get("routing_decision", RoutingDecision.AGENTIC)
        LOGGER.info(f"Handling request with routing decision: {routing_decision}")

        with start_span(
            "agent.request",
            attributes={
                "conversation_id": conversation_id,
                "input_size": len(request.prompt),
                "routing_decision": routing_decision,
                "prompt": request.prompt[:500] if request.prompt else "",
            },
        ):
            try:
                # Record USER message
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

                # If CHAT:
                if routing_decision == RoutingDecision.CHAT:
                    completion_text = await self._litellm.generate(history)
                    session.add(
                        Message(
                            session_id=db_session.id,
                            role="assistant",
                            content=completion_text,
                            trace_id=current_trace_ids().get("trace_id"),
                        )
                    )
                    await session.commit()

                    # Yield completion step
                    yield {
                        "type": "completion",
                        "provider": "litellm",
                        "model": self._settings.model_agentchat,
                        "status": "ok",
                        "trace": current_trace_ids(),
                    }
                    yield {"type": "content", "content": completion_text}
                    return

                # AGENTIC
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

                history_with_tools = list(history)
                for tool_res in metadata_tool_results:
                    if tool_res.get("status") == "ok" and tool_res.get("output"):
                        msg_content = f"Tool {tool_res['name']} output:\n{tool_res['output']}"
                        history_with_tools.append(AgentMessage(role="system", content=msg_content))

                # Prepare tool descriptions for planning
                allowlist = self._parse_tool_allowlist(request_metadata.get("tools"))
                target_tools = allowlist or {
                    t.name
                    for t in self._tool_registry.tools()
                    if getattr(t, "category", "domain") == "orchestration"
                }
                tool_descriptions = self._describe_tools(target_tools)
                available_skills_text = get_registry_index()

                # Initialize variables for adaptive loop
                prompt_history = list(history_with_tools)
                completion_text = ""
                completion_provider = "litellm"
                completion_model = self._settings.model_agentchat
                execution_complete = False

                # ═══════════════════════════════════════════════════════════════════
                # ADAPTIVE EXECUTION LOOP
                # This loop enables re-planning when the supervisor detects issues
                # ═══════════════════════════════════════════════════════════════════
                while replans_remaining >= 0 and not execution_complete:
                    replan_count = max_replans - replans_remaining
                    set_span_attributes({"replan_count": replan_count})

                    # Apply exponential backoff on re-plan attempts
                    # Delays: 0.5s, 1s, 2s for attempts 1, 2, 3
                    if replan_count > 0:
                        backoff_delay = 0.5 * (2 ** (replan_count - 1))
                        LOGGER.info(
                            f"Re-plan backoff: waiting {backoff_delay}s before attempt {replan_count}"
                        )
                        await asyncio.sleep(backoff_delay)

                    # ─────────────────────────────────────────────────────────────
                    # PLANNING PHASE
                    # ─────────────────────────────────────────────────────────────
                    if request_metadata.get("plan") and replan_count == 0:
                        LOGGER.info("Using injected plan from metadata")
                        plan = Plan(**request_metadata["plan"])
                    else:
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
                                "metadata": {
                                    "role": "Planner",
                                    "replan": True,
                                    "attempt": replan_count,
                                },
                            }

                        plan = None
                        async for event in planner.generate_stream(
                            request,
                            history=prompt_history,
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
                        plan = await plan_supervisor.review(plan)
                        assert plan is not None  # Mypy guard
                        if plan is None:  # Runtime guard
                            raise ValueError("Plan became None after review")

                        # Enrich Trace with Plan Details
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

                    if plan is None:
                        # Should be unreachable due to previous checks
                        raise ValueError("Plan is None before step check")
                    if not plan.steps:
                        plan = self._fallback_plan(request.prompt)

                    yield {
                        "type": "plan",
                        "status": "created",
                        "description": plan.description,
                        "plan": plan.model_dump(),
                        "replan_count": replan_count,
                        **current_trace_ids(),
                    }

                    # ─────────────────────────────────────────────────────────────
                    # EXECUTION PHASE
                    # ─────────────────────────────────────────────────────────────
                    needs_replan = False

                    for plan_step in plan.steps:
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
                            },
                        }

                        if plan_step.action == "tool":
                            yield {
                                "type": "tool_start",
                                "content": None,
                                "tool_call": {
                                    "name": plan_step.tool,
                                    "arguments": plan_step.args,
                                },
                                "metadata": {"role": "Executor", "id": plan_step.id},
                            }

                        step_execution_result: StepResult | None = None
                        try:
                            # Stream execution
                            async for event in executor.run_stream(
                                plan_step,
                                request=request,
                                conversation_id=conversation_id,
                                prompt_history=prompt_history,
                            ):
                                if event["type"] == "content":
                                    yield {"type": "content", "content": event["content"]}
                                elif event["type"] == "thinking":
                                    meta = (event.get("metadata") or {}).copy()
                                    meta["id"] = plan_step.id
                                    yield {
                                        "type": "thinking",
                                        "content": event["content"],
                                        "metadata": meta,
                                    }
                                elif event["type"] == "result":
                                    step_execution_result = event["result"]

                                await asyncio.sleep(0)  # Force flush loop

                            if not step_execution_result:
                                LOGGER.error(
                                    "Executor failed to yield result (Stream ended prematurely)"
                                )
                                yield {
                                    "type": "error",
                                    "content": "Step execution ended without result.",
                                }
                                return

                        except ToolConfirmationError as exc:
                            LOGGER.info(f"Step {plan_step.id} paused for confirmation")
                            msg_content = (
                                f"Action paused. Tool '{exc.tool_name}' requires confirmation.\n"
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

                        # ─────────────────────────────────────────────────────────
                        # SUPERVISOR REVIEW (Adaptive Execution)
                        # Skip review for completion steps - they're the final answer
                        # ─────────────────────────────────────────────────────────
                        if plan_step.action == "completion":
                            # Completion steps bypass supervision
                            decision, reason, suggested_fix = (
                                "ok",
                                "Completion step (skipped review)",
                                None,
                            )
                        else:
                            decision, reason, suggested_fix = await step_supervisor.review(
                                plan_step, step_execution_result
                            )

                        if plan_step.action == "tool":
                            chunk_type = "tool_output"
                            tool_call = {"name": plan_step.tool}
                        else:
                            chunk_type = "thinking"
                            tool_call = None

                        # Enriched metadata for legacy compatibility
                        meta = {
                            "status": step_execution_result.status,
                            "decision": decision,
                            "supervisor_reason": reason,
                            "id": plan_step.id,
                            "action": plan_step.action,
                            "tool": plan_step.tool,
                            "name": plan_step.tool,
                            "executor": plan_step.executor,
                            "output": str(step_execution_result.result.get("output") or ""),
                            "source_count": step_execution_result.result.get("source_count", 0),
                        }

                        # Add skill name for consult_expert tool
                        if plan_step.tool == "consult_expert" and plan_step.args:
                            meta["skill"] = plan_step.args.get("skill", "Research")

                        content_str = str(
                            step_execution_result.result.get("output")
                            or step_execution_result.status
                        )

                        # Check for trivial status content
                        is_trivial = False
                        if chunk_type == "thinking" and content_str.lower() in (
                            "ok",
                            "completed step",
                        ):
                            is_trivial = True

                        if not is_trivial:
                            yield {
                                "type": chunk_type,
                                "content": content_str,
                                "tool_call": tool_call,
                                "metadata": meta,
                            }

                        prompt_history.extend(step_execution_result.messages)
                        if plan_step.action == "tool":
                            session.add(
                                Message(
                                    session_id=db_session.id,
                                    role="tool",
                                    content=str(step_execution_result.result.get("output", "")),
                                    trace_id=current_trace_ids().get("trace_id"),
                                )
                            )

                        # ─────────────────────────────────────────────────────────
                        # HANDLE ADJUST DECISION (Trigger Re-plan)
                        # ─────────────────────────────────────────────────────────
                        if decision == "adjust":
                            LOGGER.warning(
                                "Supervisor rejected step '%s': %s", plan_step.label, reason
                            )

                            if replans_remaining > 0:
                                # Inject feedback for re-planning
                                feedback_msg = (
                                    f"Step '{plan_step.label}' failed validation. "
                                    f"Supervisor feedback: {reason}."
                                )
                                if suggested_fix:
                                    feedback_msg += f"\n\nSuggested approach: {suggested_fix}"
                                feedback_msg += (
                                    "\n\nPlease generate a new plan to address this issue."
                                )
                                prompt_history.append(
                                    AgentMessage(role="system", content=feedback_msg)
                                )

                                yield {
                                    "type": "thinking",
                                    "content": f"⚠️ Step rejected: {reason}. Re-planning...",
                                    "metadata": {
                                        "role": "Supervisor",
                                        "supervisor_decision": "adjust",
                                        "reason": reason,
                                        "suggested_fix": suggested_fix,
                                        "replans_remaining": replans_remaining - 1,
                                    },
                                }

                                needs_replan = True
                                replans_remaining -= 1
                                break  # Exit step loop to trigger re-plan
                            else:
                                # Max replans reached, continue with warning
                                LOGGER.error(
                                    "Max replans (%d) reached. Continuing despite rejection.",
                                    max_replans,
                                )
                                yield {
                                    "type": "thinking",
                                    "content": (
                                        f"⚠️ Step issue: {reason}. "
                                        f"Max re-plans ({max_replans}) reached. Continuing..."
                                    ),
                                    "metadata": {"role": "Supervisor", "max_replans_reached": True},
                                }

                        # Check for completion step
                        if (
                            plan_step.action == "completion"
                            and step_execution_result.status == "ok"
                        ):
                            completion_text = step_execution_result.result.get("completion", "")
                            completion_provider = plan_step.provider or completion_provider
                            completion_model = step_execution_result.result.get(
                                "model", completion_model
                            )
                            execution_complete = True
                            break

                    # If no replan needed and loop completed normally
                    if not needs_replan:
                        execution_complete = True

                # ═══════════════════════════════════════════════════════════════════
                # END ADAPTIVE EXECUTION LOOP
                # ═══════════════════════════════════════════════════════════════════

                if not completion_text:
                    yield {
                        "type": "thinking",
                        "content": "Generating final answer...",
                        "metadata": {"role": "Executor"},
                    }
                    completion_text = await self._litellm.generate(prompt_history)
                    # Only yield content if we just generated it here
                    yield {
                        "type": "content",
                        "content": completion_text,
                        "metadata": {
                            "provider": completion_provider,
                            "model": completion_model,
                        },
                    }

                session.add(
                    Message(
                        session_id=db_session.id,
                        role="assistant",
                        content=completion_text,
                        trace_id=current_trace_ids().get("trace_id"),
                    )
                )
                await self._memory.add_records(
                    [MemoryRecord(conversation_id=conversation_id, text=request.prompt)]
                )
                await session.commit()

                log_event(
                    SupervisorDecision(
                        item_id=conversation_id,
                        decision="ok",
                        comments="Conversation complete",
                        trace=TraceContext(**current_trace_ids()),
                    )
                )

                # Yield updated history for legacy compatibility
                # Convert internal AgentMessage objects to dict or keep as is?
                # get_history returns AgentMessage objects.
                # We have 'prompt_history' + 'assistant_msg'.
                # Reconstruct full history:
                final_history = list(prompt_history)
                final_history.append(AgentMessage(role="assistant", content=completion_text))
                # Add records was done in DB.
                yield {"type": "history_snapshot", "messages": final_history}

            except Exception as e:
                set_span_status("ERROR", str(e))
                raise e

    async def handle_request(self, request: AgentRequest, session: AsyncSession) -> AgentResponse:
        """Process an :class:`AgentRequest` and return an :class:`AgentResponse`."""
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

        return AgentResponse(
            response=response_text,
            conversation_id=conversation_id,
            steps=steps,
            metadata=response_metadata,
            messages=messages,
        )

    async def list_models(self) -> Any:
        """Proxy LiteLLM's `/v1/models` response."""

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
        """Retrieve the conversation history from the database."""
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
            for tool in self._tool_registry.tools():
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

    def _inject_pinned_files(
        self,
        history: list[AgentMessage],
        pinned_files: list[str] | None,
    ) -> None:
        """Inject pinned file contents into the conversation history.

        Args:
            history: The conversation history to modify in-place
            pinned_files: List of file paths to inject
        """
        if not pinned_files:
            return

        from pathlib import Path

        pinned_content = []
        for pf in pinned_files:
            try:
                p = Path(pf)
                if p.exists() and p.is_file():
                    pinned_content.append(f"### FILE: {pf}\n{p.read_text(encoding='utf-8')}")
            except Exception as e:
                LOGGER.warning(f"Failed to read pinned file {pf}: {e}")

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

    def _inject_workspace_rules(
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

        rules_path = Path(workspace_path) / ".agent" / "rules.md"
        if not rules_path.exists() or not rules_path.is_file():
            return

        try:
            rules_content = rules_path.read_text(encoding="utf-8").strip()
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
