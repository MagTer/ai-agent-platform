"""Step executor agent responsible for performing plan steps."""

from __future__ import annotations

import inspect
import time
from typing import Any, Literal, cast

from shared.models import AgentMessage, AgentRequest, PlanStep, StepResult

from core.command_loader import load_command
from core.core.litellm_client import LiteLLMClient
from core.core.memory import MemoryStore
from core.models.pydantic_schemas import StepEvent, ToolCallEvent, TraceContext
from core.observability.logging import log_event
from core.observability.tracing import current_trace_ids, start_span
from core.tools import ToolRegistry


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
            attributes={},
        ) as span:
            span.set_attribute("action", str(step.action))
            span.set_attribute("executor", str(step.executor))
            span.set_attribute("step", str(step.id))

            # Record arguments (sanitize if needed in future)
            if step.args:
                span.set_attribute("step.args", str(step.args))

            result: dict[str, Any] = {}
            try:
                if step.executor == "agent" and step.action == "memory":
                    query = step.args.get("query") or request.prompt
                    limit_value = step.args.get("limit")
                    try:
                        limit = int(limit_value) if limit_value is not None else 5
                    except (TypeError, ValueError):
                        limit = 5
                    records = await self._memory.search(
                        str(query), limit=limit, conversation_id=conversation_id
                    )
                    for record in records:
                        messages.append(
                            AgentMessage(role="system", content=f"Context memory: {record.text}")
                        )
                    result = {"count": len(records)}
                    status = "ok"
                elif step.executor == "agent" and step.action == "tool":
                    result, messages, status = await self._run_tool(step, request)
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
                from core.tools.base import ToolConfirmationError

                if isinstance(exc, ToolConfirmationError):
                    raise exc
                result = {"error": str(exc)}
                status = "error"

            duration_ms = (time.perf_counter() - start_time) * 1000
            span.set_attribute("latency_ms", duration_ms)
            span.set_attribute("status", status)
            if result:
                span.set_attribute("step.result", str(result))
            trace_ctx = TraceContext(**current_trace_ids())

            final_status = cast(
                Literal["ok", "error", "skipped", "in_progress"],
                status if status in {"ok", "error", "skipped"} else "ok",
            )

            log_event(
                StepEvent(
                    step_id=step.id,
                    label=step.label,
                    action=step.action,
                    executor=step.executor,
                    status=final_status,
                    metadata={"duration_ms": duration_ms} | result,
                    trace=trace_ctx,
                )
            )
            return StepResult(step=step, status=status, result=result, messages=messages)

    async def _run_tool(
        self,
        step: PlanStep,
        request: AgentRequest,
    ) -> tuple[dict[str, Any], list[AgentMessage], str]:
        tool_messages: list[AgentMessage] = []

        # 1. Try Native Tool
        tool = self._tool_registry.get(step.tool) if self._tool_registry and step.tool else None

        if not tool:
            # 2. Try Skill (Markdown Command)
            if step.tool:
                try:
                    metadata, rendered_prompt = load_command(step.tool, step.args or {})
                    # Execute Skill via LLM (One-off)
                    with start_span(f"skill.call.{step.tool}"):
                        output_text = await self._litellm.generate(
                            [AgentMessage(role="user", content=rendered_prompt)]
                        )

                    tool_messages.append(
                        AgentMessage(
                            role="system",
                            content=f"Tool {step.tool} output:\n{output_text}",
                        )
                    )
                    # Trace
                    trace_ctx = TraceContext(**current_trace_ids())
                    log_event(
                        ToolCallEvent(
                            name=step.tool,
                            args=step.args,
                            status="ok",
                            output_preview=output_text[:200],
                            trace=trace_ctx,
                        )
                    )
                    return (
                        {"name": step.tool, "status": "ok", "output": output_text},
                        tool_messages,
                        "ok",
                    )

                except FileNotFoundError:
                    pass  # Fall through to return missing
                except Exception as e:
                    return (
                        {
                            "name": step.tool,
                            "status": "error",
                            "reason": f"Skill execution failed: {e}",
                        },
                        tool_messages,
                        "error",
                    )

            return {"name": step.tool, "status": "missing"}, tool_messages, "missing"

        # Simplified argument unpacking: Trust the plan, with minor legacy fallback
        tool_args = step.args
        if (
            "tool_args" in step.args
            and len(step.args) == 1
            and isinstance(step.args["tool_args"], dict)
        ):
            tool_args = step.args["tool_args"]

        allowlist = step.args.get("allowed_tools") if isinstance(step.args, dict) else None
        if allowlist and step.tool not in allowlist:
            # Legacy guard, mostly for 'router' calls but safe to keep
            return (
                {"name": step.tool, "status": "skipped", "reason": "not-allowed"},
                tool_messages,
                "skipped",
            )

        # Inject CWD if provided and tool supports it
        cwd = step.args.get("cwd")  # Explicit args take precedence
        if not cwd and "cwd" in (request.metadata or {}):
            cwd = (request.metadata or {}).get("cwd")

        final_args = tool_args.copy()
        if cwd:
            # Check if tool.run accepts cwd
            sig = inspect.signature(tool.run)
            has_cwd = "cwd" in sig.parameters
            has_kwargs = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
            if has_cwd or has_kwargs:
                final_args["cwd"] = cwd

        with start_span(f"tool.call.{step.tool}"):
            try:
                if tool.requires_confirmation:
                    # Check for confirmation token/flag in args (future proofing)
                    # For now, always raise unless explicit "confirm=True" is in args?
                    # The Prompt/Request metadata might carry confirmation, but we pass args here.
                    # Let's check a reserved arg "_confirmed" or similar?
                    # Or relying on the AgentService to re-call with modification.
                    # Simple approach: If args.get("confirm_dangerous_action") is not True, raise.
                    if not final_args.get("confirm_dangerous_action"):
                        from core.tools.base import ToolConfirmationError

                        raise ToolConfirmationError(tool.name, tool_args=final_args)

                # Remove confirmation flag before calling tool
                run_args = final_args.copy()
                run_args.pop("confirm_dangerous_action", None)

                output = await tool.run(**run_args)
            except TypeError as exc:
                return (
                    {
                        "name": step.tool,
                        "status": "error",
                        "reason": f"Invalid arguments: {exc}",
                    },
                    tool_messages,
                    "error",
                )
        output_text = str(output)

        # Phase 4: Integration Feedback
        # If output is error, append a hint to the system message
        msg_content = f"Tool {step.tool} output:\n{output_text}"
        if output_text.startswith("Error:"):
            msg_content += (
                "\n\nSYSTEM HINT: The last tool call failed. "
                "Analyze the error above. If it is a syntax error, fix the code. "
                "If it is a logic error, adjust your plan args."
            )

        tool_messages.append(AgentMessage(role="system", content=msg_content))
        trace_ctx = TraceContext(**current_trace_ids())
        log_event(
            ToolCallEvent(
                name=step.tool or "unknown",
                args=tool_args,
                status="ok",
                output_preview=output_text[:200],
                trace=trace_ctx,
            )
        )
        return (
            {"name": step.tool, "status": "ok", "output": output_text},
            tool_messages,
            "ok",
        )

    async def _generate_completion(
        self,
        step: PlanStep,
        prompt_history: list[AgentMessage],
        request: AgentRequest,
    ) -> tuple[str, str]:
        settings = getattr(self._litellm, "_settings", None)
        default_model = getattr(settings, "litellm_model", "agent-model")
        model_override = str(step.args.get("model") or default_model)
        with start_span("llm.call.assistant") as span:
            span.set_attribute("model", model_override)
            span.set_attribute("step", str(step.id))
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
