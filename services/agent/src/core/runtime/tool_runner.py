"""Tool execution module - handles tool invocation and schema generation."""

from __future__ import annotations

import logging
from typing import Any

from core.models.pydantic_schemas import ToolCallEvent, TraceContext
from core.observability.logging import log_event
from core.observability.tracing import (
    current_trace_ids,
    set_span_attributes,
    set_span_status,
    start_span,
)
from core.runtime.config import Settings
from core.tools import ToolRegistry

LOGGER = logging.getLogger(__name__)


class ToolRunner:
    """Handles tool execution and schema generation."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        settings: Settings,
    ):
        """Initialize the tool runner.

        Args:
            tool_registry: Registry of available tools
            settings: Runtime settings for tool execution
        """
        self._tool_registry = tool_registry
        self._settings = settings

    @staticmethod
    def _parse_tool_allowlist(raw: Any) -> set[str] | None:
        """Parse tool allowlist from request metadata.

        Args:
            raw: Raw allowlist value (can be list, tuple, set, or None)

        Returns:
            Set of allowed tool names, or None if no allowlist specified
        """
        if raw is None:
            return None
        if isinstance(raw, list | tuple | set):
            return {str(item) for item in raw if isinstance(item, str)}
        return None

    async def _execute_tools(self, metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Execute requested tools and return a structured result list.

        Args:
            metadata: Request metadata containing tool_calls and tools allowlist

        Returns:
            List of tool execution results with status, output, and errors
        """
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
        """Run a single tool invocation while normalizing the output.

        Args:
            tool_name: Name of the tool to execute
            call_args: Arguments to pass to the tool
            allowlist: Optional set of allowed tool names

        Returns:
            Dictionary with tool execution results including status, output, and any errors
        """
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
        """Turn a tool result into a structured step entry.

        Args:
            result: Raw tool execution result
            source: Source of the tool call (e.g., "plan", "skill")

        Returns:
            Structured entry suitable for plan execution
        """
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

    def _describe_tools(self, allowlist: set[str] | None = None) -> list[dict[str, Any]]:
        """Generate tool descriptions for LLM context.

        Args:
            allowlist: Optional set of allowed tool names to filter by

        Returns:
            List of tool descriptions with names, descriptions, and schemas
        """
        tool_list = []

        # Registry tools
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
