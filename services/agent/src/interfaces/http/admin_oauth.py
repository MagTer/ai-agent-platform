"""Admin endpoints for OAuth token management."""

# ruff: noqa: E501
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from shared.sanitize import sanitize_log
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.user_service import get_or_create_user, get_user_default_context
from core.db.engine import get_db
from core.db.oauth_models import OAuthToken
from core.providers import get_token_manager
from core.tools.mcp_loader import get_mcp_client_pool
from interfaces.http.admin_auth import AdminUser, require_admin_or_redirect, verify_admin_user
from interfaces.http.csrf import require_csrf

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platformadmin/oauth",
    tags=["platform-admin", "oauth"],
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


@router.get("/tokens", response_model=OAuthTokenList, dependencies=[Depends(verify_admin_user)])
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
        Requires admin role via Entra ID authentication.
    """
    stmt = select(OAuthToken)

    # Apply filters
    if context_id:
        stmt = stmt.where(OAuthToken.context_id == context_id)
    if provider:
        stmt = stmt.where(OAuthToken.provider == provider)

    result = await session.execute(stmt)
    tokens = result.scalars().all()

    now = datetime.now(UTC).replace(tzinfo=None)
    token_infos = [
        OAuthTokenInfo(
            id=token.id,
            context_id=token.context_id,
            provider=token.provider,
            token_type=token.token_type,
            expires_at=token.expires_at,
            scope=token.scope,
            is_expired=token.expires_at < now,
            has_refresh_token=token.has_refresh_token(),
            created_at=token.created_at,
            updated_at=token.updated_at,
        )
        for token in tokens
    ]

    return OAuthTokenList(tokens=token_infos, total=len(token_infos))


@router.delete(
    "/tokens/{token_id}",
    response_model=RevokeResponse,
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
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
        Requires admin role via Entra ID authentication.
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

    LOGGER.info(
        "Revoked OAuth token %s for context %s (provider: %s)",
        sanitize_log(token_id),
        sanitize_log(context_id),
        sanitize_log(provider),
    )

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


@router.get(
    "/status/{context_id}", response_model=dict[str, Any], dependencies=[Depends(verify_admin_user)]
)
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
        Requires admin role via Entra ID authentication.
    """
    stmt = select(OAuthToken).where(OAuthToken.context_id == context_id)
    result = await session.execute(stmt)
    tokens = result.scalars().all()

    now = datetime.now(UTC).replace(tzinfo=None)
    provider_status = []

    for token in tokens:
        provider_status.append(
            {
                "provider": token.provider,
                "authorized": True,
                "expires_at": token.expires_at.isoformat(),
                "is_expired": token.expires_at < now,
                "has_refresh_token": token.has_refresh_token(),
                "scope": token.scope,
            }
        )

    return {
        "context_id": str(context_id),
        "providers": provider_status,
        "total_providers": len(provider_status),
    }


