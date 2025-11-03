"""High level agent orchestration."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from agent.tools import ToolRegistry, load_tool_registry

from .config import Settings
from .litellm_client import LiteLLMClient
from .memory import MemoryRecord, MemoryStore
from .models import AgentMessage, AgentRequest, AgentResponse
from .state import StateStore

LOGGER = logging.getLogger(__name__)


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

        if request.messages:
            history = list(request.messages)
        else:
            history = self._state_store.get_messages(conversation_id)
        steps: list[dict[str, Any]] = []

        semantic_memories = self._memory.search(
            request.prompt, conversation_id=conversation_id
        )
        if semantic_memories:
            steps.append(
                {
                    "type": "memory_retrieval",
                    "source": "qdrant",
                    "count": len(semantic_memories),
                }
            )
        request_metadata: dict[str, Any] = dict(request.metadata or {})
        tool_results = await self._execute_tools(request_metadata)
        if tool_results:
            for result in tool_results:
                step_entry: dict[str, Any] = {
                    "type": "tool",
                    "name": result.get("name"),
                    "status": result.get("status"),
                }
                if result.get("status") == "ok" and result.get("output"):
                    step_entry["output"] = result["output"]
                steps.append(step_entry)

        prompt_messages: list[AgentMessage] = []
        if history:
            prompt_messages.extend(history)
        for memory in semantic_memories:
            prompt_messages.append(
                AgentMessage(
                    role="system",
                    content=f"Context memory: {memory.text}",
                )
            )

        for result in tool_results:
            if result.get("status") != "ok":
                continue
            prompt_messages.append(
                AgentMessage(
                    role="system",
                    content=f"Tool {result['name']} output:\n{result['output']}",
                )
            )

        user_message = AgentMessage(role="user", content=request.prompt)
        prompt_messages.append(user_message)

        completion = await self._litellm.generate(prompt_messages)
        assistant_message = AgentMessage(role="assistant", content=completion)
        steps.append(
            {
                "type": "completion",
                "provider": "litellm",
                "model": self._settings.litellm_model,
            }
        )

        await asyncio.to_thread(
            self._memory.add_records,
            [MemoryRecord(conversation_id=conversation_id, text=request.prompt)],
        )
        self._state_store.append_messages(
            conversation_id,
            [user_message, assistant_message],
        )

        if tool_results:
            request_metadata["tool_results"] = tool_results

        response = AgentResponse(
            conversation_id=conversation_id,
            response=completion,
            messages=prompt_messages + [assistant_message],
            steps=steps,
            metadata=request_metadata,
        )
        return response

    def conversation_history(self, conversation_id: str, limit: int = 20) -> list[AgentMessage]:
        """Return the stored conversation history."""

        return self._state_store.get_messages(conversation_id, limit=limit)

    async def _execute_tools(self, metadata: dict[str, Any]) -> list[dict[str, Any]]:
        """Execute requested tools and return a structured result list."""

        if not metadata:
            return []

        allowlist: set[str] | None = None
        tools_field = metadata.get("tools")
        if isinstance(tools_field, list):
            allowlist = {str(item) for item in tools_field if isinstance(item, str)}
        elif tools_field is not None:
            LOGGER.warning("Ignoring metadata.tools because it is not a list")

        raw_calls = metadata.get("tool_calls")
        if not raw_calls:
            return []
        if isinstance(raw_calls, dict):
            call_items: list[Any] = [raw_calls]
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
                args_field = entry.get("args", {})
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

            tool_name = str(tool_name)
            if allowlist is not None and tool_name not in allowlist:
                LOGGER.info("Tool %s not in allow-list; skipping execution", tool_name)
                results.append({"name": tool_name, "status": "skipped", "reason": "not-allowed"})
                continue

            tool = self._tool_registry.get(tool_name) if self._tool_registry else None
            if not tool:
                LOGGER.warning("Requested tool %s is not registered", tool_name)
                results.append({"name": tool_name, "status": "missing"})
                continue

            try:
                output = await tool.run(**call_args)
            except Exception as exc:  # pragma: no cover - depends on tool implementation
                LOGGER.exception("Tool %s execution failed", tool_name)
                results.append({"name": tool_name, "status": "error", "error": str(exc)})
                continue

            output_text = str(output)
            trimmed_output = output_text[: self._settings.tool_result_max_chars]
            results.append(
                {
                    "name": tool_name,
                    "status": "ok",
                    "output": trimmed_output,
                }
            )

        return results


__all__ = ["AgentService"]
