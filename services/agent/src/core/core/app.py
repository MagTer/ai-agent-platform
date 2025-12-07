"""FastAPI application factory for the agent service."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from core.observability.tracing import configure_tracing
from interfaces.http.openwebui_adapter import router as openwebui_router

from ..tools.mcp_loader import load_mcp_tools
from .config import Settings, get_settings
from .litellm_client import LiteLLMClient, LiteLLMError
from .memory import MemoryStore
from .models import (
    AgentMessage,
    AgentRequest,
    AgentResponse,
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    HealthStatus,
)
from .service import AgentService
from .state import (
    StateStore,
)  # Make sure StateStore is also imported if used in AgentService

LOGGER = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None, service: AgentService | None = None
) -> FastAPI:
    """Initialise the FastAPI application."""

    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)
    configure_tracing(
        settings.app_name,
        span_log_path=str(settings.trace_span_log_path or "data/spans.jsonl"),
    )

    app = FastAPI(title=settings.app_name)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    litellm_client = LiteLLMClient(settings)
    memory_store = MemoryStore(settings)
    state_store = StateStore(settings.sqlite_state_path)  # Instantiate StateStore

    service_instance = service or AgentService(
        settings=settings,
        litellm=litellm_client,
        memory=memory_store,
        state_store=state_store,  # Pass StateStore
    )

    @app.on_event("startup")
    async def _startup_mcp_tools() -> None:
        """Load MCP tools on application startup."""
        import asyncio

        # Run MCP loading in the background so it doesn't block startup
        asyncio.create_task(load_mcp_tools(settings, service_instance._tool_registry))

    def get_service() -> AgentService:
        """Return a singleton agent service instance."""

        return service_instance

    @app.get("/healthz", response_model=HealthStatus)
    async def health() -> HealthStatus:  # pragma: no cover - trivial endpoint
        return HealthStatus(status="ok")

    @app.post("/v1/agent", response_model=AgentResponse)
    async def run_agent(
        request: AgentRequest,
        svc: AgentService = Depends(get_service),
    ) -> AgentResponse:
        try:
            return await svc.handle_request(request)
        except LiteLLMError as exc:  # pragma: no cover - upstream failure
            LOGGER.error("LiteLLM gateway error: %s", exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:  # pragma: no cover - defensive branch
            LOGGER.exception("Agent processing failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    async def _handle_chat_completions(
        request: ChatCompletionRequest,
        svc: AgentService,
    ) -> ChatCompletionResponse:
        try:
            agent_request = _build_agent_request_from_chat(request)
        except ValueError as exc:  # pragma: no cover - defensive validation
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            response = await svc.handle_request(agent_request)
        except LiteLLMError as exc:
            LOGGER.error("LiteLLM gateway error: %s", exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:  # pragma: no cover - defensive branch
            LOGGER.exception("Agent processing failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        message_metadata = dict(response.metadata)
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
            created=int(response.created_at.timestamp()),
            model=request.model,
            choices=[choice],
            steps=response.steps,
            metadata=message_metadata,
        )

    @app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
    async def chat_completions(
        request: ChatCompletionRequest,
        svc: AgentService = Depends(get_service),
    ) -> ChatCompletionResponse:
        return await _handle_chat_completions(request, svc)

    @app.post("/chat/completions", response_model=ChatCompletionResponse)
    async def legacy_chat_completions(
        request: ChatCompletionRequest,
        svc: AgentService = Depends(get_service),
    ) -> ChatCompletionResponse:
        return await _handle_chat_completions(request, svc)

    @app.get("/models")
    async def list_models(svc: AgentService = Depends(get_service)) -> Any:
        try:
            return await svc.list_models()
        except LiteLLMError as exc:  # pragma: no cover - upstream failure
            LOGGER.error("LiteLLM gateway error: %s", exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    app.include_router(openwebui_router)
    return app


def run() -> None:  # pragma: no cover - used by Poetry script
    """Run the application via ``poetry run agent-app``."""

    settings = get_settings()
    import uvicorn

    uvicorn.run(
        "core.core.app:create_app",
        host=settings.host,
        port=settings.port,
        factory=True,
        reload=settings.environment == "development",
    )


__all__ = ["create_app", "run"]


def _build_agent_request_from_chat(request: ChatCompletionRequest) -> AgentRequest:
    """Translate an OpenAI-style request into an :class:`AgentRequest`."""

    if not request.messages:
        raise ValueError("messages list cannot be empty")

    chat_messages: list[AgentMessage] = [
        message.to_agent_message() for message in request.messages
    ]
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
        prompt=prompt_message.content,
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
    """Create a deterministic conversation identifier."""

    for message in messages:
        if message.role == "user" and message.content:
            digest = hashlib.sha256(message.content.encode("utf-8")).hexdigest()
            return f"conv-{digest}"
    return f"conv-{hashlib.sha256(b'agent').hexdigest()}"
