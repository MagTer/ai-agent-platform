"""High level agent orchestration."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.agents import (
    PlannerAgent,
    PlanSupervisorAgent,
    ResponseAgent,
    StepExecutorAgent,
    StepSupervisorAgent,
)
from core.core.config import Settings
from core.core.litellm_client import LiteLLMClient
from core.core.memory import MemoryStore
from core.db import Context, Conversation, Message, Session
from core.models.pydantic_schemas import SupervisorDecision, ToolCallEvent, TraceContext
from core.observability.logging import log_event
from core.observability.tracing import current_trace_ids, start_span
from core.tools import ToolRegistry
from shared.models import (
    AgentMessage,
    AgentRequest,
    AgentResponse,
    Plan,
    PlanStep,
    RoutingDecision,
    StepResult,
)

from .memory import MemoryRecord

LOGGER = logging.getLogger(__name__)


class AgentService:
    """Coordinate the memory, LLM and metadata layers."""

    _settings: Settings
    _litellm: LiteLLMClient
    _memory: MemoryStore
    _tool_registry: ToolRegistry

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

    async def handle_request(self, request: AgentRequest, session: AsyncSession) -> AgentResponse:
        """Process an :class:`AgentRequest` and return an :class:`AgentResponse`."""
        conversation_id = request.conversation_id or str(uuid.uuid4())
        LOGGER.info("Processing prompt for conversation %s", conversation_id)

        # 1. Ensure Conversation and Session exist
        db_conversation = await session.get(Conversation, conversation_id)
        if not db_conversation:
            # Ensure a default context exists (MVP)
            context_stmt = select(Context).where(Context.name == "default")
            context_result = await session.execute(context_stmt)
            db_context = context_result.scalar_one_or_none()
            if not db_context:
                db_context = Context(name="default", type="general", default_cwd="/tmp")  # noqa: S108
                session.add(db_context)
                await session.flush()  # get ID

            db_conversation = Conversation(
                id=conversation_id,
                platform="api",
                platform_id="generic",
                context_id=db_context.id,
                current_cwd=db_context.default_cwd,
            )
            session.add(db_conversation)
            await session.flush()

        # Get active session
        session_stmt = select(Session).where(
            Session.conversation_id == conversation_id, Session.active == True
        )
        session_result = await session.execute(session_stmt)
        db_session = session_result.scalar_one_or_none()

        if not db_session:
            db_session = Session(conversation_id=conversation_id, active=True)
            session.add(db_session)
            await session.flush()

        # 2. Load History
        history_stmt = (
            select(Message)
            .where(Message.session_id == db_session.id)
            .order_by(Message.created_at.asc())
        )
        history_result = await session.execute(history_stmt)
        db_messages = history_result.scalars().all()

        history = [AgentMessage(role=msg.role, content=msg.content) for msg in db_messages]

        # 3. Request Prep
        steps: list[dict[str, Any]] = []
        request_metadata: dict[str, Any] = dict(request.metadata or {})
        planner = PlannerAgent(self._litellm, model_name=self._settings.litellm_model)
        plan_supervisor = PlanSupervisorAgent()
        executor = StepExecutorAgent(self._memory, self._litellm, self._tool_registry)
        step_supervisor = StepSupervisorAgent()
        responder = ResponseAgent()

        # Extract routing decision (default to AGENTIC if missing)
        routing_decision = request_metadata.get("routing_decision", RoutingDecision.AGENTIC)
        LOGGER.info(f"Handling request with routing decision: {routing_decision}")

        with start_span(
            "agent.request",
            attributes={
                "conversation_id": conversation_id,
                "input_size": len(request.prompt),
                "routing_decision": routing_decision,
            },
        ):
            # Record USER message in memory and DB
            user_message = AgentMessage(role="user", content=request.prompt)
            session.add(
                Message(
                    session_id=db_session.id,
                    role="user",
                    content=request.prompt,
                    trace_id=current_trace_ids().get("trace_id"),
                )
            )
            # Fix: Don't commit yet, wait for flow? Or commit incrementally?
            # Ideally transaction commits at end of request scope in FastAPI,
            # but we might want to checkpoint.
            # For now rely on flush for IDs, commit happens at app closure or explicitly if needed.
            # But wait, session is passed from Depends(get_db). FastAPI usually handles commit if no error?
            # Or we must commit. AsyncSession dependency usually yields session.
            # I will flush to be safe for visibility in same transaction.

            # If CHAT:
            if routing_decision == RoutingDecision.CHAT:
                # Direct LLM call
                completion_text = await self._litellm.generate(history + [user_message])

                assistant_message = AgentMessage(role="assistant", content=completion_text)

                # DB Persist
                session.add(
                    Message(
                        session_id=db_session.id,
                        role="assistant",
                        content=completion_text,
                        trace_id=current_trace_ids().get("trace_id"),
                    )
                )

                # Add a 'completion' step trace for visibility
                completion_step: dict[str, Any] = {
                    "type": "completion",
                    "provider": "litellm",
                    "model": self._settings.litellm_model,
                    "status": "ok",
                    "trace": current_trace_ids(),
                }
                steps.append(completion_step)

                await session.commit()  # Save state

                return await responder.finalize(
                    completion=completion_text,
                    conversation_id=conversation_id,
                    messages=history + [user_message, assistant_message],
                    steps=steps,
                    metadata=request_metadata,
                )

            # AGENTIC (or FAST_PATH with injected plan)
            metadata_tool_results = await self._execute_tools(request_metadata)
            all_tool_results = list(metadata_tool_results)
            for result in metadata_tool_results:
                entry = self._tool_result_entry(result, source="metadata")
                entry.update(current_trace_ids())
                steps.append(entry)

            history_with_tools = list(history)
            for result in metadata_tool_results:
                if result.get("status") == "ok" and result.get("output"):
                    msg_content = f"Tool {result['name']} output:\n{result['output']}"
                    history_with_tools.append(AgentMessage(role="system", content=msg_content))
                    # Persist implicit tool outputs from metadata?
                    # Probably yes, as system messages to keep context?
                    # For now, I will NOT persist metadata-injected tool results to DB history
                    # unless we decide they are part of permanent record.
                    # "history" variable implies previous turns.
                    # "history_with_tools" is transient for this turn.
                    # I'll stick to that.

            # Check for Command (Skill) in metadata or Prompt?
            # Implementation Plan says: "Integrate CommandLoader... Check if a requested tool matches a .md skill."
            # The Planner decides tools.
            # If the planner outputs a tool that is a SKILL, the Executor needs to know.
            # But here we are generating the plan.

            if request_metadata.get("plan"):
                LOGGER.info("Using injected plan from metadata")
                plan = Plan(**request_metadata["plan"])
            else:
                allowlist = self._parse_tool_allowlist(request_metadata.get("tools"))

                # Merged Tools: Registry + Skills?
                # "Command System: ... dynamic loading of versioned commands (skills)..."
                # I should probably list available skills and add them to tool_descriptions?
                # For Phase 2, let's keep it simple: Planner sees registry tools.
                # If we want skills to be visible, we need to load them.
                # I'll add a TODO or basic loading.

                tool_descriptions = self._describe_tools(allowlist)

                plan = await planner.generate(
                    request,
                    history=history_with_tools,
                    tool_descriptions=tool_descriptions,
                )
                plan = await plan_supervisor.review(plan)

            if not plan.steps:
                plan = self._fallback_plan(request.prompt)
            request_metadata["plan"] = plan.model_dump()
            steps.append(
                {
                    "type": "plan",
                    "status": "created",
                    "description": plan.description,
                    "plan": plan.model_dump(),
                    **current_trace_ids(),
                }
            )

            prompt_history = list(history_with_tools)
            plan_tool_results: list[dict[str, Any]] = []
            completion_text = ""
            completion_provider = "litellm"
            completion_model = self._settings.litellm_model
            completion_step_id: str | None = None

            # Execute Plan
            for plan_step in plan.steps:
                step_entry: dict[str, Any] = {
                    "type": "plan_step",
                    "id": plan_step.id,
                    "label": plan_step.label,
                    "action": plan_step.action,
                    "executor": plan_step.executor,
                    "tool": plan_step.tool,
                    "status": "in_progress",
                    "trace": current_trace_ids(),
                }
                steps.append(step_entry)

                # Executor Run
                # Does executor support Skills?
                # StepExecutor needs to support "command" action or "tool" action that maps to a skill.
                # If plan_step.action == "tool", check if tool is in registry.
                # If not in registry, check CommandLoader?
                # The current StepExecutor uses `_tool_registry`.
                # I should update StepExecutor to support skills OR handle it here?
                # "Integrate CommandLoader... execute as a one-off LLM call..."
                # Use `CommandLoader.load_command(name, args)`.

                # Intercept tool actions for Skills here?
                # Or inside StepExecutor?
                # Creating a "SkillTool" wrapper is cleanest.
                # But StepExecutor is separate class.
                # For this refactor, I will modify StepExecutor to use CommandLoader.
                # Wait, I cannot modify StepExecutor in this chunk.
                # I will handle it here: If executor.run fails or if I pre-check.
                # Better: Modify StepExecutor later.
                # Current StepExecutor agent imports tool registry.
                # I can inject a "SkillAwareToolRegistry" or just update StepExecutor.
                # I'll stick to standard tools for now, and handle explicit skill integration in next chunk if needed.
                # Update: Implementation Plan said "Integrate CommandLoader... AgentService will merge ToolRegistry and CommandLoader".

                step_execution_result: StepResult = await executor.run(
                    plan_step,
                    request=request,
                    conversation_id=conversation_id,
                    prompt_history=prompt_history,
                )

                decision = await step_supervisor.review(plan_step, step_execution_result.status)
                step_entry.update(
                    status=step_execution_result.status,
                    result=step_execution_result.result,
                    decision=decision,
                    trace=current_trace_ids(),
                )
                prompt_history.extend(step_execution_result.messages)
                if plan_step.action == "tool":
                    plan_tool_results.append(step_execution_result.result)
                    # Persist Tool output to DB?
                    # As 'tool' role message.
                    session.add(
                        Message(
                            session_id=db_session.id,
                            role="tool",  # or system?
                            content=str(step_execution_result.result.get("output", "")),
                            trace_id=current_trace_ids().get("trace_id"),
                        )
                    )

                if plan_step.action == "completion" and step_execution_result.status == "ok":
                    completion_text = step_execution_result.result.get("completion", "")
                    completion_provider = plan_step.provider or completion_provider
                    completion_model = step_execution_result.result.get("model", completion_model)
                    completion_step_id = plan_step.id
                    break

            if not completion_text:
                completion_text = await self._litellm.generate(prompt_history + [user_message])
                completion_step_id = completion_step_id or (
                    plan.steps[-1].id if plan.steps else None
                )

            tool_results = list(all_tool_results) + plan_tool_results
            if tool_results:
                request_metadata["tool_results"] = tool_results

            assistant_message = AgentMessage(role="assistant", content=completion_text)
            final_completion_step_entry: dict[str, Any] = {
                "type": "completion",
                "provider": completion_provider,
                "model": completion_model,
                "status": "ok",
                "plan_step_id": completion_step_id,
                **current_trace_ids(),
            }
            steps.append(final_completion_step_entry)

            # Persist Final Answer
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

            response = await responder.finalize(
                completion=completion_text,
                conversation_id=conversation_id,
                messages=history_with_tools + [user_message, assistant_message],
                steps=steps,
                metadata=request_metadata,
            )

            log_event(
                SupervisorDecision(
                    item_id=conversation_id,
                    decision="ok",
                    comments="Conversation complete",
                    trace=TraceContext(**current_trace_ids()),
                )
            )

            return response

    async def list_models(self) -> Any:
        """Proxy LiteLLM's `/v1/models` response."""

        return await self._litellm.list_models()

    async def _execute_tools(self, metadata: dict[str, Any]) -> list[dict[str, Any]]:
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
            try:
                output = await tool.run(**sanitized_args)
                status = "ok"
            except Exception as exc:  # pragma: no cover - depends on tool implementation
                LOGGER.exception("Tool %s execution failed", tool_name)
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
        if not self._tool_registry:
            return []

        tools = self._tool_registry.tools()
        if allowlist is not None:
            tools = [t for t in tools if t.name in allowlist]

        tool_list: list[dict[str, Any]] = []
        for tool in tools:
            info = {
                "name": tool.name,
                "description": getattr(tool, "description", tool.__class__.__name__),
            }
            # Attempt to grab schema info if available via attributes
            if hasattr(tool, "parameters"):
                info["parameters"] = tool.parameters
            elif hasattr(tool, "schema"):
                info["schema"] = tool.schema

            tool_list.append(info)

        return tool_list

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


__all__ = ["AgentService"]
