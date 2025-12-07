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

from core.core.config import Settings, get_settings
from core.core.litellm_client import LiteLLMClient
from core.core.memory import MemoryStore
from core.core.service import AgentService
from core.tools import ToolRegistry
from orchestrator.dispatcher import Dispatcher, DispatchResult
from orchestrator.skill_loader import SkillLoader
from orchestrator.utils import render_skill_prompt
from shared.models import AgentMessage, AgentRequest

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
    tool_registry = ToolRegistry([])  # Provide an empty ToolRegistry for now
    return AgentService(settings, litellm, memory, tool_registry=tool_registry)


# --- Endpoints ---


@router.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    dispatcher: Dispatcher = Depends(get_dispatcher),
    agent_service: AgentService = Depends(get_agent_service),
) -> Any:
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
    dispatch_result = await dispatcher.route_message(session_id, user_message)

    # 3. Execute & Stream
    if request.stream:
        return StreamingResponse(
            stream_response_generator(
                dispatch_result,
                request.model,
                agent_service,
                history_messages,
                dispatcher,
            ),
            media_type="text/event-stream",
        )
    else:
        # Non-streaming response handling
        # This path is less critical for the current refactor focus but should be updated
        # to support the Plan injection if present.
        # For now, let's focus on streaming as that's what OpenWebUI uses.

        # We will mock a quick response for non-streaming if needed or reuse the logic.
        # For simplicity, we call handle_request appropriately.

        agent_req = AgentRequest(
            prompt=user_message,
            conversation_id=session_id,
            messages=history_messages,
        )

        if dispatch_result.plan:
            # Inject the plan into metadata so AgentService picks it up
            if not agent_req.metadata:
                agent_req.metadata = {}
            agent_req.metadata["plan"] = dispatch_result.plan.model_dump()

        elif dispatch_result.skill_name:
            # Logic for skill: render prompt and replace
            skill = dispatcher.skill_loader.skills.get(dispatch_result.skill_name)
            if skill:
                # We need to parse params again or pass them in DispatchResult
                # Simplification: pass raw args if DispatchResult held them
                # For now, re-parse or ignore arguments for this legacy path
                agent_req.prompt = render_skill_prompt(skill, {})

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
    dispatch_result: DispatchResult,
    model_name: str,
    agent_service: AgentService,
    history: list[AgentMessage],
    dispatcher: Dispatcher,
) -> AsyncGenerator[str, None]:
    """
    Generates SSE events compatible with OpenAI API.
    """
    chunk_id = f"chatcmpl-{uuid.uuid4()}"
    created = int(time.time())

    try:
        yield _format_chunk(chunk_id, created, model_name, "")  # Initial ack

        agent_req = AgentRequest(
            prompt=dispatch_result.original_message,
            conversation_id=None,  # Will be generated if None, or we could pass it
            messages=history,
            metadata={"routing_decision": dispatch_result.decision},
        )

        # Handle Fast Path (Plan exists)
        if dispatch_result.plan:
            yield _format_chunk(
                chunk_id, created, model_name, "**Fast Path Active**\n\n"
            )
            if agent_req.metadata is None:
                agent_req.metadata = {}
            agent_req.metadata["plan"] = dispatch_result.plan.model_dump()

        # Handle Legacy Skill
        elif dispatch_result.skill_name:
            skill = dispatcher.skill_loader.skills.get(dispatch_result.skill_name)
            if skill:
                # Naive param parsing or empty
                system_prompt = render_skill_prompt(skill, {})
                agent_req.prompt = system_prompt
                yield _format_chunk(
                    chunk_id,
                    created,
                    model_name,
                    f"**Executing Skill**: {skill.name}\n\n",
                )

        # Execute
        # AgentService.handle_request will use the injected plan if present
        response = await agent_service.handle_request(agent_req)
        full_response_text = response.response

        # Simulate streaming
        words = full_response_text.split(" ")
        for i, word in enumerate(words):
            text_chunk = word + " " if i < len(words) - 1 else word
            yield _format_chunk(chunk_id, created, model_name, text_chunk)
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


def _format_chunk(chunk_id: str, created: int, model: str, content: str) -> str:
    data = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
    }
    return f"data: {json.dumps(data)}\n\n"
