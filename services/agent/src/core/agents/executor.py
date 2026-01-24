"""Step executor agent responsible for performing plan steps."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any, Literal, cast

from shared.models import AgentMessage, AgentRequest, PlanStep, StepResult

from core.command_loader import load_command
from core.core.litellm_client import LiteLLMClient
from core.core.memory import MemoryStore
from core.models.pydantic_schemas import StepEvent, ToolCallEvent, TraceContext
from core.observability.logging import log_event
from core.observability.tracing import current_trace_ids, set_span_status, start_span
from core.tools import ToolRegistry

LOGGER = logging.getLogger(__name__)

# Default timeout for tool execution (2 minutes)
TOOL_TIMEOUT_SECONDS = 120


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
        """Run a step and return the final result (compatibility wrapper)."""
        async for event in self.run_stream(
            step,
            request=request,
            conversation_id=conversation_id,
            prompt_history=prompt_history,
        ):
            if event["type"] == "result":
                step_res = event["result"]
                # Extract fields from the StepResult object
                return step_res

        # Fallback if stream yields nothing (should not happen)
        return StepResult(step=step, status="error", result={"error": "No result"}, messages=[])

    async def run_stream(
        self,
        step: PlanStep,
        *,
        request: AgentRequest,
        conversation_id: str,
        prompt_history: list[AgentMessage],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute a step and yield incremental updates (content tokens, etc)."""
        start_time = time.perf_counter()

        span_name = f"Step: {step.label}" if step.label else f"Step: {step.id}"
        with start_span(
            span_name,
            attributes={},
        ) as span:
            span.set_attribute("action", str(step.action))
            span.set_attribute("executor", str(step.executor))
            span.set_attribute("step", str(step.id))

            # Always capture args for context
            if step.args:
                span.set_attribute("step.args", str(step.args))

            status = "skipped"
            result: dict[str, Any] = {}

            try:
                # Dispatch based on executor/action
                if step.executor == "agent" and step.action == "memory":
                    async for event in self._execute_memory_step(step, request, conversation_id):
                        yield event
                        if event["type"] == "result":
                            status = event["result"].status
                            result = event["result"].result

                elif step.executor == "agent" and step.action == "tool":
                    async for event in self._execute_tool_step(step, request):
                        yield event
                        if event["type"] == "result":
                            status = event["result"].status
                            result = event["result"].result

                elif step.executor in {"litellm", "remote"} and step.action == "completion":
                    async for event in self._execute_completion_step(step, request, prompt_history):
                        yield event
                        if event["type"] == "result":
                            status = event["result"].status
                            result = event["result"].result

                else:
                    result = {"reason": "unsupported executor/action"}
                    status = "skipped"
                    step_res = StepResult(step=step, status=status, result=result, messages=[])
                    yield {"type": "result", "result": step_res}

            except Exception as exc:
                from core.tools.base import ToolConfirmationError

                if isinstance(exc, ToolConfirmationError):
                    raise exc
                result = {"error": str(exc)}
                status = "error"
                step_res = StepResult(step=step, status=status, result=result, messages=[])
                yield {"type": "result", "result": step_res}

            # Final observability (Logs/Tracing)
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

    async def _run_tool_gen(
        self,
        step: PlanStep,
        request: AgentRequest,
    ) -> AsyncGenerator[dict[str, Any], None]:
        LOGGER.info(f"Starting _run_tool_gen for step {step.id} tool={step.tool}")

        # Wrapped in try/except to catch any unexpected crashes
        try:
            tool_messages: list[AgentMessage] = []

            # 1. Try Native Tool
            tool = self._tool_registry.get(step.tool) if self._tool_registry and step.tool else None

            if not tool:
                # 2. Try Skill (Markdown Command)
                if step.tool:
                    try:
                        metadata, rendered_prompt = load_command(step.tool, step.args or {})

                        # --- DYNAMIC ROUTING ---
                        # Use the model defined in skill, or default to 'skillsrunner-complex'
                        target_model = metadata.get("model", "skillsrunner-complex")

                        if target_model != "skillsrunner-complex":
                            LOGGER.info(
                                f"Routing skill '{step.tool}' to specialized model: {target_model}"
                            )
                        # -----------------------

                        # Execute Skill via LLM (Streaming)
                        # The span name is still the same, but we might want to
                        # attribute the used model
                        with start_span(f"skill.call.{step.tool}"):
                            full_content = []
                            skill_msg = [AgentMessage(role="user", content=rendered_prompt)]

                            LOGGER.info(f"Stream Chatting for skill {step.tool}...")

                            # Pass the target_model explicitly
                            async for chunk in self._litellm.stream_chat(
                                skill_msg, model=target_model
                            ):
                                if chunk["type"] == "content" and chunk["content"]:
                                    content = chunk["content"]
                                    full_content.append(content)
                                    # Stream skill output as regular content
                                    yield {
                                        "type": "content",
                                        "content": content,
                                    }
                                    # Fix for Ketchup Effect: Force flush
                                    await asyncio.sleep(0)
                                else:
                                    # Log debug info about non-content chunks
                                    if chunk.get("type") != "done":
                                        LOGGER.debug(f"Skill chunk: {chunk.get('type')}")

                            output_text = "".join(full_content)
                            LOGGER.info(f"Skill {step.tool} done. Len: {len(output_text)}")

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
                        yield {
                            "type": "final",
                            "data": (
                                {
                                    "name": step.tool,
                                    "status": "ok",
                                    "output": output_text,
                                },
                                tool_messages,
                                "ok",
                            ),
                        }
                        return

                    except FileNotFoundError:
                        # Fall through to return missing if not found
                        pass
                    except Exception as e:
                        LOGGER.error(f"Skill execution exception: {e}", exc_info=True)
                        yield {
                            "type": "final",
                            "data": (
                                {
                                    "name": step.tool,
                                    "status": "error",
                                    "reason": f"Skill execution failed: {e}",
                                },
                                tool_messages,
                                "error",
                            ),
                        }
                        return

                # If no tool found or FileNotFoundError passed through
                yield {
                    "type": "final",
                    "data": (
                        {"name": step.tool, "status": "missing"},
                        tool_messages,
                        "missing",
                    ),
                }
                return

            # Native Tool Execution
            # Validates step.args is not None
            safe_args = step.args or {}  # Fix: Ensure dict for allowlist and CWD checks

            # Simplified argument unpacking: Trust the plan, with minor legacy fallback
            tool_args = safe_args
            if (
                "tool_args" in safe_args
                and len(safe_args) == 1
                and isinstance(safe_args.get("tool_args"), dict)
            ):
                tool_args = safe_args["tool_args"]

            if tool_args is None:
                tool_args = {}

            allowlist = safe_args.get("allowed_tools")
            if allowlist and step.tool not in allowlist:
                # Legacy guard
                yield {
                    "type": "final",
                    "data": (
                        {
                            "name": step.tool,
                            "status": "skipped",
                            "reason": "not-allowed",
                        },
                        tool_messages,
                        "skipped",
                    ),
                }
                return

            # Inject CWD if provided and tool supports it
            cwd = safe_args.get("cwd")  # Fix: Use safe_args
            if not cwd and "cwd" in (request.metadata or {}):
                cwd = (request.metadata or {}).get("cwd")

            # Defensive copy
            try:
                final_args = tool_args.copy()
            except AttributeError:
                # Fallback if somehow tool_args is still None or weird
                LOGGER.warning(f"tool_args was {type(tool_args)} instead of dict. Resetting.")
                final_args = {}

            if cwd:
                # Check if tool.run accepts cwd
                sig = inspect.signature(tool.run)
                has_cwd = "cwd" in sig.parameters
                has_kwargs = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
                if has_cwd or has_kwargs:
                    final_args["cwd"] = cwd

            # Inject user_email for send_email tool (from request metadata)
            if step.tool == "send_email":
                user_email = (request.metadata or {}).get("user_email")
                if user_email:
                    sig = inspect.signature(tool.run)
                    has_user_email = "user_email" in sig.parameters
                    has_kwargs = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
                    if has_user_email or has_kwargs:
                        final_args["user_email"] = user_email

            # Inject user_id and session for tools that need credential lookup
            # This includes direct tools (azure_devops) and orchestration tools (consult_expert)
            # that forward credentials to sub-tools
            if step.tool in ("azure_devops", "consult_expert"):
                user_id_str = (request.metadata or {}).get("user_id")
                db_session = (request.metadata or {}).get("_db_session")
                if user_id_str and db_session:
                    from uuid import UUID

                    sig = inspect.signature(tool.run)
                    has_user_id = "user_id" in sig.parameters
                    has_session = "session" in sig.parameters
                    has_kwargs = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
                    if has_user_id or has_kwargs:
                        final_args["user_id"] = UUID(user_id_str)
                    if has_session or has_kwargs:
                        final_args["session"] = db_session

            # Inject context_id for tools that need OAuth token lookup
            # This includes direct tools (homey) and orchestration tools (consult_expert)
            # that forward context_id to sub-tools
            if step.tool in ("homey", "consult_expert"):
                context_id_str = (request.metadata or {}).get("context_id")
                if context_id_str:
                    from uuid import UUID

                    sig = inspect.signature(tool.run)
                    has_context_id = "context_id" in sig.parameters
                    has_kwargs = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
                    if has_context_id or has_kwargs:
                        final_args["context_id"] = UUID(context_id_str)

            with start_span(f"tool.call.{step.tool}"):
                try:
                    run_args = (final_args or {}).copy()

                    output = None
                    result_outputs = []  # Collect all result outputs
                    content_chunks = []  # Collect content stream
                    tool_source_count = 0  # Track source count from streaming tools

                    # Wrap tool execution with timeout
                    async with asyncio.timeout(TOOL_TIMEOUT_SECONDS):
                        if inspect.isasyncgenfunction(tool.run):
                            async for chunk in tool.run(**run_args):
                                if isinstance(chunk, dict) and chunk.get("type") == "thinking":
                                    yield {
                                        "type": "thinking",
                                        "content": chunk.get("content"),
                                    }
                                    await asyncio.sleep(0)  # Force flush
                                elif isinstance(chunk, dict) and chunk.get("type") == "content":
                                    # Forward content events and collect for final output
                                    if chunk_content := chunk.get("content"):
                                        content_chunks.append(str(chunk_content))
                                        yield {
                                            "type": "content",
                                            "content": chunk_content,
                                        }
                                        await asyncio.sleep(0)  # Force flush
                                elif isinstance(chunk, dict) and chunk.get("type") == "result":
                                    chunk_output = chunk.get("output")
                                    if chunk_output:
                                        result_outputs.append(str(chunk_output))
                                    # Capture source_count from skill delegate result events
                                    sc = chunk.get("source_count")
                                    if isinstance(sc, int):
                                        tool_source_count = sc
                                        LOGGER.info(
                                            "[_run_tool_gen] Captured source_count=%d",
                                            tool_source_count,
                                        )

                            # Prefer collected content stream over result outputs
                            # Content chunks are the actual skill output (researcher, etc.)
                            # Result outputs are status messages ("Worker finished.", etc.)
                            if content_chunks:
                                output = "".join(content_chunks)
                            elif result_outputs:
                                output = "\n".join(result_outputs)
                            else:
                                output = "No valid output from streaming tool."
                        else:
                            output = await tool.run(**run_args)

                    output_text = str(output)

                except TimeoutError:
                    LOGGER.error(f"Tool {step.tool} timed out after {TOOL_TIMEOUT_SECONDS}s")
                    yield {
                        "type": "final",
                        "data": (
                            {
                                "name": step.tool,
                                "status": "error",
                                "reason": f"Tool timed out after {TOOL_TIMEOUT_SECONDS} seconds",
                            },
                            tool_messages,
                            "error",
                        ),
                    }
                    return

                except TypeError as exc:
                    yield {
                        "type": "final",
                        "data": (
                            {
                                "name": step.tool,
                                "status": "error",
                                "reason": f"Invalid arguments: {exc}",
                            },
                            tool_messages,
                            "error",
                        ),
                    }
                    return

            # Phase 4: Integration Feedback
            msg_content = f"Tool {step.tool} output:\n{output_text}"

            # Phase 1: Active Observability - Error Interception
            trace_status = "ok"
            if (
                output_text.startswith("Error:")
                or "Traceback (most recent call last)" in output_text
            ):
                trace_status = "error"
                set_span_status("ERROR", description=output_text[:200])
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
                    status=trace_status,
                    output_preview=output_text[:200],
                    trace=trace_ctx,
                )
            )
            yield {
                "type": "final",
                "data": (
                    {
                        "name": step.tool,
                        "status": trace_status,
                        "output": output_text,
                        "source_count": tool_source_count,
                    },
                    tool_messages,
                    trace_status,
                ),
            }

        except BaseException as e:
            LOGGER.critical(f"CRITICAL FAILURE in _run_tool_gen: {e}", exc_info=True)
            yield {
                "type": "final",
                "data": (
                    {
                        "name": step.tool,
                        "status": "error",
                        "reason": f"System Crash: {e}",
                    },
                    [],
                    "error",
                ),
            }

    async def _generate_completion(
        self,
        step: PlanStep,
        prompt_history: list[AgentMessage],
        request: AgentRequest,
    ) -> tuple[str, str]:
        # Use composer model for completion (non-streaming fallback)
        model_override = str(step.args.get("model") or "composer")
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

    async def _execute_memory_step(
        self,
        step: PlanStep,
        request: AgentRequest,
        conversation_id: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute a memory search step."""
        messages: list[AgentMessage] = []

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
                AgentMessage(
                    role="system",
                    content=f"Context memory: {record.text}",
                )
            )

        result = {"count": len(records)}
        status = "ok"
        step_res = StepResult(step=step, status=status, result=result, messages=messages)
        yield {"type": "result", "result": step_res}

    async def _execute_completion_step(
        self,
        step: PlanStep,
        request: AgentRequest,
        prompt_history: list[AgentMessage],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute a completion step with streaming."""
        # Use composer model for final answer synthesis (fast Llama)
        model_override = str(step.args.get("model") or "composer")

        with start_span("llm.call.assistant") as llm_span:
            llm_span.set_attribute("model", model_override)
            llm_span.set_attribute("step", str(step.id))

            full_content = []

            # Add composer system prompt to preserve technical content language
            composer_system = AgentMessage(
                role="system",
                content=(
                    "You are synthesizing a final answer for the user. "
                    "CRITICAL: When the previous steps contain technical content like "
                    "requirements, user stories, drafts, or work items, you MUST preserve "
                    "their exact language (usually English). Do NOT translate technical "
                    "content to the user's language. Only translate conversational parts."
                ),
            )

            # Reconstruct messages list with composer system prompt
            # prompt_history already includes the user request from service.py
            msgs_to_send = [composer_system] + list(prompt_history)

            async for chunk in self._litellm.stream_chat(msgs_to_send, model=model_override):
                if chunk["type"] == "content" and chunk["content"]:
                    full_content.append(chunk["content"])
                    yield {"type": "content", "content": chunk["content"]}
                    await asyncio.sleep(0)  # Yield for flush
                elif chunk["type"] == "error":
                    raise Exception(chunk["content"])

            completion_text = "".join(full_content)
            llm_span.set_attribute("llm.output.size", len(completion_text))

            result = {"completion": completion_text, "model": model_override}
            status = "ok"

            # Yield final result
            step_res = StepResult(step=step, status=status, result=result, messages=[])
            yield {"type": "result", "result": step_res}

    async def _execute_tool_step(
        self,
        step: PlanStep,
        request: AgentRequest,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute a tool step, wrapping the generator.

        Handles both traditional tools (yielding 'final' events) and streaming tools
        like SkillDelegateTool (yielding 'content' and 'result' events).
        """
        content_chunks: list[str] = []
        result_outputs: list[str] = []
        got_final = False
        source_count = 0  # Track source count from skill delegate tool

        async for tool_event in self._run_tool_gen(step, request):
            if tool_event["type"] == "final":
                # Traditional tool completion
                result, messages, status = tool_event["data"]
                LOGGER.info(f"[StepExecutor] Got FINAL event, result keys: {result.keys()}")
                step_res = StepResult(step=step, status=status, result=result, messages=messages)
                yield {"type": "result", "result": step_res}
                got_final = True

            elif tool_event["type"] == "content":
                # Streaming tool content (e.g., SkillDelegateTool)
                # Collect but DON'T stream - skill output should inform the answer, not  # noqa: E501
                # BE the answer
                chunk = tool_event.get("content", "")
                if chunk:
                    content_chunks.append(chunk)

            elif tool_event["type"] == "result":
                # Streaming tool result event (e.g., SkillDelegateTool)
                output = tool_event.get("output", "")
                if output and output not in ("Worker finished.",):
                    result_outputs.append(output)
                # Capture source count from skill delegate tool
                if "source_count" in tool_event:
                    source_count = tool_event["source_count"]
                    LOGGER.info(
                        f"[StepExecutor] Captured source_count={source_count} from tool_event"
                    )

            elif tool_event["type"] == "skill_activity":
                # Forward skill activity events for OpenWebUI display
                yield tool_event

            elif tool_event["type"] == "thinking":
                yield {
                    "type": "thinking",
                    "content": tool_event["content"],
                    "metadata": tool_event.get("metadata"),
                }
                await asyncio.sleep(0)  # Yield for flush

        # If no 'final' event was received, build StepResult from collected content/results
        # This handles streaming tools like SkillDelegateTool (consult_expert)
        if not got_final:
            # Prefer streamed content over result messages
            if content_chunks:
                output_text = "".join(content_chunks)
            elif result_outputs:
                output_text = "\n".join(result_outputs)
            else:
                output_text = "No output from tool."

            # Build messages list for prompt_history (critical for LLM context)
            messages = [
                AgentMessage(
                    role="system",
                    content=f"Tool {step.tool} output:\n{output_text}",
                )
            ]
            result = {
                "name": step.tool,
                "status": "ok",
                "output": output_text,
                "source_count": source_count,
            }
            LOGGER.info(
                f"[StepExecutor] Yielding result with source_count={source_count} "
                f"for tool={step.tool}"
            )
            step_res = StepResult(step=step, status="ok", result=result, messages=messages)
            yield {"type": "result", "result": step_res}


__all__ = ["StepExecutorAgent", "StepResult"]
