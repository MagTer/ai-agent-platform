"""Admin endpoints for OAuth token management."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.db.oauth_models import OAuthToken
from core.tools.mcp_loader import get_mcp_client_pool

from .admin_auth import verify_admin_api_key

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/oauth",
    tags=["admin", "oauth"],
    dependencies=[Depends(verify_admin_api_key)],
)


class OAuthTokenInfo(BaseModel):
    """OAuth token information (sanitized for admin display)."""

    id: UUID
    context_id: UUID
    provider: str
    token_type: str
    expires_at: datetime
    scope: str | None
    is_expired: bool
    has_refresh_token: bool
    created_at: datetime
    updated_at: datetime


class OAuthTokenList(BaseModel):
    """List of OAuth tokens."""

    tokens: list[OAuthTokenInfo]
    total: int


class RevokeResponse(BaseModel):
    """Response after revoking an OAuth token."""

    success: bool
    message: str
    revoked_token_id: UUID


@router.get("/tokens", response_model=OAuthTokenList)
async def list_oauth_tokens(
    context_id: UUID | None = None,
    provider: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> OAuthTokenList:
    """List all OAuth tokens with optional filtering.

    Args:
        context_id: Optional context UUID to filter by
        provider: Optional provider name to filter by
        session: Database session

    Returns:
        List of OAuth tokens (with sensitive data masked)

    Security:
        Requires admin API key via X-API-Key header
    """
    stmt = select(OAuthToken)

    # Apply filters
    if context_id:
        stmt = stmt.where(OAuthToken.context_id == context_id)
    if provider:
        stmt = stmt.where(OAuthToken.provider == provider)

    result = await session.execute(stmt)
    tokens = result.scalars().all()

    now = datetime.utcnow()
    token_infos = [
        OAuthTokenInfo(
            id=token.id,
            context_id=token.context_id,
            provider=token.provider,
            token_type=token.token_type,
            expires_at=token.expires_at,
            scope=token.scope,
            is_expired=token.expires_at < now,
            has_refresh_token=token.refresh_token is not None,
            created_at=token.created_at,
            updated_at=token.updated_at,
        )
        for token in tokens
    ]

    return OAuthTokenList(tokens=token_infos, total=len(token_infos))


@router.delete("/tokens/{token_id}", response_model=RevokeResponse)
async def revoke_oauth_token(
    token_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> RevokeResponse:
    """Revoke (delete) an OAuth token and invalidate MCP client cache.

    This will:
    1. Delete the token from the database
    2. Disconnect MCP clients for that context (forcing re-auth on next request)

    Args:
        token_id: UUID of the token to revoke
        session: Database session

    Returns:
        Success confirmation

    Raises:
        HTTPException: 404 if token not found

    Security:
        Requires admin API key via X-API-Key header
    """
    # Find token
    stmt = select(OAuthToken).where(OAuthToken.id == token_id)
    result = await session.execute(stmt)
    token = result.scalar_one_or_none()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"OAuth token {token_id} not found",
        )

    context_id = token.context_id
    provider = token.provider

    # Delete token from database
    await session.delete(token)
    await session.commit()

    LOGGER.info(f"Revoked OAuth token {token_id} for context {context_id} (provider: {provider})")

    # Invalidate MCP client cache for this context
    try:
        pool = get_mcp_client_pool()
        await pool.disconnect_context(context_id)
        LOGGER.info(f"Disconnected MCP clients for context {context_id}")
    except RuntimeError as e:
        LOGGER.warning(f"Failed to disconnect MCP clients: {e}")

    return RevokeResponse(
        success=True,
        message=f"Revoked {provider} OAuth token for context {context_id}",
        revoked_token_id=token_id,
    )


@router.get("/status/{context_id}", response_model=dict[str, Any])
async def get_oauth_status(
    context_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get OAuth authorization status for a context.

    Shows which providers are authorized and token expiration status.

    Args:
        context_id: Context UUID
        session: Database session

    Returns:
        OAuth status summary

    Security:
        Requires admin API key via X-API-Key header
    """
    stmt = select(OAuthToken).where(OAuthToken.context_id == context_id)
    result = await session.execute(stmt)
    tokens = result.scalars().all()

    now = datetime.utcnow()
    provider_status = []

    for token in tokens:
        provider_status.append(
            {
                "provider": token.provider,
                "authorized": True,
                "expires_at": token.expires_at.isoformat(),
                "is_expired": token.expires_at < now,
                "has_refresh_token": token.refresh_token is not None,
                "scope": token.scope,
            }
        )

    return {
        "context_id": str(context_id),
        "providers": provider_status,
        "total_providers": len(provider_status),
    }


__all__ = ["router"]
