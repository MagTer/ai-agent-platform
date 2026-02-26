"""Agent API router: /v1/agent, /v1/agent/chat/completions, /models endpoints."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from shared.sanitize import sanitize_log
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.runtime.litellm_client import LiteLLMError
from core.runtime.models import (
    AgentMessage,
    AgentRequest,
    AgentResponse,
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
)
from core.runtime.service import AgentService
from core.runtime.service_factory import ServiceFactory
from interfaces.http.dependencies import get_service, get_service_factory, verify_agent_api_key

LOGGER = logging.getLogger(__name__)

router = APIRouter()


@router.post("/v1/agent", response_model=AgentResponse)
async def run_agent(
    request: AgentRequest,
    factory: ServiceFactory = Depends(get_service_factory),
    session: AsyncSession = Depends(get_db),
    _auth: None = Depends(verify_agent_api_key),
) -> AgentResponse:
    try:
        from core.context import ContextService

        if request.conversation_id:
            context_id = await ContextService.resolve_for_conversation_id(
                request.conversation_id, "agent", session
            )
        else:
            context_id = await ContextService.resolve_anonymous("agent", session)

        # Create context-scoped service
        svc = await factory.create_service(context_id, session)

        return await svc.handle_request(request, session=session)
    except LiteLLMError as exc:  # pragma: no cover - upstream failure
        LOGGER.error("LiteLLM gateway error: %s", exc)
        raise HTTPException(
            status_code=502, detail="Upstream service temporarily unavailable"
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive branch
        LOGGER.exception("Agent processing failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc


async def _handle_chat_completions(
    request: ChatCompletionRequest,
    svc: AgentService,
    session: AsyncSession,
) -> ChatCompletionResponse:
    try:
        agent_request = _build_agent_request_from_chat(request)
    except ValueError as exc:  # pragma: no cover - defensive validation
        raise HTTPException(status_code=400, detail="Invalid request format") from exc

    try:
        response = await svc.handle_request(agent_request, session=session)
    except LiteLLMError as exc:
        LOGGER.error("LiteLLM gateway error: %s", exc)
        raise HTTPException(
            status_code=502, detail="Upstream service temporarily unavailable"
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive branch
        LOGGER.exception("Agent processing failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
    message_metadata = dict(response.metadata or {})
    message_metadata["steps"] = response.steps
    choice = ChatCompletionChoice(
        index=0,
        finish_reason="stop",
        message={
            "role": "assistant",
            "content": response.response,
            "metadata": message_metadata,
        },
    )
    return ChatCompletionResponse(
        id=response.conversation_id,
        created=int(response.created_at.timestamp()) if response.created_at else 0,
        model=request.model,
        choices=[choice],
        steps=response.steps,
        metadata=message_metadata,
    )


@router.post("/v1/agent/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    request: ChatCompletionRequest,
    svc: AgentService = Depends(get_service),
    session: AsyncSession = Depends(get_db),
    _auth: None = Depends(verify_agent_api_key),
) -> ChatCompletionResponse:
    return await _handle_chat_completions(request, svc, session)


@router.post("/chat/completions", response_model=ChatCompletionResponse)
async def legacy_chat_completions(
    request: ChatCompletionRequest,
    svc: AgentService = Depends(get_service),
    session: AsyncSession = Depends(get_db),
    _auth: None = Depends(verify_agent_api_key),
) -> ChatCompletionResponse:
    return await _handle_chat_completions(request, svc, session)


@router.get("/models")
async def list_models(
    svc: AgentService = Depends(get_service),
    _auth: None = Depends(verify_agent_api_key),
) -> Any:
    try:
        return await svc.list_models()
    except LiteLLMError as exc:  # pragma: no cover - upstream failure
        LOGGER.error("LiteLLM gateway error: %s", exc)
        raise HTTPException(
            status_code=502, detail="Upstream service temporarily unavailable"
        ) from exc


@router.get("/v1/models")
async def list_models_v1(
    svc: AgentService = Depends(get_service),
    _auth: None = Depends(verify_agent_api_key),
) -> Any:
    return await list_models(svc)


@router.get("/v1/agent/history/{conversation_id}", response_model=list[AgentMessage])
async def get_history(
    conversation_id: str,
    svc: AgentService = Depends(get_service),
    session: AsyncSession = Depends(get_db),
    _auth: None = Depends(verify_agent_api_key),
) -> list[AgentMessage]:
    try:
        return await svc.get_history(conversation_id, session=session)
    except Exception as exc:
        LOGGER.exception("Failed to fetch history for %s", sanitize_log(conversation_id))
        raise HTTPException(
            status_code=500, detail="Failed to retrieve conversation history"
        ) from exc


def _build_agent_request_from_chat(request: ChatCompletionRequest) -> AgentRequest:
    """Translate an OpenAI-style request into an :class:`AgentRequest`."""

    if not request.messages:
        raise ValueError("messages list cannot be empty")

    chat_messages: list[AgentMessage] = [message.to_agent_message() for message in request.messages]
    prompt_index = _last_user_index(chat_messages)
    if prompt_index is None:
        raise ValueError("at least one user message is required")

    prompt_message = chat_messages[prompt_index]
    history: list[AgentMessage] = [
        message for idx, message in enumerate(chat_messages) if idx != prompt_index
    ]

    metadata = dict(request.metadata or {})
    conversation_id = (
        request.conversation_id
        or metadata.get("conversation_id")
        or _derive_conversation_id(chat_messages)
    )

    return AgentRequest(
        prompt=prompt_message.content or "",
        conversation_id=conversation_id,
        metadata=metadata or None,
        messages=history or None,
    )


def _last_user_index(messages: Iterable[AgentMessage]) -> int | None:
    """Return the index of the last user message if present."""

    index: int | None = None
    for idx, message in enumerate(messages):
        if message.role == "user":
            index = idx
    return index


def _derive_conversation_id(messages: Iterable[AgentMessage]) -> str:
    """Create a random unique conversation identifier."""
    return str(uuid.uuid4())


__all__ = ["router"]
