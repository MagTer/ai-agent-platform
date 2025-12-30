"""FastAPI application factory for the agent service."""

from __future__ import annotations

import logging
import traceback
import uuid
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.observability.tracing import configure_tracing
from interfaces.http.diagnostics import router as diagnostics_router
from interfaces.http.openwebui_adapter import router as openwebui_router

from ..tools.loader import load_tool_registry
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

LOGGER = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, service: AgentService | None = None) -> FastAPI:
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

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Capture unhandled exceptions and log escape for debugging."""
        timestamp = datetime.now().isoformat()
        trace_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        error_msg = f"[{timestamp}] CRITICAL: Unhandled exception\n{trace_str}\n" + "-" * 80 + "\n"

        # Log to stderr
        LOGGER.exception("Unhandled exception")

        # Write to crash log
        try:
            log_path = Path("services/agent/last_crash.log")
            # Ensure directory exists? Usually services/agent exists.
            # Append mode
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(error_msg)
        except Exception as log_exc:
            LOGGER.error(f"Failed to write to crash log: {log_exc}")

        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error. Check last_crash.log."},
        )

    @app.middleware("http")
    async def capture_request_response_middleware(request: Request, call_next: Any) -> Any:
        span = trace.get_current_span()
        if not span.is_recording():
            return await call_next(request)

        # Set a meaningful name for the trace in the UI
        path = request.url.path
        method = request.method
        if path.startswith("/v1/chat/completions"):
            span.update_name(f"Agent Chat: {method} {path}")
        elif path.startswith("/v1/agent"):
            span.update_name(f"Agent Task: {method} {path}")
        else:
            span.update_name(f"API: {method} {path}")

        skip_body = path.startswith(
            (
                "/diagnostics",
                "/health",
                "/metrics",
                "/v1/agent/history",
                "/v1/chat/completions",
            )
        )

        if not skip_body:
            # Capture request body
            try:
                body = await request.body()
                if body:
                    text = body.decode("utf-8", errors="replace")
                    span.set_attribute("http.request.body", text[:2000])

                # Re-seed the body for downstream consumers
                async def receive() -> dict[str, Any]:
                    return {"type": "http.request", "body": body, "more_body": False}

                request._receive = receive
            except Exception:
                LOGGER.warning("Failed to capture request body", exc_info=True)

        response = await call_next(request)

        if skip_body:
            return response

        # Capture response body
        # Note: This reads streaming responses which might have performance impact
        try:
            if hasattr(response, "body_iterator"):
                original_iterator = response.body_iterator
                chunks = []

                async def response_stream_wrapper() -> Any:
                    async for chunk in original_iterator:
                        if isinstance(chunk, bytes):
                            chunks.append(chunk)
                        yield chunk

                    # After stream is consumed
                    if chunks:
                        full_body = b"".join(chunks).decode("utf-8", errors="replace")
                        span.set_attribute("http.response.body", full_body[:2000])

                response.body_iterator = response_stream_wrapper()
            elif hasattr(response, "body"):
                text_body = response.body.decode("utf-8", errors="replace")
                span.set_attribute(
                    "http.response.body",
                    text_body[:2000],
                )
        except Exception:
            LOGGER.warning("Failed to capture response body", exc_info=True)

        return response

    FastAPIInstrumentor.instrument_app(app, excluded_urls="/healthz,/readyz")

    litellm_client = LiteLLMClient(settings)
    memory_store = MemoryStore(settings)
    # Correcting duplicate lines from original if they were present
    # litellm_client = LiteLLMClient(settings)
    # memory_store = MemoryStore(settings)

    # Load native tools from configuration
    tool_registry = load_tool_registry(settings.tools_config_path)

    # Inject Orchestration Tools
    from core.tools.skill_delegate import SkillDelegateTool

    delegate_tool = SkillDelegateTool(litellm_client, tool_registry)
    tool_registry.register(delegate_tool)

    service_instance = service or AgentService(
        settings=settings,
        litellm=litellm_client,
        memory=memory_store,
        tool_registry=tool_registry,
    )

    @app.on_event("startup")
    async def _startup_mcp_tools() -> None:
        """Load MCP tools and register providers on application startup."""
        import asyncio

        # --- Dependency Injection: Register module implementations ---
        # This is the ONLY place where modules are wired to core protocols.
        # Core tools use providers, modules implement protocols.
        from core.providers import (
            set_code_indexer_factory,
            set_embedder,
            set_fetcher,
            set_rag_manager,
        )
        from modules.embedder import get_embedder as get_module_embedder
        from modules.fetcher import get_fetcher as get_module_fetcher
        from modules.indexer import CodeIndexer
        from modules.rag import RAGManager

        # Register providers
        set_embedder(get_module_embedder())
        set_fetcher(get_module_fetcher())
        set_rag_manager(RAGManager())
        set_code_indexer_factory(CodeIndexer)

        LOGGER.info("Dependency providers registered")

        # Initialize memory store (connect to Qdrant)
        await memory_store.ainit()

        # Run MCP loading in the background so it doesn't block startup
        asyncio.create_task(load_mcp_tools(settings, service_instance._tool_registry))

        # Warm-up LiteLLM connection in background
        async def warm_up_litellm() -> None:
            try:
                await litellm_client.list_models()
            except Exception:
                LOGGER.warning("LiteLLM warm-up failed (non-critical)")

        asyncio.create_task(warm_up_litellm())

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await litellm_client.aclose()

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
        session: AsyncSession = Depends(get_db),
    ) -> AgentResponse:
        try:
            return await svc.handle_request(request, session=session)
        except LiteLLMError as exc:  # pragma: no cover - upstream failure
            LOGGER.error("LiteLLM gateway error: %s", exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:  # pragma: no cover - defensive branch
            LOGGER.exception("Agent processing failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    async def _handle_chat_completions(
        request: ChatCompletionRequest,
        svc: AgentService,
        session: AsyncSession,
    ) -> ChatCompletionResponse:
        try:
            agent_request = _build_agent_request_from_chat(request)
        except ValueError as exc:  # pragma: no cover - defensive validation
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            response = await svc.handle_request(agent_request, session=session)
        except LiteLLMError as exc:
            LOGGER.error("LiteLLM gateway error: %s", exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:  # pragma: no cover - defensive branch
            LOGGER.exception("Agent processing failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
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

    @app.post("/v1/agent/chat/completions", response_model=ChatCompletionResponse)
    async def chat_completions(
        request: ChatCompletionRequest,
        svc: AgentService = Depends(get_service),
        session: AsyncSession = Depends(get_db),
    ) -> ChatCompletionResponse:
        return await _handle_chat_completions(request, svc, session)

    @app.post("/chat/completions", response_model=ChatCompletionResponse)
    async def legacy_chat_completions(
        request: ChatCompletionRequest,
        svc: AgentService = Depends(get_service),
        session: AsyncSession = Depends(get_db),
    ) -> ChatCompletionResponse:
        return await _handle_chat_completions(request, svc, session)

    @app.get("/models")
    async def list_models(svc: AgentService = Depends(get_service)) -> Any:
        try:
            return await svc.list_models()
        except LiteLLMError as exc:  # pragma: no cover - upstream failure
            LOGGER.error("LiteLLM gateway error: %s", exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/v1/models")
    async def list_models_v1(svc: AgentService = Depends(get_service)) -> Any:
        return await list_models(svc)

    @app.get("/v1/agent/history/{conversation_id}", response_model=list[AgentMessage])
    async def get_history(
        conversation_id: str,
        svc: AgentService = Depends(get_service),
        session: AsyncSession = Depends(get_db),
    ) -> list[AgentMessage]:
        try:
            return await svc.get_history(conversation_id, session=session)
        except Exception as exc:
            LOGGER.exception(f"Failed to fetch history for {conversation_id}")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    app.include_router(openwebui_router)
    app.include_router(diagnostics_router)
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
