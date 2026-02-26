"""FastAPI application factory for the agent service."""

from __future__ import annotations

import logging
import secrets
import sys

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from core.middleware.rate_limit import create_rate_limiter, rate_limit_exceeded_handler
from core.observability.logging import setup_logging, setup_otel_log_bridge
from core.observability.metrics import configure_metrics
from core.observability.tracing import configure_tracing
from core.runtime.config import Settings, get_settings
from core.runtime.litellm_client import LiteLLMClient
from core.runtime.models import HealthStatus
from core.runtime.service import AgentService
from interfaces.http.admin_ado import router as admin_ado_router
from interfaces.http.admin_api import router as admin_api_router
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
from interfaces.http.admin_wiki import router as admin_wiki_router
from interfaces.http.admin_workspaces import router as admin_workspaces_router
from interfaces.http.agent_api import router as agent_api_router
from interfaces.http.bootstrap import create_lifespan
from interfaces.http.middleware import register_middlewares
from interfaces.http.oauth import router as oauth_router
from interfaces.http.oauth_webui import router as oauth_webui_router
from interfaces.http.openwebui_adapter import router as openwebui_router
from interfaces.http.readiness import create_readiness_router

LOGGER = logging.getLogger(__name__)


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

    app = FastAPI(title=settings.app_name)

    # Store settings in app.state so dependencies can access the exact settings instance
    app.state.settings = settings

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

    # Register all HTTP middleware and exception handlers
    register_middlewares(app, settings)

    FastAPIInstrumentor.instrument_app(app, excluded_urls="/healthz,/readyz")

    # Create shared LiteLLM client (stateless, safe to share across contexts)
    litellm_client = LiteLLMClient(settings)
    # Store in app.state for access by openwebui_adapter and other components
    app.state.litellm_client = litellm_client

    # Assign lifespan to app
    app.router.lifespan_context = create_lifespan(settings, litellm_client)

    @app.get("/healthz", response_model=HealthStatus)
    async def health() -> HealthStatus:  # pragma: no cover - trivial endpoint
        return HealthStatus(status="ok", environment=settings.environment)

    # Readiness probe (checks database, qdrant, skills, litellm)
    app.include_router(create_readiness_router(settings))

    # Agent API endpoints
    app.include_router(agent_api_router)

    app.include_router(openwebui_router)
    app.include_router(oauth_router)
    app.include_router(oauth_webui_router)

    # Admin routers (secured with Entra ID headers or JWT)
    app.include_router(admin_auth_oauth_router)  # OAuth endpoints first
    app.include_router(admin_portal_router)
    app.include_router(admin_contexts_router)
    app.include_router(admin_workspaces_router)
    app.include_router(admin_wiki_router)
    app.include_router(admin_oauth_router)
    app.include_router(admin_mcp_router)
    app.include_router(admin_diagnostics_router)
    app.include_router(admin_price_tracker_router)
    app.include_router(admin_permissions_router)
    app.include_router(admin_users_router)
    app.include_router(admin_scheduler_router)
    app.include_router(admin_ado_router)
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
