"""Tool for delegating work to a specialist (sub-agent)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
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

    async def run(self, skill: str, goal: str) -> AsyncGenerator[dict[str, Any], None]:
        """Execute a sub-agent loop for the given skill and goal."""

        # 1. Load Skill
        try:
            metadata, system_prompt = load_command(skill, {})
        except FileNotFoundError:
            yield {"type": "result", "output": f"Error: Skill '{skill}' not found."}
            return
        except Exception as e:
            yield {"type": "result", "output": f"Error loading skill '{skill}': {e}"}
            return

        # 2. Resolve Tools
        allowed_names = metadata.get("tools", [])
        worker_tools = []

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
        
        # Import tracing only inside method to avoid circular imports
        from core.observability.tracing import start_span

        max_turns = 10

        with start_span(f"skill.execution.{skill}", attributes={"goal": goal}):
            for i in range(max_turns):
                LOGGER.debug(f"{logger_prefix} Turn {i+1}")
                yield {"type": "thinking", "content": f"Worker ({skill}) Turn {i+1}..."}
                await asyncio.sleep(0.01)  # Force flush
                
                with start_span(f"skill.turn.{i+1}"):

                    # Stream tokens instead of blocking
                    full_content = []
                    tool_calls_buffer = {}  # index -> call
                    
                    try:
                        # Use default model
                        async for chunk in self._litellm.stream_chat(messages, model=None):
                             # 1. Yield Thinking Tokens
                             if chunk["type"] == "content" and chunk["content"]:
                                 content = chunk["content"]
                                 full_content.append(content)
                                 yield {"type": "thinking", "content": content}
                                 
                             # 2. Accumulate Tool Calls
                             elif chunk["type"] == "tool_start" and chunk["tool_call"]:
                                 tc = chunk["tool_call"]
                                 idx = tc["index"]
                                 if idx not in tool_calls_buffer:
                                     tool_calls_buffer[idx] = tc
                                 else:
                                     # Append logic for delta updates
                                     # (simplified for now: assume full tool call or delta handling in client)
                                     # LiteLLMClient currently yields full tool calls or deltas. 
                                     # Based on litellm_client.py, it yields 'tool_call' from delta.
                                     # We need to assemble deltas if they are fragmented.
                                     # Checking litellm_client.py:
                                     # if "tool_calls" in delta: for tool_call in delta["tool_calls"]: yield ...
                                     # This is likely a Delta.
                                     prev = tool_calls_buffer[idx]
                                     if "function" in tc:
                                          if "name" in tc["function"] and tc["function"]["name"]:
                                              prev["function"]["name"] = (
                                                  prev["function"].get("name") or ""
                                              ) + tc["function"]["name"]
                                          if "arguments" in tc["function"] and tc["function"]["arguments"]:
                                              prev["function"]["arguments"] = (
                                                  prev["function"].get("arguments") or ""
                                              ) + tc["function"]["arguments"]
                                 
                             # 3. Handle Error
                             elif chunk["type"] == "error":
                                 yield {"type": "result", "output": f"Worker Error: {chunk['content']}"}
                                 return

                    except Exception as e:
                         LOGGER.error(f"{logger_prefix} Streaming Error: {e}", exc_info=True)
                         yield {"type": "result", "output": f"Worker Error (Stream): {e}"}
                         return

                    content = "".join(full_content)
                    
                    # Assemble final tool calls
                    tool_calls = list(tool_calls_buffer.values())
                    
                    assistant_msg = AgentMessage(
                        role="assistant", content=content, tool_calls=tool_calls
                    )
                    messages.append(assistant_msg)

                    if not tool_calls:
                        if content:
                            yield {"type": "result", "output": "Worker finished."}
                            # Don't return yet, let next loop decide? No, we return result at end of 'turn' if no tools?
                            # If no tools, the worker is done speaking?
                            # Typically YES.
                            # But we yielded thinking tokens.
                            # We should yield the FINAL result event with the accumulated content.
                            # Wait, the Executor expects "result" event to contain string output?
                            yield {"type": "result", "output": content}
                            return
                        yield {"type": "result", "output": "Worker produced empty response."}
                        return

                    for tc in tool_calls:
                        func = tc["function"]
                        fname = func["name"]
                        call_id = tc["id"]


                        yield {"type": "thinking", "content": f"Worker invoking {fname}..."}
                        await asyncio.sleep(0.01)  # Force flush

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
                                    # Check if tool is also streaming? 
                                    # For now, assume other tools are atomic.
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

        yield {"type": "result", "output": "Worker timed out (max turns reached)."}
