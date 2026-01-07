"""Admin API authentication using API key."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from core.core.config import Settings, get_settings


def verify_admin_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
) -> None:
    """Verify admin API key from X-API-Key header.

    Args:
        x_api_key: API key from X-API-Key header
        settings: Application settings

    Raises:
        HTTPException: 401 if API key is missing or invalid
        HTTPException: 503 if admin API key is not configured

    Usage:
        @router.get("/admin/something", dependencies=[Depends(verify_admin_api_key)])
        async def admin_endpoint():
            ...
    """
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin API key not configured. Set AGENT_ADMIN_API_KEY environment variable.",
        )

    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    # Use constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(x_api_key, settings.admin_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )


__all__ = ["verify_admin_api_key"]
