import asyncio
import json
import logging
import secrets
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from shared.chunk_filter import ChunkFilter
from shared.sanitize import sanitize_log
from shared.streaming import VerbosityLevel
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.header_auth import extract_user_from_headers
from core.auth.user_service import get_or_create_user
from core.context import ContextService
from core.core.config import Settings, get_settings
from core.core.service import AgentService
from core.core.service_factory import ServiceFactory
from core.db.engine import get_db
from interfaces.base import PlatformAdapter
from orchestrator.dispatcher import Dispatcher

LOGGER = logging.getLogger(__name__)

router = APIRouter()


def verify_internal_api_key_openwebui(
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    """Verify internal API key for OpenWebUI adapter endpoints.

    Checks for API key in Authorization: Bearer <key> OR X-API-Key: <key> header.
    If AGENT_INTERNAL_API_KEY is not set, SKIP auth (dev convenience).

    Args:
        authorization: Authorization header value
        x_api_key: X-API-Key header value
        settings: Application settings

    Raises:
        HTTPException 401: If key is required but invalid or missing
    """
    # If internal_api_key is not set, skip authentication
    if not settings.internal_api_key:
        return

    # Extract key from headers
    provided_key: str | None = None

    # Check Authorization: Bearer <key>
    if authorization and authorization.startswith("Bearer "):
        provided_key = authorization[7:]  # Remove "Bearer " prefix

    # Check X-API-Key header (takes precedence if both present)
    if x_api_key:
        provided_key = x_api_key

    # Validate key using constant-time comparison
    if not provided_key or not secrets.compare_digest(
        provided_key.encode(),
        settings.internal_api_key.encode(),
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
        )


def _get_debug_category(chunk_type: str, metadata: dict[str, Any] | None) -> tuple[str, str]:
    """Get category icon and label for DEBUG mode output.

    Returns:
        Tuple of (icon, category_label) for the chunk type.
    """
    meta = metadata or {}

    if chunk_type == "thinking":
        role = meta.get("role", "")
        source = meta.get("source", "")
        if role == "Planner" or source == "":
            return ("🔵", "planning")
        if source == "reasoning_model":
            return ("🟣", "reasoning")
        if source == "skill_internal":
            return ("🟡", "skill")
        return ("🔵", "thinking")

    if chunk_type in ("step_start", "tool_start", "tool_output"):
        return ("🟢", "execution")

    if chunk_type == "skill_activity":
        return ("🟡", "activity")

    if chunk_type in ("content", "result"):
        return ("⚪", "output")

    if chunk_type == "error":
        return ("🔴", "error")

    if chunk_type in ("plan", "completion", "history_snapshot", "done"):
        return ("⚫", "meta")

    return ("⚪", chunk_type)


class OpenWebUIAdapter(PlatformAdapter):
    """Adapter for Open WebUI (HTTP/OpenAI API).

    This is primarily a passive adapter (FastAPI handles the lifecycle),
    but implementation ensures architectural consistency.
    """

    platform_name = "openwebui"

    async def start(self) -> None:
        LOGGER.info("OpenWebUIAdapter (HTTP) initialized. Listening via FastAPI.")

    async def stop(self) -> None:
        LOGGER.info("OpenWebUIAdapter (HTTP) stopped.")

    async def send_message(
        self, conversation_id: str, content: str, metadata: dict[str, Any] | None = None
    ) -> None:
        # In the request-response model, we assume the response is returned
        # by the endpoint. This method is illustrative or for async push if valid.
        LOGGER.debug("OpenWebUIAdapter.send_message called for %s", sanitize_log(conversation_id))


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


def get_dispatcher(request: Request) -> Dispatcher:
    """Get Dispatcher with shared LiteLLMClient from app state.

    Reuses the singleton LiteLLMClient to avoid per-request httpx.AsyncClient leaks.
    """
    factory: ServiceFactory = request.app.state.service_factory
    skill_registry = factory._skill_registry

    # Get shared LiteLLM client from app state (created once at startup)
    litellm = request.app.state.litellm_client

    # skill_registry can be None if not initialized - handle gracefully
    if skill_registry is None:
        raise HTTPException(status_code=503, detail="Skill registry not initialized")

    # After None check, mypy knows skill_registry is SkillRegistry
    return Dispatcher(skill_registry, litellm)


async def get_or_create_context_id(
    request: ChatCompletionRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_db),
) -> UUID:
    """Extract or create context_id from OpenWebUI conversation.

    Delegates to ContextService for the actual resolution logic.

    Args:
        request: OpenWebUI chat completion request
        http_request: FastAPI Request object (for headers)
        session: Database session

    Returns:
        Context UUID for this conversation
    """
    # Try authenticated user first
    identity = extract_user_from_headers(http_request)
    if identity:
        return await ContextService.resolve_for_authenticated_user(identity, session)

    # Anonymous -- resolve via conversation ID
    LOGGER.debug("No user headers found, using anonymous context logic")
    conversation_id_str = (
        request.chat_id or request.session_id or (request.metadata or {}).get("conversation_id")
    )

    if not conversation_id_str:
        return await ContextService.resolve_anonymous("openwebui", session)

    return await ContextService.resolve_for_conversation_id(
        conversation_id_str, "openwebui", session
    )


