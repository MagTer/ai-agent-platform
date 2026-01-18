"""Admin endpoints for OAuth token management."""

# ruff: noqa: E501
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.db.oauth_models import OAuthToken
from core.tools.mcp_loader import get_mcp_client_pool
from interfaces.http.admin_auth import verify_admin_user

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/oauth",
    tags=["admin", "oauth"],
)


@router.get("/", response_class=HTMLResponse, dependencies=[Depends(verify_admin_user)])
async def oauth_dashboard() -> str:
    """OAuth token management dashboard.

    Security:
        Requires admin role via Entra ID authentication.
    """
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OAuth Tokens - Admin</title>
    <style>
        :root { --primary: #ec4899; --bg: #f8fafc; --card: #fff; --border: #e2e8f0; --text: #1e293b; --muted: #64748b; --success: #10b981; --error: #ef4444; --warning: #f59e0b; }
        body { font-family: system-ui, sans-serif; margin: 0; background: var(--bg); color: var(--text); }
        .header { background: linear-gradient(135deg, #1e293b, #334155); color: white; padding: 24px; }
        .header h1 { margin: 0 0 4px 0; font-size: 20px; }
        .header p { margin: 0; opacity: 0.8; font-size: 13px; }
        .nav { padding: 8px 24px; background: var(--card); border-bottom: 1px solid var(--border); }
        .nav a { color: var(--primary); text-decoration: none; font-size: 13px; }
        .container { max-width: 900px; margin: 24px auto; padding: 0 24px; }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 16px; }
        .card h2 { margin: 0 0 16px 0; font-size: 16px; display: flex; justify-content: space-between; align-items: center; }
        .token-list { margin-top: 16px; }
        .token { padding: 16px; border: 1px solid var(--border); border-radius: 6px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }
        .token-info { flex: 1; }
        .token-provider { font-weight: 600; font-size: 15px; margin-bottom: 4px; }
        .token-meta { font-size: 12px; color: var(--muted); }
        .token-context { font-family: monospace; font-size: 11px; color: var(--muted); }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; }
        .badge-ok { background: #d1fae5; color: #065f46; }
        .badge-warn { background: #fef3c7; color: #92400e; }
        .badge-err { background: #fee2e2; color: #991b1b; }
        .loading { color: var(--muted); font-style: italic; }
        .btn { padding: 6px 12px; border-radius: 4px; font-size: 12px; cursor: pointer; border: 1px solid var(--border); background: var(--card); }
        .btn:hover { background: var(--bg); }
        .btn-danger { color: var(--error); border-color: var(--error); }
        .btn-danger:hover { background: #fee2e2; }
    </style>
</head>
<body>
    <div class="header">
        <h1>OAuth Tokens</h1>
        <p>Manage OAuth authentication tokens</p>
    </div>
    <div class="nav"><a href="/admin/">&larr; Back to Admin Portal</a></div>
    <div class="container">
        <div class="card">
            <h2>
                <span>Active Tokens <span id="count" class="badge badge-ok">0</span></span>
                <button class="btn" onclick="loadTokens()">Refresh</button>
            </h2>
            <div class="token-list" id="tokens">
                <div class="loading">Loading...</div>
            </div>
        </div>
    </div>
    <script>
        async function loadTokens() {
            try {
                const res = await fetch('/admin/oauth/tokens');
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
    </script>
</body>
</html>"""


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
