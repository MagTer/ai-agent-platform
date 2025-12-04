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

from core.core.config import get_settings
from core.core.models import AgentMessage, AgentRequest
from core.core.service import AgentService

# Adjust imports based on your actual python path configuration
try:
    from src.orchestrator.dispatcher import Dispatcher, GeneralChatRequest, SkillExecutionRequest
    from src.orchestrator.skill_loader import SkillLoader
except ImportError:
    from orchestrator.dispatcher import Dispatcher, GeneralChatRequest, SkillExecutionRequest
    from orchestrator.skill_loader import SkillLoader

LOGGER = logging.getLogger(__name__)

router = APIRouter()

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


def get_dispatcher() -> Dispatcher:
    loader = SkillLoader()
    return Dispatcher(loader)


def get_agent_service() -> AgentService:
    settings = get_settings()
    return AgentService(settings)


# --- Endpoints ---


@router.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    dispatcher: Dispatcher = Depends(get_dispatcher),
    agent_service: AgentService = Depends(get_agent_service),
):
    """
    OpenAI-compatible endpoint for Open WebUI.
    Routes requests via the Dispatcher.
    """
    # 1. Extract latest user message
    user_message = ""
    history_messages = []
    for msg in request.messages:
        if msg.role == "user":
            user_message = msg.content
        history_messages.append(AgentMessage(role=msg.role, content=msg.content))

    if not user_message:
        raise HTTPException(status_code=400, detail="No user message found.")

    # Use provided conversation_id or generate new one
    session_id = request.metadata.get("conversation_id") if request.metadata else None
    if not session_id:
        session_id = str(uuid.uuid4())

    # 2. Route Message
    route_result = dispatcher.route_message(session_id, user_message)

    # 3. Execute & Stream
    if request.stream:
        return StreamingResponse(
            stream_response_generator(route_result, request.model, agent_service, history_messages),
            media_type="text/event-stream",
        )
    else:
        # Non-streaming response
        content = ""
        if isinstance(route_result, SkillExecutionRequest):
            # Simple stub execution for skills
            content = (
                f"Executed Skill: {route_result.skill.name}\n" f"Params: {route_result.parameters}"
            )
        elif isinstance(route_result, GeneralChatRequest):
            # Execute Core Agent
            agent_req = AgentRequest(
                prompt=route_result.message,
                conversation_id=route_result.session_id,
                messages=history_messages,
            )
            response = await agent_service.handle_request(agent_req)
            content = response.response

        return {
            "id": f"chatcmpl-{uuid.uuid4()}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        }


async def stream_response_generator(
    route_result, model_name: str, agent_service: AgentService, history: list[AgentMessage]
) -> AsyncGenerator[str, None]:
    """
    Generates SSE events compatible with OpenAI API.
    """
    chunk_id = f"chatcmpl-{uuid.uuid4()}"
    created = int(time.time())
    full_response_text = ""

    try:
        if isinstance(route_result, SkillExecutionRequest):
            # Stub for Skill Execution
            chunks = [
                f"**Skill Detected**: `{route_result.skill.name}`\n\n",
                f"**Description**: {route_result.skill.description}\n",
                f"**Parameters**: `{route_result.parameters}`\n\n",
                "*(Skill execution logic is not yet fully implemented)*",
            ]
            for text in chunks:
                yield _format_chunk(chunk_id, created, model_name, text)
                await asyncio.sleep(0.1)

        elif isinstance(route_result, GeneralChatRequest):
            # Execute Core Agent
            # Note: AgentService is currently blocking/non-streaming, so we wait for full response
            # In a real streaming implementation, handle_request would yield chunks.
            yield _format_chunk(chunk_id, created, model_name, "")  # Send initial chunk to ack

            agent_req = AgentRequest(
                prompt=route_result.message,
                conversation_id=route_result.session_id,
                messages=history,
            )

            # This call waits for the full completion
            response = await agent_service.handle_request(agent_req)
            full_response_text = response.response

            # Simulate streaming the result back
            # We split by words to make it look like it's streaming
            words = full_response_text.split(" ")
            for i, word in enumerate(words):
                # Re-add space that was split away,
                # except for last word maybe?
                # Simpler to just append space to all but last,
                # or just use split keeping delimiters.
                # For simple simulation:
                text_chunk = word + " "
                if i == len(words) - 1:
                    text_chunk = word

                yield _format_chunk(chunk_id, created, model_name, text_chunk)
                # Tiny sleep to simulate token generation
                await asyncio.sleep(0.02)

    except Exception as e:
        LOGGER.error(f"Error during generation: {e}")
        yield _format_chunk(chunk_id, created, model_name, f"\n\nError: {str(e)}")

    # Final chunk
    final_chunk = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_name,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(final_chunk)}\n\n"
    yield "data: [DONE]\n\n"


def _format_chunk(chunk_id, created, model, content):
    data = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
    }
    return f"data: {json.dumps(data)}\n\n"
