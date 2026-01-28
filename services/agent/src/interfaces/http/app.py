"""FastAPI application factory for the agent service."""

from __future__ import annotations

import logging
import traceback
import uuid
from collections.abc import AsyncGenerator, Iterable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from core.core.config import Settings, get_settings
from core.core.litellm_client import LiteLLMClient, LiteLLMError
from core.core.models import (
    AgentMessage,
    AgentRequest,
    AgentResponse,
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    HealthStatus,
)
from core.core.service import AgentService
from core.core.service_factory import ServiceFactory
from core.db.engine import get_db
from core.db.models import Context, Conversation
from core.middleware.rate_limit import create_rate_limiter, rate_limit_exceeded_handler
from core.observability.tracing import configure_tracing
from core.tools.mcp_loader import set_mcp_client_pool, shutdown_all_mcp_clients
from interfaces.http.admin_auth import AuthRedirectError
from interfaces.http.admin_auth_oauth import router as admin_auth_oauth_router
from interfaces.http.admin_contexts import router as admin_contexts_router
from interfaces.http.admin_credentials import router as admin_credentials_router
from interfaces.http.admin_debug import router as admin_debug_router
from interfaces.http.admin_diagnostics import router as admin_diagnostics_router
from interfaces.http.admin_mcp import router as admin_mcp_router
from interfaces.http.admin_oauth import router as admin_oauth_router
from interfaces.http.admin_portal import router as admin_portal_router
from interfaces.http.admin_price_tracker import router as admin_price_tracker_router
from interfaces.http.admin_users import router as admin_users_router
from interfaces.http.admin_workspaces import router as admin_workspaces_router
from interfaces.http.diagnostics import router as diagnostics_router
from interfaces.http.oauth import router as oauth_router
from interfaces.http.oauth_webui import router as oauth_webui_router
from interfaces.http.openwebui_adapter import router as openwebui_router

