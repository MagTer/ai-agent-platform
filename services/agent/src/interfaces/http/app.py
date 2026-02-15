"""FastAPI application factory for the agent service."""

from __future__ import annotations

import logging
import secrets
import sys
import time
import traceback
import uuid
from collections.abc import AsyncGenerator, Iterable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from shared.sanitize import sanitize_log
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.db.models import Context
from core.middleware.rate_limit import create_rate_limiter, rate_limit_exceeded_handler
from core.observability.debug_logger import configure_debug_log_handler
from core.observability.logging import setup_logging, setup_otel_log_bridge
from core.observability.metrics import configure_metrics
from core.observability.tracing import configure_tracing
from core.runtime.config import Settings, get_settings
from core.runtime.litellm_client import LiteLLMClient, LiteLLMError
from core.runtime.models import (
    AgentMessage,
    AgentRequest,
    AgentResponse,
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    HealthStatus,
)
from core.runtime.service import AgentService
from core.runtime.service_factory import ServiceFactory
from core.tools.mcp_loader import set_mcp_client_pool
from interfaces.http.admin_api import router as admin_api_router
from interfaces.http.admin_auth import AuthRedirectError
from interfaces.http.admin_auth_oauth import router as admin_auth_oauth_router
from interfaces.http.admin_contexts import router as admin_contexts_router
from interfaces.http.admin_diagnostics import router as admin_diagnostics_router
from interfaces.http.admin_mcp import router as admin_mcp_router
from interfaces.http.admin_oauth import router as admin_oauth_router
from interfaces.http.admin_permissions import router as admin_permissions_router
from interfaces.http.admin_portal import router as admin_portal_router
from interfaces.http.admin_price_tracker import router as admin_price_tracker_router
from interfaces.http.admin_scheduler import router as admin_scheduler_router
from interfaces.http.admin_users import router as admin_users_router
from interfaces.http.admin_workspaces import router as admin_workspaces_router
from interfaces.http.oauth import router as oauth_router
from interfaces.http.oauth_webui import router as oauth_webui_router
from interfaces.http.openwebui_adapter import router as openwebui_router

LOGGER = logging.getLogger(__name__)

# Shared httpx client for readiness checks (reuse across requests)
_READINESS_HTTP_CLIENT: httpx.AsyncClient | None = None


def verify_internal_api_key(
    authorization: str | None,
    x_api_key: str | None,
    settings: Settings,
) -> None:
    """Verify internal API key for agent endpoints.

    Checks for API key in Authorization: Bearer <key> OR X-API-Key: <key> header.
    If AGENT_INTERNAL_API_KEY is not set, SKIP auth (dev convenience).

    Args:
        authorization: Authorization header value
        x_api_key: X-API-Key header value
        settings: Application settings

    Raises:
        HTTPException 401: If key is required but invalid or missing
    """
    # If internal_api_key is not set, block in production, warn in dev
    if not settings.internal_api_key:
        if settings.environment == "production":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="AGENT_INTERNAL_API_KEY must be set in production",
            )
        if settings.environment != "test":
            LOGGER.warning(
                "AGENT_INTERNAL_API_KEY not set - agent API endpoints are UNAUTHENTICATED. "
                "Set AGENT_INTERNAL_API_KEY in production for security."
            )
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


