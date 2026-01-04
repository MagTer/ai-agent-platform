import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.core.config import Settings, get_settings
from core.core.litellm_client import LiteLLMClient
from core.core.memory import MemoryStore
from core.core.service import AgentService
from core.db.engine import get_db
from core.tools.loader import load_tool_registry
from interfaces.base import PlatformAdapter
from orchestrator.dispatcher import Dispatcher
from orchestrator.skill_loader import SkillLoader

LOGGER = logging.getLogger(__name__)

router = APIRouter()


class OpenWebUIAdapter(PlatformAdapter):
    """
    Adapter for Open WebUI (HTTP/OpenAI API).
    This is primarily a passive adapter (FastAPI handles the lifecycle),
    but implementation ensures architectural consistency.
    """

    async def start(self) -> None:
        LOGGER.info("OpenWebUIAdapter (HTTP) initialized. Listening via FastAPI.")

    async def stop(self) -> None:
        LOGGER.info("OpenWebUIAdapter (HTTP) stopped.")

    async def send_message(
        self, conversation_id: str, content: str, metadata: dict[str, Any] | None = None
    ) -> None:
        # In the request-response model, we assume the response is returned
        # by the endpoint. This method is illustrative or for async push if valid.
        LOGGER.debug(f"OpenWebUIAdapter.send_message called for {conversation_id}")


# --- OpenAI Compatibility Models ---


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "gpt-3.5-turbo"
    messages: list[ChatMessage]
    stream: bool = False
    metadata: dict[str, Any] | None = None
    # OpenWebUI sends these for conversation tracking
    chat_id: str | None = None
    session_id: str | None = None
    # Allow additional fields OpenWebUI might send (params, features, etc)
    model_config = {"extra": "ignore"}


# --- Dependencies ---


def get_settings_dep() -> Settings:
    return get_settings()


def get_dispatcher(settings: Settings = Depends(get_settings_dep)) -> Dispatcher:
    loader = SkillLoader()
    # We need a LiteLLMClient.
    # Optimization: reuse the one from AgentService if possible, but simpler to create one here.
    # Or better: create a get_litellm dependency.
    litellm = LiteLLMClient(settings)
    return Dispatcher(loader, litellm)


async def get_agent_service(
    settings: Settings = Depends(get_settings_dep),
) -> AgentService:
    litellm = LiteLLMClient(settings)
    memory = MemoryStore(settings)
    await memory.ainit()  # Await async initialization
    tool_registry = load_tool_registry(settings.tools_config_path)
    return AgentService(settings, litellm, memory, tool_registry=tool_registry)


# --- Endpoints ---


@router.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    dispatcher: Dispatcher = Depends(get_dispatcher),
    agent_service: AgentService = Depends(get_agent_service),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """
    OpenAI-compatible endpoint for Open WebUI.
    Routes requests via the Dispatcher and streams responses.
    """
    # 1. Extract latest user message
    user_message = ""
    for msg in request.messages:
        if msg.role == "user":
            user_message = msg.content

    if not user_message:
        raise HTTPException(status_code=400, detail="No user message found.")

    # Use chat_id from OpenWebUI as the conversation identifier
    # Browser capture confirmed OpenWebUI sends this in every request
    conversation_id = (
        request.chat_id or request.session_id or (request.metadata or {}).get("conversation_id")
    )
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
        LOGGER.warning(f"No chat_id in request, generated new: {conversation_id}")
    else:
        LOGGER.info(f"Using chat_id from request: {conversation_id}")

    # 2. Extract conversation history
    # OpenWebUI sends full history in each request - we must pass it to the agent
    from shared.models import AgentMessage

    history: list[AgentMessage] = []
    if len(request.messages) > 1:
        # All messages except the last one (which is the current user message)
        history = [
            AgentMessage(role=msg.role, content=msg.content) for msg in request.messages[:-1]
        ]
        LOGGER.info(f"Extracted {len(history)} messages from history")

    # 3. Execute & Stream
    # We now enforce streaming or at least async generation for all requests
    # If request.stream is False, we should accumulate (MVP: just stream anyway or error?).
    # OpenAI allows stream=False.
    # The requirement says "Streaming-First", so we'll implement the streaming response.
    # OpenWebUI usually requests stream=True.

    # Check for debug mode
    debug_mode = "[DEBUG]" in user_message.upper()
    if debug_mode:
        # Strip [DEBUG] prefix from message
        user_message = user_message.replace("[DEBUG]", "").replace("[debug]", "").strip()

    # Get trace ID for correlation
    from core.observability.tracing import current_trace_ids

    trace_ids = current_trace_ids()
    trace_id = trace_ids.get("trace_id", "")

    return StreamingResponse(
        stream_response_generator(
            conversation_id,
            user_message,
            request.model,
            dispatcher,
            session,
            agent_service,
            debug_mode,
            history,
        ),
        media_type="text/event-stream",
        headers={"X-Trace-ID": trace_id} if trace_id else None,
    )