LOGGER = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, service: AgentService | None = None) -> FastAPI:
    """Initialise the FastAPI application.

    Args:
        settings: Application settings. If None, uses get_settings().
        service: Pre-configured AgentService for testing. If provided,
            legacy endpoints will use this service instead of the factory.
    """

    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)
    configure_tracing(
        settings.app_name,
        span_log_path=str(settings.trace_span_log_path or "data/spans.jsonl"),
    )

    app = FastAPI(title=settings.app_name)

    # Store test service if provided (for legacy endpoint testing)
    if service is not None:
        app.state.test_service = service

    # Configure rate limiting (disabled in test environment)
    if settings.environment != "test":
        limiter = create_rate_limiter()
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
        app.add_middleware(SlowAPIMiddleware)

    # Configure CORS with allowed origins from settings
    allowed_origins = (
        [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]
        if settings.cors_allowed_origins
        else []
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AuthRedirectError)
    async def auth_redirect_handler(request: Request, exc: AuthRedirectError) -> RedirectResponse:
        """Redirect unauthenticated users to login page."""
        return RedirectResponse(url=exc.redirect_url, status_code=302)

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Capture unhandled exceptions and log escape for debugging."""
        timestamp = datetime.now().isoformat()
        trace_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        error_msg = f"[{timestamp}] CRITICAL: Unhandled exception\n{trace_str}\n" + "-" * 80 + "\n"

        # Log to stderr
        LOGGER.exception("Unhandled exception")

        # Record exception to OpenTelemetry span for trace visibility
        span = trace.get_current_span()
        if span.is_recording():
            span.record_exception(exc)
            span.set_attribute("error.type", type(exc).__name__)
            span.set_attribute("error.message", str(exc)[:1000])  # Truncate long messages

        # Write to crash log
        try:
            log_path = Path("data/crash.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(error_msg)
        except Exception as log_exc:
            LOGGER.error(f"Failed to write to crash log: {log_exc}")

        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error. Check crash.log."},
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

    # Create shared LiteLLM client (stateless, safe to share across contexts)
    litellm_client = LiteLLMClient(settings)

    # NOTE: We no longer create a global service instance
    # Instead, we'll create a ServiceFactory in the lifespan that creates
    # context-scoped services per request

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Manage application startup and shutdown lifecycle."""
        import asyncio

        # --- STARTUP ---
        # Dependency Injection: Register module implementations
        # This is the ONLY place where modules are wired to core protocols.
        from core.providers import (
            get_fetcher,
            set_code_indexer_factory,
            set_embedder,
            set_fetcher,
            set_rag_manager,
            set_token_manager,
        )
        from modules.embedder import OpenRouterEmbedder
        from modules.fetcher import WebFetcher
        from modules.indexer import CodeIndexer
        from modules.rag import RAGManager

        # Register providers (dependency injection order matters)
        # 1. Create embedder (OpenRouter API, 4096-dim qwen3-embedding-8b)
        embedder = OpenRouterEmbedder()
        set_embedder(embedder)

        # 2. Create RAG manager with embedder
        rag_manager = RAGManager(embedder=embedder)
        set_rag_manager(rag_manager)

        # 3. Create fetcher with RAG manager
        fetcher = WebFetcher(rag_manager=rag_manager)
        set_fetcher(fetcher)

        # 4. Register indexer factory (receives embedder on instantiation)
        set_code_indexer_factory(CodeIndexer)

        # Register OAuth TokenManager
        from core.auth.token_manager import TokenManager
        from core.db.engine import AsyncSessionLocal

        token_manager = TokenManager(AsyncSessionLocal, settings)
        set_token_manager(token_manager)

        LOGGER.info("Dependency providers registered")

        # Initialize MCP client pool for context-aware MCP connections
        from core.mcp.client_pool import McpClientPool

        mcp_pool = McpClientPool(settings)
        set_mcp_client_pool(mcp_pool)
        LOGGER.info("MCP client pool initialized")

        # Initialize SkillRegistry for skills-native execution
        from core.skills import SkillRegistry
        from core.tools.loader import load_tool_registry

        # Load base tool registry for skill tool validation
        base_tool_registry = load_tool_registry(settings.tools_config_path)
        skill_registry = SkillRegistry(tool_registry=base_tool_registry)
        LOGGER.info(
            "SkillRegistry initialized with %d skills",
            len(skill_registry.available()),
        )

        # Create ServiceFactory for context-aware service creation
        from core.core.service_factory import ServiceFactory

        service_factory = ServiceFactory(
            settings=settings,
            litellm_client=litellm_client,
            skill_registry=skill_registry,
        )
        app.state.service_factory = service_factory

        LOGGER.info("ServiceFactory initialized with SkillRegistry")

        # Warm-up LiteLLM connection in background
        async def warm_up_litellm() -> None:
            try:
                await litellm_client.list_models()
            except Exception:
                LOGGER.warning("LiteLLM warm-up failed (non-critical)")

        asyncio.create_task(warm_up_litellm())

        # Database retention cleanup - runs on startup and daily
        async def retention_cleanup_loop() -> None:
            """Run retention cleanup on startup, then daily."""
            from core.db.engine import AsyncSessionLocal
            from core.db.retention import run_retention_cleanup

            # Initial cleanup on startup (after short delay)
            await asyncio.sleep(30)  # Wait for DB to be fully ready

            while True:
                try:
                    async with AsyncSessionLocal() as session:
                        results = await run_retention_cleanup(session)
                        LOGGER.info(f"Daily retention cleanup: {results}")
                except Exception as e:
                    LOGGER.error(f"Retention cleanup failed: {e}")

                # Sleep for 24 hours
                await asyncio.sleep(24 * 60 * 60)

        asyncio.create_task(retention_cleanup_loop())
        LOGGER.info("Retention cleanup scheduled (startup + daily)")

        # Email Service - platform-wide email capability
        from core.providers import set_email_service
        from modules.email.service import EmailConfig, ResendEmailService

        email_service = None
        if settings.resend_api_key:
            email_config = EmailConfig(
                api_key=settings.resend_api_key,
                from_email=settings.email_from_address,
            )
            email_service = ResendEmailService(email_config)
            set_email_service(email_service)
            LOGGER.info("Email service initialized")

        # Price Tracker Scheduler - runs background price checks
        from modules.price_tracker.scheduler import PriceCheckScheduler

        # Create and start scheduler (pass email service directly)
        scheduler = PriceCheckScheduler(
            session_factory=AsyncSessionLocal,
            fetcher=get_fetcher(),
            email_service=email_service,
        )
        await scheduler.start()
        LOGGER.info("Price check scheduler started")

        # Homey Device Sync Scheduler - nightly cache refresh
        from modules.homey.scheduler import HomeyDeviceSyncScheduler

        homey_scheduler = HomeyDeviceSyncScheduler(
            session_factory=AsyncSessionLocal,
        )
        await homey_scheduler.start()
        LOGGER.info("Homey device sync scheduler started")

        yield  # Application runs here

        # --- SHUTDOWN ---
        await homey_scheduler.stop()
        await scheduler.stop()
        # Clean up email service
        if email_service is not None:
            await email_service.close()
        await shutdown_all_mcp_clients()
        await litellm_client.aclose()
        await token_manager.shutdown()
        await embedder.close()

    # Assign lifespan to app
    app.router.lifespan_context = lifespan

    def get_service_factory() -> ServiceFactory:
        """Return the service factory from app state."""
        return app.state.service_factory

    async def get_service(
        request: Request,
        session: AsyncSession = Depends(get_db),
    ) -> AgentService:
        """Get AgentService for legacy endpoints.

        For production: Creates a service using the factory with a default context.
        For testing: Returns the pre-injected test service if available.
        """
        # Check for test service first (avoids needing service_factory in tests)
        if (
            hasattr(request.app.state, "test_service")
            and request.app.state.test_service is not None
        ):
            return request.app.state.test_service

        # Production: Get factory and create service with default "api" context
        factory: ServiceFactory = request.app.state.service_factory

        from sqlalchemy import select

        # Look up or create default API context
        stmt = select(Context).where(Context.name == "default_api")
        result = await session.execute(stmt)
        context = result.scalar_one_or_none()

        if not context:
            context = Context(
                name="default_api",
                type="virtual",
                config={},
                default_cwd="/tmp",  # noqa: S108
            )
            session.add(context)
            await session.flush()

        return await factory.create_service(context.id, session)

    @app.get("/healthz", response_model=HealthStatus)
    async def health() -> HealthStatus:  # pragma: no cover - trivial endpoint
        return HealthStatus(status="ok")

    @app.post("/v1/agent", response_model=AgentResponse)
    async def run_agent(
        request: AgentRequest,
        factory: ServiceFactory = Depends(get_service_factory),
        session: AsyncSession = Depends(get_db),
    ) -> AgentResponse:
        try:
            # Extract or create context_id from conversation_id
            from uuid import UUID

            context_id: UUID
            if request.conversation_id:
                try:
                    conversation_uuid = UUID(request.conversation_id)
                    # Look up conversation to get context_id
                    from sqlalchemy import select

                    stmt = select(Conversation).where(Conversation.id == conversation_uuid)
                    result = await session.execute(stmt)
                    conversation = result.scalar_one_or_none()

                    if conversation:
                        context_id = conversation.context_id
                    else:
                        # Create default context for new conversation
                        context = Context(
                            name=f"agent_{conversation_uuid}",
                            type="virtual",
                            config={},
                            default_cwd="/tmp",  # noqa: S108
                        )
                        session.add(context)
                        await session.flush()
                        context_id = context.id
                except ValueError:
                    # Invalid UUID - create default context
                    context = Context(
                        name=f"agent_{uuid.uuid4()}",
                        type="virtual",
                        config={},
                        default_cwd="/tmp",  # noqa: S108
                    )
                    session.add(context)
                    await session.flush()
                    context_id = context.id
            else:
                # No conversation_id - create default context
                context = Context(
                    name=f"agent_{uuid.uuid4()}",
                    type="virtual",
                    config={},
                    default_cwd="/tmp",  # noqa: S108
                )
                session.add(context)
                await session.flush()
                context_id = context.id

            # Create context-scoped service
            svc = await factory.create_service(context_id, session)

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
    app.include_router(oauth_router)
    app.include_router(oauth_webui_router)

    # Admin routers (secured with Entra ID headers or JWT)
    app.include_router(admin_auth_oauth_router)  # OAuth endpoints first
    app.include_router(admin_portal_router)
    app.include_router(admin_contexts_router)
    app.include_router(admin_workspaces_router)
    app.include_router(admin_credentials_router)
    app.include_router(admin_oauth_router)
    app.include_router(admin_mcp_router)
    app.include_router(admin_diagnostics_router)
    app.include_router(admin_debug_router)
    app.include_router(admin_price_tracker_router)
    app.include_router(admin_users_router)

    return app


def run() -> None:  # pragma: no cover - used by Poetry script
    """Run the application via ``poetry run agent-app``."""

    settings = get_settings()
    import uvicorn

    uvicorn.run(
        "interfaces.http.app:create_app",
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
