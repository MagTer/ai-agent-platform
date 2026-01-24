"""Admin endpoints for OAuth token management."""

# ruff: noqa: E501
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.db.oauth_models import OAuthToken
from core.tools.mcp_loader import get_mcp_client_pool
from interfaces.http.admin_auth import AdminUser, require_admin_or_redirect, verify_admin_user
from interfaces.http.admin_shared import UTF8HTMLResponse, render_admin_page

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platformadmin/oauth",
    tags=["platform-admin", "oauth"],
)


@router.get("/", response_class=UTF8HTMLResponse)
async def oauth_dashboard(admin: AdminUser = Depends(require_admin_or_redirect)) -> str:
    """OAuth token management dashboard.

    Security:
        Requires admin role via Entra ID authentication.
    """
    content = """
        <h1 class="page-title">OAuth Tokens</h1>

        <div class="card">
            <div class="card-header">
                <span>Active Tokens <span id="count" class="badge badge-success">0</span></span>
                <button class="btn" onclick="loadTokens()">Refresh</button>
            </div>
            <div class="token-list" id="tokens">
                <div class="loading">Loading...</div>
            </div>
        </div>
    """

    extra_css = """
        .token { padding: 16px; border: 1px solid var(--border); border-radius: 6px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }
        .token-info { flex: 1; }
        .token-provider { font-weight: 600; font-size: 15px; margin-bottom: 4px; }
        .token-meta { font-size: 12px; color: var(--text-muted); }
        .token-context { font-family: monospace; font-size: 11px; color: var(--text-muted); }
        .badge-ok { background: #d1fae5; color: #065f46; }
        .badge-warn { background: #fef3c7; color: #92400e; }
        .badge-err { background: #fee2e2; color: #991b1b; }
    """

    extra_js = """
        async function loadTokens() {
            try {
                const res = await fetch('/platformadmin/oauth/tokens');
                const data = await res.json();
                renderTokens(data);
            } catch (e) {
                document.getElementById('tokens').innerHTML = '<div style="color: var(--error)">Failed to load tokens</div>';
            }
        }
        function renderTokens(data) {
            document.getElementById('count').textContent = data.total || 0;
            const el = document.getElementById('tokens');
            if (!data.tokens || data.tokens.length === 0) {
                el.innerHTML = '<div class="loading">No OAuth tokens found</div>';
                return;
            }
            el.innerHTML = data.tokens.map(t => {
                const expiry = new Date(t.expires_at);
                const now = new Date();
                const isExpired = t.is_expired;
                const expiresIn = Math.round((expiry - now) / (1000 * 60 * 60));
                let badge = '<span class="badge badge-ok">Valid</span>';
                if (isExpired) {
                    badge = '<span class="badge badge-err">Expired</span>';
                } else if (expiresIn < 24) {
                    badge = '<span class="badge badge-warn">Expires soon</span>';
                }
                return `
                <div class="token">
                    <div class="token-info">
                        <div class="token-provider">${escapeHtml(t.provider)}</div>
                        <div class="token-context">Context: ${t.context_id}</div>
                        <div class="token-meta">
                            Type: ${t.token_type} |
                            Scope: ${t.scope || 'N/A'} |
                            Refresh: ${t.has_refresh_token ? 'Yes' : 'No'} |
                            Expires: ${expiry.toLocaleString()}
                        </div>
                    </div>
                    ${badge}
                </div>`;
            }).join('');
        }
        function escapeHtml(str) {
            if (!str) return '';
            const div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        }
        loadTokens();
    """

    return render_admin_page(
        title="OAuth Tokens",
        active_page="/platformadmin/oauth/",
        content=content,
        user_name=admin.display_name or admin.email.split("@")[0],
        user_email=admin.email,
        breadcrumbs=[("OAuth Settings", "#")],
        extra_css=extra_css,
        extra_js=extra_js,
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
            has_refresh_token=token.refresh_token is not None,
            created_at=token.created_at,
            updated_at=token.updated_at,
        )
        for token in tokens
    ]

    return OAuthTokenList(tokens=token_infos, total=len(token_infos))


@router.delete(
    "/tokens/{token_id}", response_model=RevokeResponse, dependencies=[Depends(verify_admin_user)]
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