async def stream_response_generator(
    session_id: str,
    message: str,
    model_name: str,
    dispatcher: Dispatcher,
    db_session: AsyncSession,
    agent_service: AgentService,
    debug_mode: bool = False,
    history: list | None = None,
) -> AsyncGenerator[str, None]:
    """
    Generates SSE events compatible with OpenAI API from AgentChunks.

    Uses token batching to reduce the number of SSE events sent to the client.
    Content tokens are aggregated and flushed based on time interval or buffer size.
    """
    chunk_id = f"chatcmpl-{uuid.uuid4()}"
    created = int(time.time())

    # Token batching configuration
    batch_interval_sec = 0.05  # Flush every 50ms
    min_batch_size = 10  # Minimum characters before considering time-based flush

    # Initial ACK
    yield _format_chunk(chunk_id, created, model_name, "")

    # Content buffer for batching
    content_buffer: list[str] = []
    last_flush_time = time.time()

    async def flush_content_buffer() -> AsyncGenerator[str, None]:
        """Flush accumulated content buffer if not empty."""
        nonlocal content_buffer, last_flush_time
        if content_buffer:
            batched_content = "".join(content_buffer)
            content_buffer = []
            last_flush_time = time.time()
            yield _format_chunk(chunk_id, created, model_name, batched_content)

    try:
        async for agent_chunk in dispatcher.stream_message(
            session_id=session_id,
            message=message,
            platform="web",  # defaulting to web
            platform_id=None,
            db_session=db_session,
            agent_service=agent_service,
            history=history,  # Pass conversation history to dispatcher
        ):
            chunk_type = agent_chunk["type"]
            content = agent_chunk.get("content")

            # Debug mode: Show all chunks with raw JSON
            if debug_mode:
                # Flush any pending content first
                async for chunk in flush_content_buffer():
                    yield chunk
                debug_output = f"\n> 🐛 **[DEBUG]** Chunk Type: `{chunk_type}`\n"
                debug_output += "> ```json\n"
                debug_output += f"> {json.dumps(agent_chunk, indent=2)}\n"
                debug_output += "> ```\n\n"
                yield _format_chunk(chunk_id, created, model_name, debug_output)

            if chunk_type == "content" and content:
                # Add to buffer instead of yielding immediately
                content_buffer.append(content)

                # Check if we should flush
                now = time.time()
                buffer_size = sum(len(c) for c in content_buffer)
                time_elapsed = now - last_flush_time

                # Flush if buffer is large enough OR time interval elapsed
                if buffer_size >= min_batch_size or time_elapsed >= batch_interval_sec:
                    async for chunk in flush_content_buffer():
                        yield chunk
                    await asyncio.sleep(0)  # Allow event loop to process

            elif chunk_type == "thinking" and content:
                # Flush any pending content before thinking output
                async for chunk in flush_content_buffer():
                    yield chunk
                # Check for streaming flag
                is_stream = False
                if (
                    agent_chunk.get("metadata")
                    and agent_chunk["metadata"]
                    and agent_chunk["metadata"].get("stream")
                ):
                    is_stream = True

                if is_stream:
                    # Just yield content for cleaner token streaming.
                    # (though WebUI might not render it as nicely inline)
                    # Ideally we want to stream into a single block.
                    # But OpenWebUI receives deltas.
                    # If we send `> 🧠 ` once, then tokens?
                    # Hard to coordinate state.
                    # Fallback: Just send the token as raw content prefixed? No.
                    # Send as italics?
                    yield _format_chunk(chunk_id, created, model_name, content)
                else:
                    role = (agent_chunk.get("metadata") or {}).get("role", "Agent")
                    formatted = f"\n🧠 **{role}:** *{_clean_content(content)}*\n\n"
                    yield _format_chunk(chunk_id, created, model_name, formatted)

            elif chunk_type == "step_start":
                # Provide visibility into the plan
                # Clean the content to handle dicts/JSON
                label = _clean_content(content)
                role = (agent_chunk.get("metadata") or {}).get("role", "Executor")
                action = (agent_chunk.get("metadata") or {}).get("action", "")
                tool_name = (agent_chunk.get("metadata") or {}).get("tool", "")

                # Improve labels for clarity
                if action == "completion":
                    formatted = "\n\n📝 **Agent:** *Composing final answer*\n\n"
                elif tool_name == "consult_expert":
                    args = (agent_chunk.get("metadata") or {}).get("args") or {}
                    skill = args.get("skill", "expert")
                    formatted = f"\n\n🧠 **{skill.title()}:** *Starting research*\n\n"
                else:
                    formatted = f"\n\n👣 **{role}:** *{label}*\n\n"
                yield _format_chunk(chunk_id, created, model_name, formatted)

            elif chunk_type == "tool_start":
                tool_call = agent_chunk.get("tool_call")
                role = (agent_chunk.get("metadata") or {}).get("role", "Executor")
                if tool_call:
                    tool_name = tool_call.get("name", "unknown")
                    args = tool_call.get("arguments", {})
                    # Format args concisely
                    args_str = ""
                    skill_name = None

                    try:
                        # 1. Normalize args to dict if possible
                        parsed_args = args
                        if isinstance(args, str):
                            # Try parsing stringified JSON
                            args = args.strip()
                            if args.startswith("{"):
                                try:
                                    parsed_args = json.loads(args)
                                except json.JSONDecodeError:
                                    pass  # Keep as string

                        # 2. Extract Skill
                        if tool_name == "consult_expert" and isinstance(parsed_args, dict):
                            skill_name = parsed_args.get("skill")

                        # 3. Format visual args string
                        if parsed_args:
                            if isinstance(parsed_args, dict):
                                parts = [f"{k}={v}" for k, v in parsed_args.items()]
                                args_str = ", ".join(parts)
                            else:
                                args_str = str(parsed_args)

                        # Truncate if too long
                        if len(args_str) > 40:
                            args_str = args_str[:37] + "..."

                        if args_str:
                            args_str = f"({args_str})"

                    except Exception as e:
                        LOGGER.warning(f"Error formatting tool args: {e}")
                        args_str = ""  # Fallback

                    if skill_name:
                        formatted = f"\n🧠 **{role}:** Using Skill `{skill_name}`\n"
                    else:
                        formatted = f"\n🛠️ **{role}:** `{tool_name}` *{args_str}*\n"

                    yield _format_chunk(
                        chunk_id,
                        created,
                        model_name,
                        formatted,
                    )

            elif chunk_type == "tool_output":
                # Check status in metadata to determine success/failure
                meta = agent_chunk.get("metadata") or {}
                status = meta.get("status", "success")
                role = meta.get("role", "Executor")
                tool_name = meta.get("name", "")

                # Improve labels based on tool type
                if tool_name == "consult_expert":
                    skill = meta.get("skill", "Research")
                    if status == "error":
                        msg = f"\n❌ **{skill.title()}:** Research failed\n"
                    else:
                        msg = f"\n✅ **{skill.title()}:** Research complete\n"
                else:
                    if status == "error":
                        msg = f"\n❌ **{role}:** Failed\n"
                    else:
                        msg = f"\n✅ **{role}:** Done\n"
                yield _format_chunk(chunk_id, created, model_name, msg)

            elif chunk_type == "skill_activity":
                # Show detailed skill activity (search queries, URLs, etc.)
                meta = agent_chunk.get("metadata") or {}
                query = meta.get("search_query")
                url = meta.get("fetch_url")
                file_path = meta.get("file_path")

                if query:
                    formatted = f"\n🔍 *Searching: {query}*\n"
                elif url:
                    # Show shortened URL for readability
                    short_url = url if len(url) <= 60 else url[:57] + "..."
                    formatted = f"\n🌐 *Fetching: [{short_url}]({url})*\n"
                elif file_path:
                    formatted = f"\n📄 *Reading: {file_path}*\n"
                else:
                    formatted = f"\n⚙️ *{_clean_content(content)}*\n"
                yield _format_chunk(chunk_id, created, model_name, formatted)

            elif chunk_type == "error":
                formatted = f"\n❌ **Error:** {_clean_content(content)}\n\n"
                yield _format_chunk(chunk_id, created, model_name, formatted)

            # Explicitly ignore history_snapshot and other internal events (unless debug mode)
            elif chunk_type in ["history_snapshot", "plan"]:
                if not debug_mode:
                    pass
                # In debug mode, these are already shown above

            elif chunk_type == "result":
                # Skill/tool final output - display as regular content
                output = agent_chunk.get("output")
                if output and isinstance(output, str):
                    yield _format_chunk(chunk_id, created, model_name, output)

            else:
                # Log but do not show unknown events to user to avoid noise
                LOGGER.debug(f"Ignored chunk type: {chunk_type}")

    except Exception as e:
        # Flush any remaining content before error
        async for chunk in flush_content_buffer():
            yield chunk
        LOGGER.error(f"Error during streaming: {e}")
        yield _format_chunk(chunk_id, created, model_name, f"\n\nSystem Error: {str(e)}")

    # Flush any remaining content before DONE
    async for chunk in flush_content_buffer():
        yield chunk

    # Final chunk
    yield "data: [DONE]\n\n"


def _clean_content(content: str | dict | None) -> str:
    """Extract readable text from potentially complex/JSON content."""
    if content is None:
        return "Processing..."

    # If it's already a dict, use it directly
    data = content

    # If it's a string, try to parse if it looks like JSON
    if isinstance(content, str):
        content = content.strip()
        if content.startswith(("{", "[")):
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                # Not JSON, use as is (truncated if too long)
                pass

    if isinstance(data, dict):
        # Priority list of readable fields
        for field in ["instruction", "description", "summary", "text", "message"]:
            if val := data.get(field):
                return str(val)
        # Fallback to stringified dict (unlikely to be pretty, but safer)
        return str(data)

    if isinstance(content, str):
        # Just a regular string
        return content

    return str(content)


def _format_chunk(chunk_id: str, created: int, model: str, content: str) -> str:
    data = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
    }
    return f"data: {json.dumps(data)}\n\n"
