"""Tool for delegating work to a specialist (sub-agent)."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from shared.models import AgentMessage

from core.command_loader import load_command
from core.core.litellm_client import LiteLLMClient
from core.tools.base import Tool
from core.tools.registry import ToolRegistry

LOGGER = logging.getLogger(__name__)


class SkillDelegateTool(Tool):
    """Orchestration tool to delegate a task to a skilled worker."""

    name = "consult_expert"
    description = (
        "Delegates a task to a specialized worker persona. "
        "`skill` MUST be an existing markdown filename (e.g., 'researcher', "
        "'requirements_engineer') available in the system. "
        "Do not pass atomic tool names here."
    )
    category = "orchestration"

    def __init__(self, litellm: LiteLLMClient, registry: ToolRegistry) -> None:
        self._litellm = litellm
        self._registry = registry

    async def run(self, skill: str, goal: str) -> str:
        """Execute a sub-agent loop for the given skill and goal."""

        # 1. Load Skill
        try:
            metadata, system_prompt = load_command(skill, {})
        except FileNotFoundError:
            return f"Error: Skill '{skill}' not found. Please verify available skills."
        except Exception as e:
            return f"Error loading skill '{skill}': {e}"

        # 2. Resolve Tools
        allowed_names = metadata.get("tools", [])
        worker_tools = []

        # Security: Only allow tools explicitly listed in the skill definition.
        # If 'tools' is empty, the worker has NO tools.
        for name in allowed_names:
            t = self._registry.get(name)
            if t:
                worker_tools.append(t)
            else:
                LOGGER.warning(f"Skill '{skill}' requested missing tool '{name}'")

        # 3. Build LiteLLM Tool Definitions
        tool_schemas = []
        for t in worker_tools:
            info: dict[str, Any] = {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                },
            }
            if hasattr(t, "parameters"):
                info["function"]["parameters"] = t.parameters
            tool_schemas.append(info)

        # 4. Worker Loop
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        today = datetime.now().strftime("%Y-%m-%d")
        year = datetime.now().year

        system_context = (
            "SYSTEM CONTEXT:\n"
            f"- Current Date & Time: {now}\n"
            f"- Your knowledge cutoff is static, but YOU ARE LIVE in {year}.\n"
            f"- Treat all retrieved documents dated up to {today} "
            "as HISTORICAL FACTS, not predictions.\n"
        )

        messages = [
            AgentMessage(
                role="system",
                content=f"{system_context}\n{system_prompt}",
            ),
            AgentMessage(role="user", content=goal),
        ]

        logger_prefix = f"[Worker:{skill}]"
        LOGGER.info(f"{logger_prefix} Starting goal: {goal}")

        # Import tracing only inside method to avoid circular imports at module level if any
        # (Though better to import at top if possible, but let's be safe for this specific patch)
        from core.observability.tracing import start_span

        max_turns = 10

        with start_span(f"skill.execution.{skill}", attributes={"goal": goal}):
            for i in range(max_turns):
                LOGGER.debug(f"{logger_prefix} Turn {i+1}")
                with start_span(f"skill.turn.{i+1}"):
                    try:
                        # Call LLM
                        assistant_data = await self._litellm.run_with_tools(
                            messages, tools=tool_schemas if tool_schemas else []
                        )
                    except Exception as e:
                        LOGGER.error(f"{logger_prefix} LLM Error: {e}", exc_info=True)
                        return f"Worker Error (LLM): {e}"

                    content = assistant_data.get("content")
                    tool_calls = assistant_data.get("tool_calls")

                    # Log event?

                    assistant_msg = AgentMessage(
                        role="assistant", content=content, tool_calls=tool_calls
                    )
                    messages.append(assistant_msg)

                    if not tool_calls:
                        if content:
                            return content
                        return "Worker produced empty response."

                    for tc in tool_calls:
                        func = tc["function"]
                        fname = func["name"]
                        call_id = tc["id"]

                        with start_span(f"skill.tool.{fname}"):
                            try:
                                fargs = json.loads(func["arguments"])
                            except json.JSONDecodeError:
                                fargs = {}

                            tool_obj = next((t for t in worker_tools if t.name == fname), None)

                            output_str = ""
                            if tool_obj:
                                LOGGER.info(f"{logger_prefix} Executing {fname}")
                                try:
                                    output_str = str(await tool_obj.run(**fargs))
                                except Exception as e:
                                    output_str = f"Error: {e}"
                            else:
                                output_str = f"Error: Tool {fname} not found in worker context."

                            messages.append(
                                AgentMessage(
                                    role="tool",
                                    tool_call_id=call_id,
                                    name=fname,
                                    content=output_str,
                                )
                            )

        return "Worker timed out (max turns reached)."
