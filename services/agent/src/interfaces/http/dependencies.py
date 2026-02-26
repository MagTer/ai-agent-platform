"""FastAPI dependency injection functions for agent API endpoints."""

from __future__ import annotations

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.db.models import Context
from core.runtime.service import AgentService
from core.runtime.service_factory import ServiceFactory


def get_service_factory(request: Request) -> ServiceFactory:
    """Return the service factory from app state."""
    return request.app.state.service_factory


async def verify_agent_api_key(
    request: Request,
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> None:
    """Verify internal API key for agent endpoints (dependency injection).

    Checks for API key in Authorization: Bearer <key> OR X-API-Key: <key> header.
    If AGENT_INTERNAL_API_KEY is not set, SKIP auth (dev convenience).

    Args:
        request: FastAPI request (used to access app.state.settings)
        authorization: Authorization header value
        x_api_key: X-API-Key header value

    Raises:
        HTTPException 401: If key is required but invalid or missing
    """
    from interfaces.http.app import verify_internal_api_key

    settings = request.app.state.settings
    verify_internal_api_key(authorization, x_api_key, settings)


async def get_service(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> AgentService:
    """Get AgentService for legacy endpoints.

    For production: Creates a service using the factory with a default context.
    For testing: Returns the pre-injected test service if available.
    """
    from sqlalchemy import select

    # Check for test service first (avoids needing service_factory in tests)
    if hasattr(request.app.state, "test_service") and request.app.state.test_service is not None:
        return request.app.state.test_service

    # Production: Get factory and create service with default "api" context
    factory: ServiceFactory = request.app.state.service_factory

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


__all__ = ["get_service", "get_service_factory", "verify_agent_api_key"]
