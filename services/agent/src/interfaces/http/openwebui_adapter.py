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

    # Use provided conversation_id or generate new one
    session_id = (request.metadata or {}).get("conversation_id")
    if not session_id:
        session_id = str(uuid.uuid4())

    # 2. Execute & Stream
    # We now enforce streaming or at least async generation for all requests
    # If request.stream is False, we should accumulate (MVP: just stream anyway or error?).
    # OpenAI allows stream=False.
    # The requirement says "Streaming-First", so we'll implement the streaming response.
    # OpenWebUI usually requests stream=True.

    return StreamingResponse(
        stream_response_generator(
            session_id, user_message, request.model, dispatcher, session, agent_service
        ),
        media_type="text/event-stream",
    )


async def stream_response_generator(
    session_id: str,
    message: str,
    model_name: str,
    dispatcher: Dispatcher,
    db_session: AsyncSession,
    agent_service: AgentService,
) -> AsyncGenerator[str, None]:
    """
    Generates SSE events compatible with OpenAI API from AgentChunks.
    """
    chunk_id = f"chatcmpl-{uuid.uuid4()}"
    created = int(time.time())

    # Initial ACK
    yield _format_chunk(chunk_id, created, model_name, "")

    try:
        async for agent_chunk in dispatcher.stream_message(
            session_id=session_id,
            message=message,
            platform="web",  # defaulting to web
            platform_id=None,
            db_session=db_session,
            agent_service=agent_service,
        ):
            chunk_type = agent_chunk["type"]
            content = agent_chunk.get("content")

            if chunk_type == "content" and content:
                yield _format_chunk(chunk_id, created, model_name, content)

            elif chunk_type == "thinking" and content:
                # Check for streaming flag
                is_stream = False
                if agent_chunk.get("metadata") and agent_chunk["metadata"].get("stream"):
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
                    formatted = f"\n> 🧠 *{_clean_content(content)}*\n\n"
                    yield _format_chunk(chunk_id, created, model_name, formatted)

            elif chunk_type == "step_start":
                # Provide visibility into the plan
                # Clean the content to handle dicts/JSON
                label = _clean_content(content)
                formatted = f"\n\n> 👣 **Step:** *{label}*\n\n"
                yield _format_chunk(chunk_id, created, model_name, formatted)

            elif chunk_type == "tool_start":
                tool_call = agent_chunk.get("tool_call")
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
                        formatted = f"\n> 🧠 **Using Skill:** `{skill_name}`\n"
                    else:
                        formatted = f"\n> 🛠️ **Tool:** `{tool_name}` *{args_str}*\n"

                    yield _format_chunk(
                        chunk_id,
                        created,
                        model_name,
                        formatted,
                    )

            elif chunk_type == "tool_output":
                yield _format_chunk(chunk_id, created, model_name, "\n> ✅ *Finished*\n")

            elif chunk_type == "error":
                formatted = f"\n> ❌ **Error:** {_clean_content(content)}\n\n"
                yield _format_chunk(chunk_id, created, model_name, formatted)

            # Explicitly ignore history_snapshot and other internal events
            elif chunk_type in ["history_snapshot", "plan"]:
                pass

            else:
                # Log but do not show unknown events to user to avoid noise
                LOGGER.debug(f"Ignored chunk type: {chunk_type}")

    except Exception as e:
        LOGGER.error(f"Error during streaming: {e}")
        yield _format_chunk(chunk_id, created, model_name, f"\n\nSystem Error: {str(e)}")

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