@router.get("/callback")
async def oauth_callback(
    code: str = Query(..., description="Authorization code from provider"),
    state: str = Query(..., description="State parameter for CSRF protection"),
    error: str | None = Query(None, description="Error if user denied"),
) -> HTMLResponse:
    """Handle OAuth provider callback (e.g., Homey).

    This endpoint receives the redirect from external OAuth providers after
    user authorization. It exchanges the code for tokens and stores them.

    Path: /platformadmin/oauth/callback

    Note: This is separate from Entra ID auth which uses /platformadmin/auth/callback

    Args:
        code: Authorization code from provider
        state: State parameter for CSRF validation
        error: Error code if user denied authorization

    Returns:
        HTML page showing success or error
    """
    from core.auth.models import OAuthError
    from core.observability.security_logger import (
        OAUTH_COMPLETED,
        OAUTH_FAILED,
        log_security_event,
    )

    # Handle user denial
    if error:
        log_security_event(
            event_type=OAUTH_FAILED,
            endpoint="/platformadmin/oauth/callback",
            details={"error": error, "reason": "user_denied"},
            severity="WARNING",
        )
        return HTMLResponse(
            content="""<!DOCTYPE html>
<html>
<head><title>Authorization Cancelled</title>
<style>body{font-family:system-ui,sans-serif;text-align:center;padding:50px}h1{color:#d32f2f}</style>
</head>
<body><h1>Authorization Cancelled</h1><p>You can close this window.</p></body>
</html>""",
            status_code=400,
        )

    try:
        token_manager = get_token_manager()
        await token_manager.exchange_code_for_token(
            authorization_code=code,
            state=state,
        )

        log_security_event(
            event_type=OAUTH_COMPLETED,
            endpoint="/platformadmin/oauth/callback",
            details={"state": state[:8] + "..."},
        )
        return HTMLResponse(
            content="""<!DOCTYPE html>
<html>
<head><title>Authorization Successful</title>
<style>body{font-family:system-ui,sans-serif;text-align:center;padding:50px}h1{color:#2e7d32}</style>
</head>
<body>
<h1>Authorization Successful!</h1>
<p>You can close this window and return to the admin portal.</p>
<script>setTimeout(()=>window.close(),3000)</script>
</body>
</html>"""
        )

    except OAuthError as e:
        log_security_event(
            event_type=OAUTH_FAILED,
            endpoint="/platformadmin/oauth/callback",
            details={"error": e.error, "description": e.description},
            severity="ERROR",
        )
        import html

        safe_error = html.escape(e.error or "Unknown error")
        safe_desc = html.escape(e.description or "")
        return HTMLResponse(
            content=f"""<!DOCTYPE html>
<html>
<head><title>Authorization Failed</title>
<style>body{{font-family:system-ui,sans-serif;text-align:center;padding:50px}}h1{{color:#d32f2f}}.err{{background:#ffebee;padding:20px;margin:20px auto;max-width:500px;border-radius:5px}}</style>
</head>
<body><h1>Authorization Failed</h1><div class="err"><p><b>Error:</b> {safe_error}</p><p>{safe_desc}</p></div></body>
</html>""",
            status_code=400,
        )
    except Exception as e:
        LOGGER.error(f"OAuth callback error: {e}", exc_info=True)
        return HTMLResponse(
            content="""<!DOCTYPE html>
<html>
<head><title>Error</title>
<style>body{font-family:system-ui,sans-serif;text-align:center;padding:50px}h1{color:#d32f2f}</style>
</head>
<body><h1>Internal Server Error</h1><p>Please try again later.</p></body>
</html>""",
            status_code=500,
        )


@router.get("/initiate/{provider}")
async def initiate_oauth(
    provider: str,
    context_id: UUID | None = None,
    admin: AdminUser = Depends(require_admin_or_redirect),
    session: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Initiate OAuth flow for the admin user's personal context.

    This endpoint:
    1. Gets or creates the admin user in the database
    2. Gets the user's personal context (or specified context)
    3. Generates an OAuth authorization URL
    4. Redirects the user to the provider's authorization page

    Args:
        provider: OAuth provider name (e.g., "homey")
        context_id: Optional context UUID to use instead of user's default context
        admin: Authenticated admin user
        session: Database session

    Returns:
        Redirect to OAuth provider's authorization page

    Security:
        Requires admin role via Entra ID authentication.
    """
    from core.auth.header_auth import UserIdentity

    # Create UserIdentity from admin info
    identity = UserIdentity(
        email=admin.email,
        name=admin.display_name or admin.email.split("@")[0],
        openwebui_id=str(admin.user_id),
    )

    # Get or create user and their personal context
    try:
        user = await get_or_create_user(identity, session)

        if context_id:
            # Use specified context (admin is authorizing for a specific context)
            from core.db.models import Context

            ctx_stmt = select(Context).where(Context.id == context_id)
            ctx_result = await session.execute(ctx_stmt)
            context = ctx_result.scalar_one_or_none()
            if not context:
                raise HTTPException(status_code=404, detail=f"Context {context_id} not found")
        else:
            context = await get_user_default_context(user, session)

        if not context:
            LOGGER.error(f"No default context found for user {user.email}")
            raise HTTPException(
                status_code=500,
                detail="Could not find user context. Please contact support.",
            )

        # Generate authorization URL
        token_manager = get_token_manager()
        authorization_url, state = await token_manager.get_authorization_url(
            provider=provider.lower(),
            context_id=context.id,
            user_id=user.id,
        )

        LOGGER.info(
            "Initiating OAuth for %s (user: %s, context: %s)",
            sanitize_log(provider),
            sanitize_log(user.email),
            sanitize_log(context.id),
        )

        # Redirect to provider's authorization page
        return RedirectResponse(url=authorization_url, status_code=302)

    except ValueError as e:
        LOGGER.error(f"OAuth initiation failed: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' is not configured: {e}",
        ) from e
    except Exception as e:
        LOGGER.error(f"Unexpected error during OAuth initiation: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initiate OAuth: {e}",
        ) from e


__all__ = ["router"]
