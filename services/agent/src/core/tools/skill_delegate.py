"""Tool for delegating work to a specialist (sub-agent)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any, cast
from urllib.parse import urlparse
from uuid import UUID

from shared.models import AgentMessage
from sqlalchemy.ext.asyncio import AsyncSession

from core.command_loader import load_command
from core.core.litellm_client import LiteLLMClient
from core.tools.base import Tool
from core.tools.registry import ToolRegistry

LOGGER = logging.getLogger(__name__)


def _build_activity_message(tool_obj: Tool | None, fname: str, fargs: dict[str, Any]) -> str:
    """Build an informative activity message from tool hints or arguments.

    Args:
        tool_obj: The tool object (may have activity_hint attribute)
        fname: The function/tool name
        fargs: The arguments passed to the tool

    Returns:
        A human-readable activity message for UI display
    """
    # 1. Try tool's activity_hint first
    if tool_obj and hasattr(tool_obj, "activity_hint") and tool_obj.activity_hint:
        for arg_name, pattern in tool_obj.activity_hint.items():
            if arg_name in fargs:
                value = fargs[arg_name]
                domain = value  # Default: use value as domain too

                # Handle special {domain} placeholder - extract netloc from URL
                if "{domain}" in pattern and isinstance(value, str):
                    try:
                        domain = urlparse(value).netloc or value
                    except Exception:  # Intentional: fallback if URL parsing fails
                        domain = value

                # Truncate long values
                if isinstance(value, str) and len(value) > 50:
                    value = value[:47] + "..."
                if isinstance(domain, str) and len(domain) > 50:
                    domain = domain[:47] + "..."

                try:
                    return pattern.format(**{arg_name: value, "domain": domain})
                except (KeyError, ValueError):
                    pass  # Fall through to fallback

    # 2. Fallback: Common argument patterns
    if "query" in fargs:
        q = fargs["query"]
        q = q if len(q) <= 50 else q[:47] + "..."
        return f'Searching: "{q}"'
    elif "url" in fargs:
        try:
            domain = urlparse(fargs["url"]).netloc
            return f"Fetching: {domain}"
        except Exception:  # Intentional: fallback to generic message if parsing fails
            return "Fetching URL"
    elif "path" in fargs or "file_path" in fargs:
        path = fargs.get("path") or fargs.get("file_path")
        return f"Reading: {path}"

    # 3. Ultimate fallback
    return f"Using {fname}"


class SkillDelegateTool(Tool):
    """Orchestration tool to delegate a task to a skilled worker.

    DEPRECATED: This tool is maintained for backward compatibility.
    New plans should use executor="skill", action="skill" directly.

    The skills-native execution architecture routes skill steps directly
    to SkillExecutor, bypassing this tool. This tool will be removed
    in a future release.
    """

    name = "consult_expert"
    description = (
        "[DEPRECATED] Delegates a task to a specialized worker persona. "
        "New plans should use executor='skill' and action='skill' directly. "
        "`skill` MUST be an existing markdown filename (e.g., 'researcher', "
        "'requirements_drafter') available in the system."
    )
    category = "orchestration"
    activity_hint = {"skill": "Consulting: {skill}"}
    deprecated = True  # Flag for deprecation
    parameters = {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "The name of the skill/persona to delegate to (e.g., 'researcher').",
            },
            "goal": {
                "type": "string",
                "description": "The specific task or goal for the skill to accomplish.",
            },
        },
        "required": ["skill", "goal"],
    }

    def __init__(self, litellm: LiteLLMClient, registry: ToolRegistry) -> None:
        self._litellm = litellm
        self._registry = registry

    async def run(  # type: ignore[override]
        self,
        skill: str,
        goal: str,
        user_id: UUID | None = None,
        session: AsyncSession | None = None,
        context_id: UUID | None = None,
        **kwargs: Any,  # Accept and ignore extra arguments from LLM
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute a sub-agent loop for the given skill and goal.

        DEPRECATED: Use SkillExecutor with executor="skill" steps instead.

        Args:
            skill: Name of the skill/persona to delegate to
            goal: The specific task or goal for the skill
            user_id: User UUID for credential lookup (passed to tools that need it)
            session: Database session for credential lookup (passed to tools that need it)
            context_id: Context UUID for OAuth token lookup (passed to tools that need it)
            **kwargs: Extra arguments (logged and ignored)
        """
        # Deprecation warning
        LOGGER.warning(
            "DEPRECATED: consult_expert tool is deprecated. "
            "Use executor='skill', action='skill' in plan steps instead. "
            "Skill: %s",
            skill,
        )

        # Store credential context for sub-tools
        self._user_id = user_id
        self._session = session
        self._context_id = context_id

        # Log any unexpected extra args for debugging (exclude our credential params)
        credential_params = ("user_id", "session", "context_id")
        extra_kwargs = {k: v for k, v in kwargs.items() if k not in credential_params}
        if extra_kwargs:
            LOGGER.debug(f"Ignoring extra args in consult_expert: {list(extra_kwargs.keys())}")

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
        tool_lookup: dict[str, Tool] = {}  # For activity message lookup
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
            tool_lookup[t.name] = t

        # 4. Worker Loop
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        today = datetime.now().strftime("%Y-%m-%d")
        year = datetime.now().year

        # Get max_turns from skill metadata, default to 10 if not specified
        max_turns = metadata.get("max_turns", 10)
        if not isinstance(max_turns, int) or max_turns < 1:
            LOGGER.warning(f"Invalid max_turns for skill '{skill}', using default 10")
            max_turns = 10

        system_context = (
            "SYSTEM CONTEXT:\n"
            f"- Current Date & Time: {now}\n"
            f"- Your knowledge cutoff is static, but YOU ARE LIVE in {year}.\n"
            f"- Treat all retrieved documents dated up to {today} "
            "as HISTORICAL FACTS, not predictions.\n"
            "\n"
            "## EXECUTION PROTOCOL\n"
            "\n"
            "RULE 1 - PROGRESSIVE RESEARCH: You may call tools multiple times "
            "to gather information.\n"
            "RULE 2 - AVOID EXACT DUPLICATES: Don't repeat identical tool calls "
            "(same args).\n"
            "RULE 3 - STOP WHEN SUFFICIENT: After gathering enough data, "
            "provide your final answer.\n"
            "\n"
            "CORRECT FLOW:\n"
            "1. User asks question\n"
            "2. Call tools as needed (search, fetch, query)\n"
            "3. Analyze results, call more tools if needed\n"
            "4. When you have sufficient information, provide final answer\n"
            "\n"
            "BUDGET:\n"
            f"- Maximum turns: {max_turns}\n"
            "- Maximum calls per tool type: 3\n"
            "- Use tools strategically to avoid wasting budget\n"
            "\n"
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
        from core.observability.tracing import set_span_attributes, start_span

        source_count = 0  # Track number of tool calls (sources)
        seen_calls: set[tuple[str, str]] = set()  # Track executed calls to prevent loops
        tool_call_counts: dict[str, int] = {}  # Track calls per tool type for rate limiting
        max_calls_per_tool = 3  # Maximum calls to any single tool
        blocked_this_turn = False  # Track if we blocked a call this turn

        LOGGER.info(f"{logger_prefix} Max turns allowed: {max_turns}")

        # Use model from skill metadata if specified
        skill_model = metadata.get("model")
        if skill_model:
            LOGGER.info(f"{logger_prefix} Using model from skill metadata: {skill_model}")

        with start_span(f"skill.execution.{skill}", attributes={"goal": goal}):
            for i in range(max_turns):
                LOGGER.debug(f"{logger_prefix} Turn {i+1}")
                # Show research topic on first turn only
                # For Turn 2+, we show a detailed summary after tool calls are known
                if i == 0:
                    # Truncate goal to one line (max 80 chars for readability)
                    display_goal = goal if len(goal) <= 80 else goal[:77] + "..."
                    yield {"type": "thinking", "content": f"Goal: {display_goal}"}
                    await asyncio.sleep(0)  # Force flush

                # Reset blocked flag for this turn
                blocked_this_turn = False

                with start_span(f"skill.turn.{i+1}") as _turn_span:
                    # Capture turn metadata
                    set_span_attributes(
                        {
                            "skill.turn": i + 1,
                            "skill.name": skill,
                        }
                    )

                    # Stream tokens instead of blocking
                    full_content = []
                    tool_calls_buffer = {}  # index -> call

                    try:
                        # Use skill's preferred model, or default if not specified
                        tools_arg = tool_schemas if tool_schemas else None
                        async for chunk in self._litellm.stream_chat(
                            messages, model=skill_model, tools=tools_arg
                        ):
                            # 1. Yield Thinking Tokens
                            if chunk["type"] == "content" and chunk["content"]:
                                content = chunk["content"]
                                full_content.append(content)
                                yield {
                                    "type": "content",
                                    "content": content,
                                }

                            # 2. Accumulate Tool Calls
                            elif chunk["type"] == "tool_start" and chunk["tool_call"]:
                                tc = chunk["tool_call"]
                                idx = tc["index"]
                                if idx not in tool_calls_buffer:
                                    tool_calls_buffer[idx] = tc
                                else:
                                    self._merge_tool_calls(
                                        tool_calls_buffer, cast(dict[str, Any], chunk)
                                    )

                            # 3. Handle Error
                            elif chunk["type"] == "error":
                                err = f"Worker Error: {chunk['content']}"
                                yield {"type": "result", "output": err}
                                return

                    except Exception as e:
                        LOGGER.error(f"{logger_prefix} Stream Err: {e}", exc_info=True)
                        yield {"type": "result", "output": f"Stream Error: {e}"}
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
                            LOGGER.info(
                                f"{logger_prefix} Yielding final result with "
                                f"source_count={source_count}"
                            )
                            yield {
                                "type": "result",
                                "output": "Worker finished.",
                                "source_count": source_count,
                            }
                            # Yield final result event with accumulated content.
                            yield {
                                "type": "result",
                                "output": content,
                                "source_count": source_count,
                            }
                            return
                        yield {
                            "type": "result",
                            "output": "Worker produced empty response.",
                            "source_count": source_count,
                        }
                        return

                    # Count sources (tool calls)
                    source_count += len(tool_calls)
                    LOGGER.info(
                        f"{logger_prefix} Turn {i+1}: {len(tool_calls)} tool calls "
                        f"(total sources: {source_count})"
                    )

                    # Build a summary of what this turn will do
                    turn_activities: list[str] = []
                    for tc in tool_calls:
                        try:
                            fargs = json.loads(tc["function"]["arguments"])
                        except json.JSONDecodeError:
                            fargs = {}
                        if "query" in fargs:
                            q = fargs["query"]
                            # Truncate long queries
                            q = q if len(q) <= 50 else q[:47] + "..."
                            turn_activities.append(f'"{q}"')
                        elif "url" in fargs:
                            # Extract domain from URL for clearer display
                            try:
                                domain = urlparse(fargs["url"]).netloc
                                turn_activities.append(domain or "URL")
                            except Exception:  # Intentional: fallback if URL parsing fails
                                turn_activities.append("URL")

                    # Count searches vs fetches for summary
                    search_count = sum(1 for a in turn_activities if a.startswith('"'))
                    fetch_count = len(turn_activities) - search_count

                    # Show turn summary if we have activities (Turn 2+)
                    if i > 0 and turn_activities:
                        # Show up to 3 queries/activities
                        if len(turn_activities) > 3:
                            extra = len(turn_activities) - 3
                            summary = ", ".join(turn_activities[:3]) + f" +{extra} more"
                        else:
                            summary = ", ".join(turn_activities)

                        # Build action verb based on what we're doing
                        if search_count > 0 and fetch_count > 0:
                            action = "Searching/fetching"
                        elif fetch_count > 0:
                            action = f"Fetching {fetch_count} pages:"
                        else:
                            action = "Searching"

                        yield {
                            "type": "thinking",
                            "content": f"Turn {i+1}: {action} {summary}",
                        }
                        await asyncio.sleep(0)  # Force flush

                    for tc in tool_calls:
                        func = tc["function"]
                        fname = func["name"]
                        call_id = tc["id"]

                        # Parse arguments early for activity message
                        try:
                            fargs = json.loads(func["arguments"])
                        except json.JSONDecodeError:
                            fargs = {}

                        # Build informative message using tool's activity_hint
                        tool_obj = tool_lookup.get(fname)
                        invoke_msg = _build_activity_message(tool_obj, fname, fargs)

                        # Pre-calculate duplication status to suppress UI for duplicates
                        # We still re-check inside the span for blocking logic
                        call_key_check = (fname, json.dumps(fargs, sort_keys=True))
                        is_duplicate_ui = call_key_check in seen_calls

                        if is_duplicate_ui:
                            yield {
                                "type": "thinking",
                                "content": f"Skipping duplicate call to {fname}",
                            }
                        else:
                            yield {
                                "type": "thinking",
                                "content": invoke_msg,
                            }

                            # Yield detailed skill_activity for OpenWebUI live display
                            yield {
                                "type": "skill_activity",
                                "content": invoke_msg,
                                "metadata": {
                                    "tool": fname,
                                    "search_query": fargs.get("query"),
                                    "fetch_url": fargs.get("url"),
                                    "file_path": fargs.get("path") or fargs.get("file_path"),
                                    "skill": skill,
                                },
                            }
                        await asyncio.sleep(0)  # Force flush

                        with start_span(f"skill.tool.{fname}") as _tool_span:
                            # fargs already parsed above

                            # Add detailed attributes for search queries
                            tool_attrs: dict[str, str | int] = {
                                "tool.name": fname,
                                "tool.args": json.dumps(fargs)[:500],  # Truncate
                            }
                            # Extract common tool-specific attributes
                            # Search tools (web_search, search_code, tibp_wiki_search)
                            if "query" in fargs:
                                tool_attrs["search.query"] = str(fargs["query"])[:200]
                            # Web fetch
                            if "url" in fargs:
                                tool_attrs["fetch.url"] = str(fargs["url"])[:200]
                            # File operations (read_file, write_to_file)
                            if "path" in fargs:
                                tool_attrs["file.path"] = str(fargs["path"])[:200]
                            if "file_path" in fargs:
                                tool_attrs["file.path"] = str(fargs["file_path"])[:200]
                            # Azure DevOps
                            if "action" in fargs:
                                tool_attrs["devops.action"] = str(fargs["action"])
                            if "work_item_id" in fargs:
                                tool_attrs["devops.work_item_id"] = int(fargs["work_item_id"])
                            if "type" in fargs:
                                tool_attrs["devops.type"] = str(fargs["type"])
                            # Test runner
                            if "test_path" in fargs:
                                tool_attrs["test.path"] = str(fargs["test_path"])[:200]
                            set_span_attributes(tool_attrs)

                            # Deduplication and rate limiting checks
                            call_key = (fname, json.dumps(fargs, sort_keys=True))
                            current_tool_count = tool_call_counts.get(fname, 0)

                            output_str = ""
                            if call_key in seen_calls:
                                # Exact duplicate - same tool, same args
                                LOGGER.warning(
                                    "%s Blocking duplicate call to %s", logger_prefix, fname
                                )
                                output_str = (
                                    f"BLOCKED: Duplicate call to '{fname}'. "
                                    "You already have the data from your previous call. "
                                    "STOP calling tools. Write your final answer NOW."
                                )
                                set_span_attributes({"tool.status": "duplicate_blocked"})
                                blocked_this_turn = True
                            elif current_tool_count >= max_calls_per_tool:
                                # Rate limit - too many calls to same tool
                                LOGGER.warning(
                                    f"{logger_prefix} Rate limiting {fname} "
                                    f"(called {current_tool_count} times)"
                                )
                                output_str = (
                                    f"BLOCKED: Maximum calls ({max_calls_per_tool}) to '{fname}' "
                                    "reached. Use the data you have collected. "
                                    "Write your final answer NOW."
                                )
                                set_span_attributes({"tool.status": "rate_limited"})
                                blocked_this_turn = True
                            else:
                                seen_calls.add(call_key)
                                tool_call_counts[fname] = current_tool_count + 1

                                tool_obj = next((t for t in worker_tools if t.name == fname), None)
                                if tool_obj:
                                    LOGGER.info(f"{logger_prefix} Executing {fname}")
                                    try:
                                        # Inject credential context for tools that need it
                                        tool_args = fargs.copy()
                                        if self._user_id is not None and self._session is not None:
                                            # Tools that require user credential lookup
                                            if fname == "azure_devops":
                                                tool_args["user_id"] = self._user_id
                                                tool_args["session"] = self._session
                                        # Inject context_id for tools that need OAuth token lookup
                                        if self._context_id is not None:
                                            if fname == "homey":
                                                tool_args["context_id"] = self._context_id
                                        output_str = str(await tool_obj.run(**tool_args))
                                        # Capture output summary in span
                                        set_span_attributes(
                                            {
                                                "tool.output_preview": output_str[:500],
                                                "tool.output_length": len(output_str),
                                                "tool.status": "success",
                                            }
                                        )
                                    except Exception as e:
                                        output_str = f"Error: {e}"
                                        set_span_attributes(
                                            {
                                                "tool.status": "error",
                                                "tool.error": str(e)[:200],
                                            }
                                        )
                                else:
                                    output_str = f"Error: Tool {fname} not found in worker context."
                                    set_span_attributes({"tool.status": "not_found"})

                            messages.append(
                                AgentMessage(
                                    role="tool",
                                    tool_call_id=call_id,
                                    name=fname,
                                    content=output_str,
                                )
                            )

                # After processing tool calls, break if we blocked any call
                if blocked_this_turn:
                    LOGGER.info(
                        f"{logger_prefix} Blocked duplicate/rate-limited call, "
                        "terminating skill execution with source_count={source_count}"
                    )
                    break
            else:
                # Loop completed without break (hit max_turns)
                LOGGER.warning(
                    f"{logger_prefix} Reached max_turns limit ({max_turns}) with "
                    f"source_count={source_count}"
                )

        # Extract tool outputs from messages to include in final result
        tool_outputs: list[str] = []
        for msg in messages:
            if msg.role == "tool" and msg.content:
                # Only include substantial outputs (not errors or blocked messages)
                if (
                    not msg.content.startswith("Error:")
                    and not msg.content.startswith("BLOCKED:")
                    and len(msg.content) > 50
                ):
                    tool_outputs.append(msg.content)

        # Build a useful summary - always indicate max_turns was reached
        if tool_outputs:
            # Include up to 3 most recent substantial tool outputs
            recent_outputs = tool_outputs[-3:]
            combined = "\n\n---\n\n".join(recent_outputs)
            output_msg = (
                f"Skill '{skill}' reached maximum turns limit ({max_turns}). "
                f"Collected data from {source_count} sources:\n\n"
                f"{combined}"
            )
        else:
            output_msg = (
                f"Skill '{skill}' reached maximum turns limit ({max_turns}). "
                f"Used {source_count} sources but no substantial results."
            )

        yield {
            "type": "result",
            "output": output_msg,
            "source_count": source_count,
        }

    def _merge_tool_calls(self, buffer: dict[int, Any], chunk: dict[str, Any]) -> None:
        """Merge streaming tool call deltas into the buffer."""
        tc = chunk["tool_call"]
        idx = tc["index"]

        # Delta update
        prev = buffer[idx]
        if "function" not in tc:
            return

        func = tc["function"]
        if "name" in func and func["name"]:
            prev["function"]["name"] = (prev["function"].get("name") or "") + func["name"]

        if "arguments" in func and func["arguments"]:
            prev["function"]["arguments"] = (prev["function"].get("arguments") or "") + func[
                "arguments"
            ]
