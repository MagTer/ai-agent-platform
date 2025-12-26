"""Tool for delegating work to a specialist (sub-agent)."""

from __future__ import annotations

import json
import logging

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
        "Delegate a specific task to a domain expert. "
        "Required for: Web Search, File Operations, Coding, Math, etc. "
        "Args: skill='name_of_skill', goal='what to do'"
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
        allowed_names = metadata.get("tools")
        worker_tools = []

        if allowed_names:
            # Explicit allowable list
            for name in allowed_names:
                t = self._registry.get(name)
                if t:
                    worker_tools.append(t)
        else:
            # Default: All DOMAIN tools
            for t in self._registry.tools():
                if getattr(t, "category", "domain") == "domain":
                    worker_tools.append(t)

        # 3. Build LiteLLM Tool Definitions
        tool_schemas = []
        for t in worker_tools:
            info = {
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
        messages = [
            AgentMessage(role="system", content=system_prompt),
            AgentMessage(role="user", content=goal),
        ]

        logger_prefix = f"[Worker:{skill}]"
        LOGGER.info(f"{logger_prefix} Starting goal: {goal}")

        # Determine model override from skill metadata?
        # For now use default.

        max_turns = 10

        for i in range(max_turns):
            LOGGER.debug(f"{logger_prefix} Turn {i+1}")
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

            # Helper to create correct AgentMessage from LiteLLM/OpenAI dict
            assistant_msg = AgentMessage(role="assistant", content=content, tool_calls=tool_calls)
            messages.append(assistant_msg)

            # If no tool calls, we assume this is the final answer or a question
            if not tool_calls:
                if content:
                    return content
                return "Worker produced empty response."

            # Process Tool Calls
            for tc in tool_calls:
                func = tc["function"]
                fname = func["name"]
                call_id = tc["id"]

                # Parse Args
                try:
                    fargs = json.loads(func["arguments"])
                except json.JSONDecodeError:
                    fargs = {}

                # Find tool
                tool_obj = next((t for t in worker_tools if t.name == fname), None)

                output_str = ""
                if tool_obj:
                    LOGGER.info(f"{logger_prefix} Executing {fname}")
                    try:
                        # Ensure we await the tool run
                        output_str = str(await tool_obj.run(**fargs))
                    except Exception as e:
                        output_str = f"Error: {e}"
                else:
                    output_str = f"Error: Tool {fname} not found in worker context."

                # Append Tool Output Message
                messages.append(
                    AgentMessage(role="tool", tool_call_id=call_id, name=fname, content=output_str)
                )

        return "Worker timed out (max turns reached)."