def create_app(settings: Settings | None = None, service: AgentService | None = None) -> FastAPI:
    """Initialise the FastAPI application.

    Args:
        settings: Application settings. If None, uses get_settings().
        service: Pre-configured AgentService for testing. If provided,
            legacy endpoints will use this service instead of the factory.
    """

    settings = settings or get_settings()
    setup_logging(level=settings.log_level, log_to_file="pytest" not in sys.modules)
    configure_tracing(
        settings.app_name,
        span_log_path=str(settings.trace_span_log_path or "data/spans.jsonl"),
        span_log_max_size_mb=settings.trace_span_log_max_size_mb,
        span_log_max_files=settings.trace_span_log_max_files,
    )
    configure_metrics(settings.app_name)
    setup_otel_log_bridge(settings.app_name)
    configure_debug_log_handler()

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

        # Write to crash log asynchronously
        try:
            import asyncio

            log_path = Path("data/crash.log")

            def _write_crash_log() -> None:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(error_msg)

            await asyncio.to_thread(_write_crash_log)
        except Exception as log_exc:
            LOGGER.error(f"Failed to write to crash log: {log_exc}")

        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error"},
        )

    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next: Any) -> Any:
        """Add security headers to all responses."""
        response = await call_next(request)

        # Standard security headers for all responses
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'self'"
        )

        # X-Frame-Options: SAMEORIGIN for admin portal, DENY for everything else
        if request.url.path.startswith("/platformadmin/"):
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        else:
            response.headers["X-Frame-Options"] = "DENY"

        # HSTS only in production
        if settings.environment == "production":
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"

        return response

    @app.middleware("http")
    async def csrf_middleware(request: Request, call_next: Any) -> Any:
        """CSRF protection middleware for admin portal endpoints."""
        # Only apply to /platformadmin/ endpoints
        if not request.url.path.startswith("/platformadmin/"):
            return await call_next(request)

        # Skip for GET, HEAD, OPTIONS (safe methods)
        if request.method in ("GET", "HEAD", "OPTIONS"):
            # Set CSRF cookie on GET requests
            response = await call_next(request)

            # Only set cookie if admin_jwt_secret is configured
            if settings.admin_jwt_secret and settings.environment != "test":
                from interfaces.http.csrf import (
                    CSRF_COOKIE_NAME,
                    generate_csrf_token,
                    set_csrf_cookie,
                )

                # Check if cookie already exists
                existing_cookie = request.cookies.get(CSRF_COOKIE_NAME)
                if not existing_cookie:
                    # Generate new token and set cookie
                    token = generate_csrf_token(settings.admin_jwt_secret)
                    set_csrf_cookie(response, token, secure=(settings.environment == "production"))
                    LOGGER.debug("CSRF cookie set for new session")

            return response

        # POST, DELETE, PUT, PATCH require CSRF validation
        # (The validation is handled by the require_csrf dependency in endpoints)
        return await call_next(request)

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
                "/v1/agent",
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

        # Capture response body (only first 2000 bytes to avoid memory bloat)
        try:
            if hasattr(response, "body_iterator"):
                original_iterator = response.body_iterator
                preview_chunks: list[bytes] = []
                preview_bytes_collected = 0
                preview_limit = 2000

                async def response_stream_wrapper() -> Any:
                    nonlocal preview_bytes_collected
                    async for chunk in original_iterator:
                        if isinstance(chunk, bytes) and preview_bytes_collected < preview_limit:
                            remaining = preview_limit - preview_bytes_collected
                            preview_chunks.append(chunk[:remaining])
                            preview_bytes_collected += min(len(chunk), remaining)
                        yield chunk

                    # After stream is consumed - only preview bytes in memory
                    if preview_chunks:
                        preview = b"".join(preview_chunks).decode("utf-8", errors="replace")
                        span.set_attribute("http.response.body", preview[:2000])

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

    @app.middleware("http")
    async def request_metrics_middleware(request: Request, call_next: Any) -> Any:
        """Track request timing and log slow requests."""
        start_time = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000

        # Add timing to response headers
        response.headers["X-Response-Time"] = f"{duration_ms:.1f}ms"

        # Record in OpenTelemetry span
        span = trace.get_current_span()
        if span.is_recording():
            span.set_attribute("http.request.duration_ms", round(duration_ms, 1))
            span.set_attribute("http.route", request.url.path)

        # Record OTel metrics for agent API endpoints
        if request.url.path.startswith(("/v1/agent", "/v1/chat/completions", "/chat/completions")):
            from core.observability.metrics import request_counter, request_duration_histogram

            status_str = "error" if response.status_code >= 400 else "ok"
            attrs = {"http.route": request.url.path, "status": status_str}
            request_counter.add(1, attributes=attrs)
            request_duration_histogram.record(duration_ms, attributes=attrs)

        # Log slow requests (> 5 seconds)
        if duration_ms > 5000:
            LOGGER.warning(
                "Slow request: %s %s took %.1fms",
                request.method,
                request.url.path,
                duration_ms,
            )

        return response

    FastAPIInstrumentor.instrument_app(app, excluded_urls="/healthz,/readyz")

    # Create shared LiteLLM client (stateless, safe to share across contexts)
    litellm_client = LiteLLMClient(settings)
    # Store in app.state for access by openwebui_adapter and other components
    app.state.litellm_client = litellm_client

    # NOTE: We no longer create a global service instance
    # Instead, we'll create a ServiceFactory in the lifespan that creates
    # context-scoped services per request

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Manage application startup and shutdown lifecycle."""
        import asyncio

        global _READINESS_HTTP_CLIENT

        # --- STARTUP ---
        # Initialize shared HTTP client for readiness checks
        _READINESS_HTTP_CLIENT = httpx.AsyncClient(timeout=3.0)

        # Dependency Injection: Register module implementations via orchestrator
        from core.db.engine import AsyncSessionLocal
        from orchestrator.startup import create_email_service, register_providers, start_schedulers

        token_manager = await register_providers(settings, litellm_client)

        # Initialize model capability registry
        from core.runtime.model_registry import ModelCapabilityRegistry

        ModelCapabilityRegistry.get_instance()
        LOGGER.info("Model capability registry initialized")

        # Initialize MCP client pool for context-aware MCP connections
        from core.mcp.client_pool import McpClientPool

        mcp_pool = McpClientPool(settings)
        set_mcp_client_pool(mcp_pool)
        mcp_pool.start_eviction()
        LOGGER.info("MCP client pool initialized with background eviction")

        # Initialize SkillRegistry for skills-native execution
        from core.skills import SkillRegistry
        from core.tools.loader import load_tool_registry

        # Load base tool registry for skill tool validation
        base_tool_registry = load_tool_registry(settings.tools_config_path)
        # Use async parallel loading for faster startup
        skill_registry = await SkillRegistry.create_async(tool_registry=base_tool_registry)
        LOGGER.info(
            "SkillRegistry initialized with %d skills (async parallel loading)",
            len(skill_registry.available()),
        )

        # Create ServiceFactory for context-aware service creation
        from core.runtime.service_factory import ServiceFactory

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

        # Email service + background schedulers (via orchestrator)
        email_service = create_email_service(settings)
        scheduler, homey_scheduler = await start_schedulers(email_service)

        # Job Scheduler (cron-based skill execution)
        from interfaces.scheduler.adapter import SchedulerAdapter

        telegram_token = (
            settings.telegram_bot_token if hasattr(settings, "telegram_bot_token") else None
        )
        job_scheduler = SchedulerAdapter(
            session_factory=AsyncSessionLocal,
            service_factory=service_factory,
            telegram_bot_token=telegram_token,
        )
        await job_scheduler.initialize_next_run_times()
        await job_scheduler.start()
        LOGGER.info("Job scheduler started")

        yield  # Application runs here

        # --- SHUTDOWN ---
        await job_scheduler.stop()
        await homey_scheduler.stop()
        await scheduler.stop()
        # Clean up email service
        if email_service is not None:
            await email_service.close()
        await mcp_pool.stop()
        await litellm_client.aclose()
        await token_manager.shutdown()
        # Close shared Qdrant client in ServiceFactory
        await service_factory.close()
        # Close shared HTTP client
        if _READINESS_HTTP_CLIENT is not None:
            await _READINESS_HTTP_CLIENT.aclose()

    # Assign lifespan to app
    app.router.lifespan_context = lifespan

    def get_service_factory() -> ServiceFactory:
        """Return the service factory from app state."""
        return app.state.service_factory

    def verify_agent_api_key(
        authorization: str | None = Header(None),
        x_api_key: str | None = Header(None, alias="X-API-Key"),
    ) -> None:
        """Verify internal API key for agent endpoints (dependency injection).

        Checks for API key in Authorization: Bearer <key> OR X-API-Key: <key> header.
        If AGENT_INTERNAL_API_KEY is not set, SKIP auth (dev convenience).

        Args:
            authorization: Authorization header value
            x_api_key: X-API-Key header value

        Raises:
            HTTPException 401: If key is required but invalid or missing
        """
        verify_internal_api_key(authorization, x_api_key, settings)

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
                type="shared",
                config={},
                default_cwd="/tmp",  # noqa: S108
            )
            session.add(context)
            await session.flush()

        return await factory.create_service(context.id, session)

    @app.get("/healthz", response_model=HealthStatus)
    async def health() -> HealthStatus:  # pragma: no cover - trivial endpoint
        return HealthStatus(status="ok", environment=settings.environment)

    @app.get("/readyz")
    async def readiness(
        request: Request,
        session: AsyncSession = Depends(get_db),
    ) -> JSONResponse:
        """Readiness probe that checks all dependencies.

        Returns HTTP 200 if all checks pass, HTTP 503 if any fail.
        """
        import asyncio

        checks: dict[str, dict[str, Any]] = {}
        all_ready = True

        # Database check
        async def check_database() -> dict[str, Any]:
            try:
                start = time.perf_counter()
                from sqlalchemy import text

                await session.execute(text("SELECT 1"))
                latency = (time.perf_counter() - start) * 1000
                return {"status": "ok", "latency_ms": round(latency, 1)}
            except Exception as e:
                return {"status": "error", "error": str(e)[:200]}

        # Qdrant check
        async def check_qdrant() -> dict[str, Any]:
            try:
                start = time.perf_counter()
                # Use shared Qdrant client from service factory
                factory = request.app.state.service_factory
                client = factory._qdrant_client

                # List collections to verify connection
                await asyncio.wait_for(client.get_collections(), timeout=2.0)
                latency = (time.perf_counter() - start) * 1000
                return {"status": "ok", "latency_ms": round(latency, 1)}
            except TimeoutError:
                return {"status": "error", "error": "timeout"}
            except AttributeError:
                return {"status": "unavailable", "error": "qdrant client not initialized"}
            except Exception as e:
                return {"status": "error", "error": str(e)[:200]}

        # Skill registry check
        async def check_skills() -> dict[str, Any]:
            try:
                if hasattr(request.app.state, "service_factory"):
                    factory: ServiceFactory = request.app.state.service_factory
                    if hasattr(factory, "_skill_registry"):
                        registry = factory._skill_registry
                        if registry is not None:
                            count = len(registry.available())
                            return {"status": "ok", "count": count}
                return {"status": "unavailable", "error": "skill registry not initialized"}
            except Exception as e:
                return {"status": "error", "error": str(e)[:200]}

        # LiteLLM check (use /health/liveliness for fast response)
        async def check_litellm() -> dict[str, Any]:
            try:
                start = time.perf_counter()
                if _READINESS_HTTP_CLIENT is None:
                    return {"status": "error", "error": "http client not initialized"}
                litellm_url = str(settings.litellm_api_base).rstrip("/")
                response = await _READINESS_HTTP_CLIENT.get(f"{litellm_url}/health/liveliness")
                latency = (time.perf_counter() - start) * 1000
                if response.status_code == 200:
                    return {"status": "ok", "latency_ms": round(latency, 1)}
                return {
                    "status": "error",
                    "error": f"HTTP {response.status_code}",
                }
            except (TimeoutError, httpx.TimeoutException):
                return {"status": "error", "error": "timeout"}
            except Exception as e:
                return {"status": "error", "error": str(e)[:200] or type(e).__name__}

        # Run all checks in parallel with timeout
        try:
            results = await asyncio.gather(
                check_database(),
                check_qdrant(),
                check_skills(),
                check_litellm(),
                return_exceptions=True,
            )

            # Cast results to proper types
            db_result = results[0]
            qdrant_result = results[1]
            skills_result = results[2]
            litellm_result = results[3]

            checks["database"] = (
                db_result
                if isinstance(db_result, dict)
                else {
                    "status": "error",
                    "error": str(db_result)[:200],
                }
            )
            checks["qdrant"] = (
                qdrant_result
                if isinstance(qdrant_result, dict)
                else {
                    "status": "error",
                    "error": str(qdrant_result)[:200],
                }
            )
            checks["skills"] = (
                skills_result
                if isinstance(skills_result, dict)
                else {
                    "status": "error",
                    "error": str(skills_result)[:200],
                }
            )
            checks["litellm"] = (
                litellm_result
                if isinstance(litellm_result, dict)
                else {
                    "status": "error",
                    "error": str(litellm_result)[:200],
                }
            )

            # Determine overall readiness
            for check_result in checks.values():
                if check_result.get("status") not in ("ok", "unavailable"):
                    all_ready = False
                    break

        except Exception:
            LOGGER.exception("Readiness check failed")
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "error": "Readiness check execution failed",
                    "checks": checks,
                },
            )

        status_code = 200 if all_ready else 503
        return JSONResponse(
            status_code=status_code,
            content={
                "status": "ready" if all_ready else "not_ready",
                "environment": settings.environment,
                "checks": checks,
            },
        )

    @app.post("/v1/agent", response_model=AgentResponse)
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

    @app.post("/v1/agent/chat/completions", response_model=ChatCompletionResponse)
    async def chat_completions(
        request: ChatCompletionRequest,
        svc: AgentService = Depends(get_service),
        session: AsyncSession = Depends(get_db),
        _auth: None = Depends(verify_agent_api_key),
    ) -> ChatCompletionResponse:
        return await _handle_chat_completions(request, svc, session)

    @app.post("/chat/completions", response_model=ChatCompletionResponse)
    async def legacy_chat_completions(
        request: ChatCompletionRequest,
        svc: AgentService = Depends(get_service),
        session: AsyncSession = Depends(get_db),
        _auth: None = Depends(verify_agent_api_key),
    ) -> ChatCompletionResponse:
        return await _handle_chat_completions(request, svc, session)

    @app.get("/models")
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

    @app.get("/v1/models")
    async def list_models_v1(
        svc: AgentService = Depends(get_service),
        _auth: None = Depends(verify_agent_api_key),
    ) -> Any:
        return await list_models(svc)

    @app.get("/v1/agent/history/{conversation_id}", response_model=list[AgentMessage])
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

    app.include_router(openwebui_router)
    app.include_router(oauth_router)
    app.include_router(oauth_webui_router)

    # Admin routers (secured with Entra ID headers or JWT)
    app.include_router(admin_auth_oauth_router)  # OAuth endpoints first
    app.include_router(admin_portal_router)
    app.include_router(admin_contexts_router)
    app.include_router(admin_workspaces_router)
    app.include_router(admin_oauth_router)
    app.include_router(admin_mcp_router)
    app.include_router(admin_diagnostics_router)
    app.include_router(admin_price_tracker_router)
    app.include_router(admin_permissions_router)
    app.include_router(admin_users_router)
    app.include_router(admin_scheduler_router)
    app.include_router(admin_api_router)  # Diagnostic API (X-API-Key or Entra ID)

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


__all__ = ["create_app", "run", "verify_internal_api_key"]


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
