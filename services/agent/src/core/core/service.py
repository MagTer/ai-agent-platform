"""High level agent orchestration."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

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
from core.core.state import StateStore
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
    _state_store: StateStore
    _tool_registry: ToolRegistry

    async def handle_request(self, request: AgentRequest) -> AgentResponse:
        """Process an :class:`AgentRequest` and return an :class:`AgentResponse`."""
        conversation_id = request.conversation_id or str(uuid.uuid4())
        LOGGER.info("Processing prompt for conversation %s", conversation_id)

        history = (
            list(request.messages)
            if request.messages
            else self._state_store.get_messages(conversation_id)
        )
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
            # FAST_PATH is handled via injected plan in metadata
            # (existing logic covers this if we keep it)
            # But Tri-State logic says:
            # if FAST_PATH -> Execute pre-calculated plan.
            # if CHAT -> Chat only.
            # if AGENTIC -> Plan & Execute.

            # If FAST_PATH, the adapter should have injected the plan.
            # So `if request_metadata.get("plan")` check is still valid for FAST_PATH.

            # If CHAT:
            if routing_decision == RoutingDecision.CHAT:
                user_message = AgentMessage(role="user", content=request.prompt)
                # Simple RAG or just Chat? "CHAT (General conversation, knowledge questions)"
                # Usually implies we might still want memory, but no tools.
                # Instructions say: "Call self.response_agent.reply(history) directly.
                # Do NOT create a plan."
                # But ResponseAgent.reply might not exist or do what we want (LLM generation).
                # `responder.finalize` formats the response object.
                # We need to generate the text first.

                completion_text = await self._litellm.generate(history + [user_message])

                assistant_message = AgentMessage(role="assistant", content=completion_text)
                self._state_store.append_messages(
                    conversation_id,
                    [user_message, assistant_message],
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

                return await responder.finalize(
                    completion=completion_text,
                    conversation_id=conversation_id,
                    messages=history + [user_message, assistant_message],
                    steps=steps,
                    metadata=request_metadata,
                )

            # AGENTIC (or FAST_PATH with injected plan)
            # ... existing logic ...
            metadata_tool_results = await self._execute_tools(request_metadata)
            all_tool_results = list(metadata_tool_results)
            for result in metadata_tool_results:
                entry = self._tool_result_entry(result, source="metadata")
                entry.update(current_trace_ids())
                steps.append(entry)

            history_with_tools = list(history)
            for result in metadata_tool_results:
                if result.get("status") == "ok" and result.get("output"):
                    history_with_tools.append(
                        AgentMessage(
                            role="system",
                            content=f"Tool {result['name']} output:\n{result['output']}",
                        )
                    )

            if request_metadata.get("plan"):
                LOGGER.info("Using injected plan from metadata")
                plan = Plan(**request_metadata["plan"])
            else:
                tool_descriptions = self._describe_tools()
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
            user_message = AgentMessage(role="user", content=request.prompt)

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
                result: StepResult = await executor.run(
                    plan_step,
                    request=request,
                    conversation_id=conversation_id,
                    prompt_history=prompt_history,
                )
                decision = await step_supervisor.review(plan_step, result.status)
                step_entry.update(
                    status=result.status,
                    result=result.result,
                    decision=decision,
                    trace=current_trace_ids(),
                )
                prompt_history.extend(result.messages)
                if plan_step.action == "tool":
                    plan_tool_results.append(result.result)
                if plan_step.action == "completion" and result.status == "ok":
                    completion_text = result.result.get("completion", "")
                    completion_provider = plan_step.provider or completion_provider
                    completion_model = result.result.get("model", completion_model)
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
            completion_step: dict[str, Any] = {
                "type": "completion",
                "provider": completion_provider,
                "model": completion_model,
                "status": "ok",
                "plan_step_id": completion_step_id,
                **current_trace_ids(),
            }
            steps.append(completion_step)

            await asyncio.to_thread(
                self._memory.add_records,
                [MemoryRecord(conversation_id=conversation_id, text=request.prompt)],
            )
            self._state_store.append_messages(
                conversation_id,
                [user_message, assistant_message],
            )

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

    def conversation_history(self, conversation_id: str, limit: int = 20) -> list[AgentMessage]:
        """Return the stored conversation history."""

        return self._state_store.get_messages(conversation_id, limit=limit)

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

    def _describe_tools(self) -> list[dict[str, str]]:
        if not self._tool_registry:
            return []
        return [
            {
                "name": tool.name,
                "description": getattr(tool, "description", tool.__class__.__name__),
            }
            for tool in self._tool_registry.tools()
        ]

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