def get_service_factory(request: Request) -> ServiceFactory:
    """Get service factory from FastAPI app state via Request."""
    return request.app.state.service_factory


async def get_agent_service(
    context_id: UUID = Depends(get_or_create_context_id),
    session: AsyncSession = Depends(get_db),
    factory: ServiceFactory = Depends(get_service_factory),
) -> AgentService:
    """Get context-aware AgentService using ServiceFactory.

    This creates a service instance scoped to the context, with:
    - Context-isolated MemoryStore
    - Context-specific tool registry (including MCP tools in Phase 3)
    - Proper multi-tenant isolation

    Args:
        context_id: Context UUID from get_or_create_context_id dependency
        session: Database session
        factory: Service factory from app state

    Returns:
        AgentService instance scoped to the context
    """
    return await factory.create_service(context_id, session)


# --- Endpoints ---


@router.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    http_request: Request,
    dispatcher: Dispatcher = Depends(get_dispatcher),
    context_id: UUID = Depends(get_or_create_context_id),
    agent_service: AgentService = Depends(get_agent_service),
    session: AsyncSession = Depends(get_db),
    _auth: None = Depends(verify_internal_api_key_openwebui),
) -> Any:
    """
    OpenAI-compatible endpoint for Open WebUI.
    Routes requests via the Dispatcher and streams responses.
    """
    # Extract user identity for tool context
    identity = extract_user_from_headers(http_request)
    user_email = identity.email if identity else None
    user_id: UUID | None = None

    # Look up user_id for credential access
    if identity:
        try:
            db_user = await get_or_create_user(identity, session)
            user_id = db_user.id
        except Exception as e:
            LOGGER.warning(f"Failed to get user for credential lookup: {e}")

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
        LOGGER.warning("No chat_id in request, generated new: %s", sanitize_log(conversation_id))
    else:
        LOGGER.info("Using chat_id from request: %s", sanitize_log(conversation_id))

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

    # Check for verbosity flags
    verbosity = VerbosityLevel.DEFAULT
    upper_message = user_message.upper()
    if "[DEBUG]" in upper_message:
        verbosity = VerbosityLevel.DEBUG
        user_message = user_message.replace("[DEBUG]", "").replace("[debug]", "").strip()
    elif "[VERBOSE]" in upper_message:
        verbosity = VerbosityLevel.VERBOSE
        user_message = user_message.replace("[VERBOSE]", "").replace("[verbose]", "").strip()

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
            verbosity,
            history,
            user_email=user_email,
            user_id=user_id,
            context_id=context_id,
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
    verbosity: VerbosityLevel = VerbosityLevel.DEFAULT,
    history: list | None = None,
    user_email: str | None = None,
    user_id: UUID | None = None,
    context_id: UUID | None = None,
) -> AsyncGenerator[str, None]:
    """
    Generates SSE events compatible with OpenAI API from AgentChunks.

    Uses token batching to reduce the number of SSE events sent to the client.
    Content tokens are aggregated and flushed based on time interval or buffer size.

    Verbosity levels:
    - DEFAULT: Minimal output - final answer, errors, brief skill start/completion.
    - VERBOSE: Detailed output - adds thinking, step progress, tool calls, skill activity.
    - DEBUG: Technical output - raw JSON for all chunks, final answer renders normally.
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

    # Track execution phase for content filtering
    in_completion_phase = False

    # Shared chunk filter for verbosity + safety rules
    chunk_filter = ChunkFilter(verbosity)

    # Track if we've shown any substantive output yet
    has_shown_output = False

    async def flush_content_buffer() -> AsyncGenerator[str, None]:
        """Flush accumulated content buffer if not empty.

        Filters out content containing raw model tokens or reasoning patterns
        (in DEFAULT mode) before yielding.
        """
        nonlocal content_buffer, last_flush_time, has_shown_output
        if content_buffer:
            batched_content = "".join(content_buffer)
            content_buffer = []
            last_flush_time = time.time()

            if not chunk_filter.is_safe_content(batched_content):
                return

            has_shown_output = True
            yield _format_chunk(chunk_id, created, model_name, batched_content)

    # Build metadata for tools
    tool_metadata: dict[str, Any] = {}
    if user_email:
        tool_metadata["user_email"] = user_email
    if user_id:
        tool_metadata["user_id"] = str(user_id)
    if context_id:
        tool_metadata["context_id"] = str(context_id)

    try:
        async for agent_chunk in dispatcher.stream_message(
            session_id=session_id,
            message=message,
            platform="web",  # defaulting to web
            platform_id=None,
            db_session=db_session,
            agent_service=agent_service,
            history=history,  # Pass conversation history to dispatcher
            metadata=tool_metadata,
        ):
            chunk_type = agent_chunk["type"]
            content = agent_chunk.get("content")
            metadata = agent_chunk.get("metadata")

            # Track execution phase for filtering
            # - step_start with skill: skill starts (suppress reasoning content)
            # - tool_output: skill completes (allow content through)
            # - step_start with completion: final answer phase (always allow content)
            if chunk_type == "step_start":
                action = (metadata or {}).get("action", "")
                in_completion_phase = action == "completion"

            # Apply verbosity filter
            if not chunk_filter.should_show(chunk_type, metadata, content):
                continue

            # DEBUG mode: Show categorized JSON for all chunks except completion content
            if verbosity == VerbosityLevel.DEBUG:
                # Skip JSON output for content during completion (let it render normally below)
                if not (chunk_type == "content" and in_completion_phase):
                    # Flush any pending content first
                    async for chunk in flush_content_buffer():
                        yield chunk

                    # Get category for better visual organization
                    icon, category = _get_debug_category(chunk_type, metadata)
                    debug_output = f"\n> {icon} **[{category}]** `{chunk_type}`\n"
                    debug_output += "> ```json\n"

                    # Format JSON with proper indentation for readability
                    try:
                        json_str = json.dumps(agent_chunk, indent=2, ensure_ascii=False)
                        debug_output += f"> {json_str}\n"
                    except (TypeError, ValueError):
                        debug_output += f"> {agent_chunk!r}\n"

                    debug_output += "> ```\n\n"
                    yield _format_chunk(chunk_id, created, model_name, debug_output)

            # Process chunks normally (formatted output for DEFAULT/VERBOSE, or content for DEBUG)
            if chunk_type == "content" and content:
                # Apply safety filters (skip AWAITING_USER_INPUT marker)
                if "[AWAITING_USER_INPUT" not in content:
                    if not chunk_filter.is_safe_content(content):
                        continue

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
                # Skip internal thinking that clutters the UI
                source = (agent_chunk.get("metadata") or {}).get("source", "")
                skip_sources = ("reasoning_model", "skill_internal")
                if source in skip_sources and verbosity != VerbosityLevel.DEBUG:
                    continue

                # Filter out thinking with raw model tokens
                if not chunk_filter.is_safe_content(content):
                    continue

                # Skip duplicate plan descriptions
                cleaned = _clean_content(content)
                if cleaned.startswith("Plan:") and chunk_filter.is_duplicate_plan(cleaned):
                    continue

                # Flush any pending content before thinking output
                async for chunk in flush_content_buffer():
                    yield chunk

                # Show initial processing indicator on first substantive output
                if not has_shown_output:
                    has_shown_output = True

                # Check for streaming flag
                is_stream = False
                if (
                    agent_chunk.get("metadata")
                    and agent_chunk["metadata"]
                    and agent_chunk["metadata"].get("stream")
                ):
                    is_stream = True

                if is_stream:
                    # Just yield content for cleaner token streaming
                    yield _format_chunk(chunk_id, created, model_name, content)
                else:
                    role = (agent_chunk.get("metadata") or {}).get("role", "Agent")
                    formatted = f"\n🧠 **{role}:** *{cleaned}*\n\n"
                    yield _format_chunk(chunk_id, created, model_name, formatted)

            elif chunk_type == "step_start":
                # Provide visibility into the plan
                # Clean the content to handle dicts/JSON
                label = _clean_content(content)
                meta = agent_chunk.get("metadata") or {}
                role = meta.get("role", "Executor")
                action = meta.get("action", "")
                executor = meta.get("executor", "")
                tool_name = meta.get("tool", "")

                # Improve labels for clarity
                if action == "completion":
                    formatted = "\n\n📝 **Agent:** *Composing final answer*\n\n"
                elif executor == "skill" or action == "skill":
                    # Skills-native execution: tool_name is the skill name
                    formatted = f"\n\n🧠 **Using skill:** `{tool_name}`\n\n"
                else:
                    formatted = f"\n\n👣 **{role}:** *{label}*\n\n"
                yield _format_chunk(chunk_id, created, model_name, formatted)

            elif chunk_type == "tool_start":
                tool_call = agent_chunk.get("tool_call")
                role = (agent_chunk.get("metadata") or {}).get("role", "Executor")
                if tool_call:
                    tool_name = tool_call.get("name", "unknown")
                    args = tool_call.get("arguments", {})
                    args_str = ""

                    try:
                        # Normalize args to dict if possible
                        parsed_args = args
                        if isinstance(args, str):
                            args = args.strip()
                            if args.startswith("{"):
                                try:
                                    parsed_args = json.loads(args)
                                except json.JSONDecodeError:
                                    pass

                        # Format args string
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
                        args_str = ""

                    formatted = f"\n🛠️ **{role}:** `{tool_name}` *{args_str}*\n"
                    yield _format_chunk(chunk_id, created, model_name, formatted)

            elif chunk_type == "tool_output":
                # Check status in metadata to determine success/failure
                meta = agent_chunk.get("metadata") or {}
                status = meta.get("status", "success")
                role = meta.get("role", "Executor")
                skill_name = meta.get("skill")  # Set by service.py for skill steps
                source_count = meta.get("source_count", 0)

                # Format based on whether this is a skill or regular tool
                if skill_name:
                    if status == "error":
                        msg = f"\n❌ **{skill_name}:** Failed\n"
                    else:
                        source_text = "source" if source_count == 1 else "sources"
                        msg = f"\n✅ **{skill_name}:** Done ({source_count} {source_text})\n"
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
                tool_name = meta.get("tool", "")
                skill_name = meta.get("skill", "")

                if query:
                    formatted = f"\n🔍 *Searching: {query}*\n"
                elif url:
                    # Show shortened URL for readability
                    short_url = url if len(url) <= 60 else url[:57] + "..."
                    formatted = f"\n🌐 *Fetching: [{short_url}]({url})*\n"
                elif file_path:
                    formatted = f"\n📄 *Reading: {file_path}*\n"
                elif tool_name:
                    # Show tool name if available
                    formatted = f"\n⚙️ *Using {tool_name}*\n"
                elif skill_name:
                    formatted = f"\n🧠 *{skill_name}: Working...*\n"
                else:
                    # Fallback with content
                    cleaned = _clean_content(content) if content else "Processing"
                    if chunk_filter.is_safe_content(cleaned):
                        formatted = f"\n⚙️ *{cleaned}*\n"
                    else:
                        continue
                yield _format_chunk(chunk_id, created, model_name, formatted)

            elif chunk_type == "awaiting_input":
                # Handle structured human-in-the-loop signaling
                meta = agent_chunk.get("metadata") or {}
                prompt = meta.get("prompt", "Awaiting input...")
                options = meta.get("options")
                category = meta.get("category", "clarification")

                formatted = f"\n\n⏸️ **Input needed ({category}):** {prompt}\n"
                if options:
                    formatted += "\n**Options:**\n"
                    for i, opt in enumerate(options, 1):
                        formatted += f"{i}. {opt}\n"
                formatted += "\n"

                yield _format_chunk(chunk_id, created, model_name, formatted)

            elif chunk_type == "trace_info":
                # Show trace_id at start of response for debugging
                meta = agent_chunk.get("metadata") or {}
                trace_id = meta.get("trace_id", "")
                if trace_id:
                    formatted = f"\n🔍 **TraceID:** `{trace_id}`\n\n"
                    yield _format_chunk(chunk_id, created, model_name, formatted)

            elif chunk_type == "error":
                formatted = f"\n❌ **Error:** {_clean_content(content)}\n\n"
                yield _format_chunk(chunk_id, created, model_name, formatted)

            # Explicitly ignore internal events
            # (in DEBUG mode, these are already shown above via the raw JSON output)
            elif chunk_type in ["history_snapshot", "plan", "done"]:
                pass

            elif chunk_type == "completion":
                # Completion metadata - log for debugging but don't display
                meta = agent_chunk.get("metadata") or {}
                LOGGER.debug(
                    "Completion: provider=%s, model=%s",
                    meta.get("provider"),
                    meta.get("model"),
                )

            elif chunk_type == "result":
                # Skill/tool final output - extract from StepResult if present
                result_obj = agent_chunk.get("result")
                output = None

                # Handle StepResult object
                if result_obj:
                    if hasattr(result_obj, "result") and isinstance(result_obj.result, dict):
                        output = result_obj.result.get("output")
                    elif isinstance(result_obj, dict):
                        output = result_obj.get("output") or result_obj.get("result", {}).get(
                            "output"
                        )

                # Fallback to direct output field
                if not output:
                    output = agent_chunk.get("output")

                if output and isinstance(output, str):
                    if chunk_filter.is_safe_content(output):
                        yield _format_chunk(chunk_id, created, model_name, output)

            else:
                # Log but do not show unknown events to user to avoid noise
                LOGGER.debug("Ignored chunk type: %s", chunk_type)

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
