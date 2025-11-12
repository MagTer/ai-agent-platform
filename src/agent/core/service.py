"""High level agent orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from agent.tools import ToolRegistry, load_tool_registry

from .config import Settings
from .litellm_client import LiteLLMClient
from .memory import MemoryRecord, MemoryStore
from .models import AgentMessage, AgentRequest, AgentResponse, Plan, PlanStep
from .state import StateStore

LOGGER = logging.getLogger(__name__)


@dataclass
class PlanExecution:
    """Result of executing a planner's steps."""

    final_prompt: list[AgentMessage]
    plan_tool_results: list[dict[str, Any]]
    completion: str
    provider: str
    model: str
    completion_step_id: str | None = None


class AgentService:
    """Coordinate the memory, LLM and metadata layers."""

    def __init__(
        self,
        settings: Settings,
        litellm: LiteLLMClient | None = None,
        memory: MemoryStore | None = None,
        state_store: StateStore | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._settings = settings
        self._litellm = litellm or LiteLLMClient(settings)
        self._memory = memory or MemoryStore(settings)
        self._state_store = state_store or StateStore(settings.sqlite_state_path)
        self._tool_registry = tool_registry or load_tool_registry(settings.tools_config_path)

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

        metadata_tool_results = await self._execute_tools(request_metadata)
        all_tool_results = list(metadata_tool_results)
        for result in metadata_tool_results:
            steps.append(self._tool_result_entry(result, source="metadata"))

        history_with_tools = list(history)
        for result in metadata_tool_results:
            if result.get("status") == "ok" and result.get("output"):
                history_with_tools.append(
                    AgentMessage(
                        role="system",
                        content=f"Tool {result['name']} output:\n{result['output']}",
                    )
                )

        plan = await self._build_plan(request, history_with_tools, request_metadata)
        plan_dict = plan.model_dump()
        steps.append(
            {
                "type": "plan",
                "status": "created",
                "description": plan.description,
                "plan": plan_dict,
            }
        )

        user_message = AgentMessage(role="user", content=request.prompt)
        plan_execution = await self._execute_plan(
            plan=plan,
            request=request,
            history=history_with_tools,
            steps=steps,
            conversation_id=conversation_id,
            user_message=user_message,
        )
        all_tool_results.extend(plan_execution.plan_tool_results)

        assistant_message = AgentMessage(role="assistant", content=plan_execution.completion)
        completion_step: dict[str, Any] = {
            "type": "completion",
            "provider": plan_execution.provider,
            "model": plan_execution.model,
            "status": "ok",
        }
        if plan_execution.completion_step_id:
            completion_step["plan_step_id"] = plan_execution.completion_step_id
        steps.append(completion_step)

        await asyncio.to_thread(
            self._memory.add_records,
            [MemoryRecord(conversation_id=conversation_id, text=request.prompt)],
        )
        self._state_store.append_messages(
            conversation_id,
            [user_message, assistant_message],
        )

        response_metadata = dict(request_metadata)
        response_metadata["plan"] = plan_dict
        if all_tool_results:
            response_metadata["tool_results"] = all_tool_results

        return AgentResponse(
            conversation_id=conversation_id,
            response=plan_execution.completion,
            messages=plan_execution.final_prompt + [assistant_message],
            steps=steps,
            metadata=response_metadata,
        )

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
        try:
            output = await tool.run(**sanitized_args)
        except Exception as exc:  # pragma: no cover - depends on tool implementation
            LOGGER.exception("Tool %s execution failed", tool_name)
            result.update({"status": "error", "error": str(exc)})
            return result

        output_text = str(output)
        trimmed_output = output_text[: self._settings.tool_result_max_chars]
        result.update(
            {
                "status": "ok",
                "output": trimmed_output,
            }
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

    async def _build_plan(
        self,
        request: AgentRequest,
        history: list[AgentMessage],
        metadata: dict[str, Any],
    ) -> Plan:
        """Ask Phi3 Mini to describe a sequential orchestration plan for the prompt."""

        tool_descriptions = self._describe_tools()
        if tool_descriptions:
            tool_listing = "\n".join(
                f"- {entry['name']}: {entry['description']}" for entry in tool_descriptions
            )
        else:
            tool_listing = "- (no MCP-specific tools are registered)"
        available_tools_text = tool_listing

        history_text = (
            "\n".join(f"{message.role}: {message.content}" for message in history) or "(no history)"
        )
        try:
            metadata_text = json.dumps(metadata, indent=2)
        except (TypeError, ValueError):
            metadata_text = str(metadata)

        system_message = AgentMessage(
            role="system",
            content=(
                "You are the Phi3 Mini planner for Open WebUI. Return a single JSON object "
                "describing the sequential steps needed to answer the user's prompt. "
                'Structure the response as {"steps": [...], "description": "optional '
                'summary"}. Each step must include:\n'
                '- "id" (unique string)\n'
                '- "label" (short description)\n'
                '- "executor" (agent|litellm|remote)\n'
                '- "action" (memory|tool|completion)\n'
                '- optional "tool" (when action is tool)\n'
                '- optional "args" containing tool parameters and helper metadata\n'
                '- optional "description" for human-readable context\n'
                '- optional "provider" when a remote LLM is required\n\n'
                "Memory and RAG access (Qdrant/embedder) happen through the agent with action "
                '"memory"; web_fetch, ragproxy, and any other registered MCP helpers should '
                "be referenced by their tool names. Try to include the helper list below when "
                "a step uses a tool. Return valid JSON only, without surrounding explanation."
            ),
        )

        user_message = AgentMessage(
            role="user",
            content=(
                f"Question:\n{request.prompt}\n\n"
                f"Conversation history:\n{history_text}\n\n"
                f"Metadata provided to the agent:\n{metadata_text}\n\n"
                f"Available tools:\n{available_tools_text}\n\n"
                "Plan each step so that the agent can update the UI with progress. The final step "
                "should be a completion (executor litellm or remote)."
            ),
        )

        plan_text = await self._litellm.plan(
            [system_message, user_message],
        )
        LOGGER.debug(
            "Planner prompt system:\n%s\nuser:\n%s",
            system_message.content,
            user_message.content,
        )
        LOGGER.debug("Planner raw output: %s", plan_text)
        return self._parse_plan(plan_text, request.prompt)

    def _parse_plan(self, raw: str, prompt: str) -> Plan:
        candidate = self._extract_json_fragment(raw)
        if candidate is None:
            LOGGER.warning(
                "Unable to parse plan output from LiteLLM (raw=%s); falling back to default plan",
                raw,
            )
            return self._fallback_plan(prompt)

        try:
            return Plan(**candidate)
        except ValidationError as exc:
            LOGGER.warning("LiteLLM plan validation failed; falling back (%s)", exc)
            return self._fallback_plan(prompt)

    @staticmethod
    def _extract_json_fragment(raw: str) -> dict[str, Any] | None:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start == -1 or end == -1:
                return None
            fragment = raw[start : end + 1]
            try:
                return json.loads(fragment)
            except json.JSONDecodeError:
                return None

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

    async def _execute_plan(
        self,
        plan: Plan,
        request: AgentRequest,
        history: list[AgentMessage],
        steps: list[dict[str, Any]],
        conversation_id: str,
        user_message: AgentMessage,
    ) -> PlanExecution:
        prompt_history = list(history)
        memory_messages: list[AgentMessage] = []
        tool_messages: list[AgentMessage] = []
        plan_tool_results: list[dict[str, Any]] = []
        completion_text = ""
        completion_provider = "litellm"
        completion_model = self._settings.litellm_model
        completion_step_id: str | None = None
        final_prompt: list[AgentMessage] | None = None

        for plan_step in plan.steps:
            step_entry: dict[str, Any] = {
                "type": "plan_step",
                "id": plan_step.id,
                "label": plan_step.label,
                "executor": plan_step.executor,
                "action": plan_step.action,
            }
            LOGGER.debug("Executing plan step %s (%s)", plan_step.id, plan_step.label)
            if plan_step.description:
                step_entry["description"] = plan_step.description
            if plan_step.tool:
                step_entry["tool"] = plan_step.tool
            if plan_step.args:
                step_entry["args"] = plan_step.args
            step_entry["status"] = "in_progress"
            steps.append(step_entry)

            try:
                if plan_step.executor == "agent":
                    if plan_step.action == "memory":
                        query = plan_step.args.get("query") or request.prompt
                        limit_value = plan_step.args.get("limit")
                        try:
                            limit = int(limit_value) if limit_value is not None else 5
                        except (TypeError, ValueError):
                            limit = 5
                        records = self._memory.search(
                            str(query),
                            limit=limit,
                            conversation_id=conversation_id,
                        )
                        for record in records:
                            memory_messages.append(
                                AgentMessage(
                                    role="system",
                                    content=f"Context memory: {record.text}",
                                )
                            )
                        step_entry.update(status="ok", result={"count": len(records)})
                        continue
                    if plan_step.action == "tool":
                        if not plan_step.tool:
                            step_entry.update(status="error", reason="missing tool name")
                            continue
                        call_args = self._coerce_tool_call_args(plan_step.args)
                        allowlist = self._parse_tool_allowlist(plan_step.args.get("allowed_tools"))
                        tool_result = await self._run_tool_call(
                            plan_step.tool,
                            call_args,
                            allowlist=allowlist,
                        )
                        plan_tool_results.append(tool_result)
                        step_entry.update(
                            status=tool_result.get("status", "unknown"),
                            result=tool_result,
                        )
                        if tool_result.get("output"):
                            tool_messages.append(
                                AgentMessage(
                                    role="system",
                                    content=(
                                        f"Tool {plan_step.tool} output:\n{tool_result['output']}"
                                    ),
                                )
                            )
                        continue
                    step_entry.update(status="skipped", reason="agent executor cannot run action")
                    continue

                if plan_step.executor in {"litellm", "remote"} and plan_step.action == "completion":
                    final_prompt = prompt_history + memory_messages + tool_messages + [user_message]
                    model_override = str(
                        plan_step.args.get("model") or self._settings.litellm_model
                    )
                    completion_text = await self._litellm.generate(
                        final_prompt, model=model_override
                    )
                    completion_provider = plan_step.provider or (
                        "remote" if plan_step.executor == "remote" else "litellm"
                    )
                    completion_model = model_override
                    completion_step_id = plan_step.id
                    step_entry.update(
                        status="ok",
                        provider=completion_provider,
                        model=completion_model,
                        result={"completion": completion_text},
                    )
                    break

                step_entry.update(status="skipped", reason="unsupported executor or action")
            except Exception as exc:
                LOGGER.exception("Plan step execution failed for %s", plan_step.id)
                step_entry.update(status="error", error=str(exc))
                if plan_step.action == "completion":
                    completion_text = ""
                break

        if not completion_text:
            final_prompt = prompt_history + memory_messages + tool_messages + [user_message]
            completion_model = self._settings.litellm_model
            completion_text = await self._litellm.generate(final_prompt)
            completion_provider = "litellm"

        if final_prompt is None:
            final_prompt = prompt_history + memory_messages + tool_messages + [user_message]

        return PlanExecution(
            final_prompt=final_prompt,
            plan_tool_results=plan_tool_results,
            completion=completion_text,
            provider=completion_provider,
            model=completion_model,
            completion_step_id=completion_step_id,
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
        if isinstance(raw, (list, tuple, set)):
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
