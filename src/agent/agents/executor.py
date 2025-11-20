"""Step executor agent responsible for performing plan steps."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from agent.core.litellm_client import LiteLLMClient
from agent.core.memory import MemoryStore
from agent.core.models import AgentMessage, AgentRequest, PlanStep
from agent.models.pydantic_schemas import StepEvent, ToolCallEvent, TraceContext
from agent.observability.logging import log_event
from agent.observability.tracing import current_trace_ids, start_span
from agent.tools import ToolRegistry


@dataclass
class StepResult:
    step: PlanStep
    status: str
    result: dict[str, Any]
    messages: list[AgentMessage]


class StepExecutorAgent:
    """Execute individual steps with observability hooks."""

    def __init__(
        self,
        memory: MemoryStore,
        litellm: LiteLLMClient,
        tool_registry: ToolRegistry | None,
    ) -> None:
        self._memory = memory
        self._litellm = litellm
        self._tool_registry = tool_registry

    async def run(
        self,
        step: PlanStep,
        *,
        request: AgentRequest,
        conversation_id: str,
        prompt_history: list[AgentMessage],
    ) -> StepResult:
        messages: list[AgentMessage] = []
        start_time = time.perf_counter()
        with start_span(
            f"executor.step_run.{step.id}",
            attributes={"action": step.action, "executor": step.executor, "step": step.id},
        ) as span:
            try:
                if step.executor == "agent" and step.action == "memory":
                    query = step.args.get("query") or request.prompt
                    limit_value = step.args.get("limit")
                    try:
                        limit = int(limit_value) if limit_value is not None else 5
                    except (TypeError, ValueError):
                        limit = 5
                    records = self._memory.search(
                        str(query), limit=limit, conversation_id=conversation_id
                    )
                    for record in records:
                        messages.append(
                            AgentMessage(role="system", content=f"Context memory: {record.text}")
                        )
                    result = {"count": len(records)}
                    status = "ok"
                elif step.executor == "agent" and step.action == "tool":
                    result, messages, status = await self._run_tool(step)
                elif step.executor in {"litellm", "remote"} and step.action == "completion":
                    completion, model = await self._generate_completion(
                        step, prompt_history, request
                    )
                    result = {"completion": completion, "model": model}
                    status = "ok"
                else:
                    result = {"reason": "unsupported executor/action"}
                    status = "skipped"
            except Exception as exc:  # pragma: no cover - defensive
                result = {"error": str(exc)}
                status = "error"
            duration_ms = (time.perf_counter() - start_time) * 1000
            span.set_attribute("latency_ms", duration_ms)
            span.set_attribute("status", status)
            trace_ctx = TraceContext(**current_trace_ids())
            log_event(
                StepEvent(
                    step_id=step.id,
                    label=step.label,
                    action=step.action,
                    executor=step.executor,
                    status=status if status in {"ok", "error", "skipped"} else "ok",
                    metadata={"duration_ms": duration_ms} | result,
                    trace=trace_ctx,
                )
            )
            return StepResult(step=step, status=status, result=result, messages=messages)

    async def _run_tool(self, step: PlanStep) -> tuple[dict[str, Any], list[AgentMessage], str]:
        tool_messages: list[AgentMessage] = []
        tool = self._tool_registry.get(step.tool) if self._tool_registry and step.tool else None
        if not tool:
            return {"name": step.tool, "status": "missing"}, tool_messages, "missing"
        raw_args = step.args if isinstance(step.args, dict) else {}
        args = (
            raw_args.get("tool_args") if isinstance(raw_args.get("tool_args"), dict) else raw_args
        )
        allowlist = raw_args.get("allowed_tools") if isinstance(raw_args, dict) else None
        if allowlist and step.tool not in allowlist:
            return (
                {"name": step.tool, "status": "skipped", "reason": "not-allowed"},
                tool_messages,
                "skipped",
            )
        with start_span(f"tool.call.{step.tool}"):
            output = await tool.run(**(args or {}))
        output_text = str(output)
        tool_messages.append(
            AgentMessage(role="system", content=f"Tool {step.tool} output:\n{output_text}")
        )
        trace_ctx = TraceContext(**current_trace_ids())
        log_event(
            ToolCallEvent(
                name=step.tool or "unknown",
                args=args or {},
                status="ok",
                output_preview=output_text[:200],
                trace=trace_ctx,
            )
        )
        return {"name": step.tool, "status": "ok", "output": output_text}, tool_messages, "ok"

    async def _generate_completion(
        self,
        step: PlanStep,
        prompt_history: list[AgentMessage],
        request: AgentRequest,
    ) -> tuple[str, str]:
        settings = getattr(self._litellm, "_settings", None)
        default_model = getattr(settings, "litellm_model", "agent-model")
        model_override = str(step.args.get("model") or default_model)
        with start_span(
            "llm.call.assistant", attributes={"model": model_override, "step": step.id}
        ) as span:
            try:
                completion_text = await self._litellm.generate(
                    prompt_history + [AgentMessage(role="user", content=request.prompt)],
                    model=model_override,
                )
            except TypeError:
                completion_text = await self._litellm.generate(
                    prompt_history + [AgentMessage(role="user", content=request.prompt)]
                )
            span.set_attribute("llm.output.size", len(completion_text))
            return completion_text, model_override


__all__ = ["StepExecutorAgent", "StepResult"]
