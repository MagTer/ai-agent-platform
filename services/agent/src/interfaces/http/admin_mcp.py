"""Admin endpoints for MCP integration management."""

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
from core.db.models import Context, DebugLog, UserContext, UserCredential
from core.db.oauth_models import OAuthToken
from core.tools.mcp_loader import get_mcp_client_pool, get_mcp_health, get_mcp_stats
from interfaces.http.admin_auth import AdminUser, require_admin_or_redirect, verify_admin_user
from interfaces.http.admin_shared import UTF8HTMLResponse, render_admin_page

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platformadmin/mcp",
    tags=["platform-admin", "mcp"],
)


@router.get("/", response_class=UTF8HTMLResponse)
async def mcp_dashboard(admin: AdminUser = Depends(require_admin_or_redirect)) -> str:
    """MCP integration management dashboard.

    Security:
        Requires admin role via Entra ID authentication.
    """
    content = """
        <h1 class="page-title">MCP Integrations</h1>

        <div class="card">
            <div class="card-header">
                <span class="card-title">Configured Integrations</span>
            </div>
            <div id="integrations">
                <div class="loading">Loading...</div>
            </div>
        </div>

        <div class="card">
            <div class="card-header">
                <span class="card-title">Recent Activity</span>
                <button class="btn btn-sm" onclick="loadActivity()">Refresh</button>
            </div>
            <div id="activity">
                <div class="loading">Loading...</div>
            </div>
        </div>
    """

    extra_css = """
        .tooltip-wrap { position: relative; cursor: help; }
        .tooltip-wrap .tooltip-text { display: none; position: absolute; bottom: 100%; left: 50%; transform: translateX(-50%); background: var(--text); color: #fff; padding: 6px 10px; border-radius: 4px; font-size: 11px; white-space: nowrap; z-index: 10; margin-bottom: 4px; }
        .tooltip-wrap:hover .tooltip-text { display: block; }
    """

    extra_js = """
        async function loadIntegrations() {
            try {
                const res = await fetch('/platformadmin/mcp/integrations');
                const data = await res.json();
                renderIntegrations(data.integrations);
            } catch (e) {
                document.getElementById('integrations').innerHTML = '<div style="color: var(--error)">Failed to load integrations</div>';
            }
        }

        async function loadActivity() {
            try {
                const res = await fetch('/platformadmin/mcp/activity');
                const data = await res.json();
                renderActivity(data.events);
            } catch (e) {
                document.getElementById('activity').innerHTML = '<div style="color: var(--error)">Failed to load activity</div>';
            }
        }

        function renderIntegrations(items) {
            const el = document.getElementById('integrations');
            if (!items || items.length === 0) {
                el.innerHTML = '<div class="empty-state"><div class="empty-state-icon">&#128268;</div><p>No MCP integrations configured</p></div>';
                return;
            }
            let html = '<table><thead><tr><th>Context</th><th>Provider</th><th>Type</th><th>Status</th><th>Last Updated</th></tr></thead><tbody>';
            for (const item of items) {
                const statusBadge = item.status === 'active'
                    ? '<span class="badge badge-success">Active</span>'
                    : item.status === 'expired'
                    ? '<span class="badge badge-error">Expired</span>'
                    : '<span class="badge badge-warning">' + item.status + '</span>';
                const typeBadge = item.type === 'oauth'
                    ? '<span class="badge badge-info">OAuth</span>'
                    : '<span class="badge badge-muted">Credential</span>';
                const ctxName = item.context_name || item.context_id.slice(0, 8) + '...';
                const updated = item.updated_at ? new Date(item.updated_at).toLocaleString() : '-';
                html += '<tr><td>' + ctxName + '</td><td>' + item.provider + '</td><td>' + typeBadge + '</td><td>' + statusBadge + '</td><td>' + updated + '</td></tr>';
            }
            html += '</tbody></table>';
            el.innerHTML = html;
        }

        function renderActivity(events) {
            const el = document.getElementById('activity');
            if (!events || events.length === 0) {
                el.innerHTML = '<div class="empty-state"><div class="empty-state-icon">&#128268;</div><p>No recent MCP activity</p></div>';
                return;
            }
            let html = '<table><thead><tr><th>Time</th><th>Context</th><th>Provider</th><th>Result</th><th>Tools</th><th>Transport</th></tr></thead><tbody>';
            for (const ev of events) {
                const resultBadge = ev.result === 'connected'
                    ? '<span class="badge badge-success">Connected</span>'
                    : '<span class="tooltip-wrap"><span class="badge badge-error">Error</span><span class="tooltip-text">' + (ev.error || 'Unknown error') + '</span></span>';
                const ts = new Date(ev.timestamp).toLocaleString();
                const ctxShort = ev.context_id ? ev.context_id.slice(0, 8) + '...' : '-';
                html += '<tr><td>' + ts + '</td><td>' + ctxShort + '</td><td>' + (ev.provider || '-') + '</td><td>' + resultBadge + '</td><td>' + (ev.tools_count != null ? ev.tools_count : '-') + '</td><td>' + (ev.transport || '-') + '</td></tr>';
            }
            html += '</tbody></table>';
            el.innerHTML = html;
        }

        loadIntegrations();
        loadActivity();
    """

    return render_admin_page(
        title="MCP Integrations",
        active_page="/platformadmin/mcp/",
        content=content,
        user_name=admin.display_name or admin.email.split("@")[0],
        user_email=admin.email,
        breadcrumbs=[("MCP Integrations", "#")],
        extra_css=extra_css,
        extra_js=extra_js,
    )


# -- New API endpoints --


class MCPIntegrationItem(BaseModel):
    """Single MCP integration entry."""

    context_id: str
    context_name: str | None
    provider: str
    type: str  # "oauth" | "credential"
    status: str  # "active" | "expired" | "no_encryption_key"
    updated_at: str | None


class MCPIntegrationsResponse(BaseModel):
    """List of configured MCP integrations."""

    integrations: list[MCPIntegrationItem]


class MCPActivityEvent(BaseModel):
    """Single MCP activity event."""

    timestamp: str
    context_id: str | None
    provider: str | None
    result: str  # "connected" | "error"
    tools_count: int | None
    error: str | None
    transport: str | None


class MCPActivityResponse(BaseModel):
    """List of recent MCP activity events."""

    events: list[MCPActivityEvent]


@router.get(
    "/integrations",
    response_model=MCPIntegrationsResponse,
    dependencies=[Depends(verify_admin_user)],
)
async def get_mcp_integrations(
    session: AsyncSession = Depends(get_db),
) -> MCPIntegrationsResponse:
    """Get configured MCP integrations per context.

    Returns OAuth-based providers (Context7, Homey) and credential-based
    providers (Zapier) with their status.
    """
    integrations: list[MCPIntegrationItem] = []

    # OAuth-based integrations (joined with Context for name)
    oauth_stmt = (
        select(OAuthToken, Context.name)
        .join(Context, OAuthToken.context_id == Context.id)
        .order_by(Context.name, OAuthToken.provider)
    )
    oauth_result = await session.execute(oauth_stmt)

    now = datetime.now(UTC).replace(tzinfo=None)
    for token, context_name in oauth_result.all():
        is_expired = token.expires_at < now
        integrations.append(
            MCPIntegrationItem(
                context_id=str(token.context_id),
                context_name=context_name,
                provider=token.provider.capitalize(),
                type="oauth",
                status="expired" if is_expired else "active",
                updated_at=token.updated_at.isoformat() if token.updated_at else None,
            )
        )

    # Credential-based integrations (Zapier MCP URL)
    cred_stmt = (
        select(UserCredential, Context.name, UserContext.context_id)
        .join(UserContext, UserCredential.user_id == UserContext.user_id)
        .join(Context, UserContext.context_id == Context.id)
        .where(UserCredential.credential_type == "zapier_mcp_url")
        .order_by(Context.name)
    )
    cred_result = await session.execute(cred_stmt)

    seen_contexts: set[str] = set()
    for _cred, context_name, context_id in cred_result.all():
        ctx_key = str(context_id)
        if ctx_key in seen_contexts:
            continue
        seen_contexts.add(ctx_key)
        integrations.append(
            MCPIntegrationItem(
                context_id=ctx_key,
                context_name=context_name,
                provider="Zapier",
                type="credential",
                status="active",
                updated_at=_cred.updated_at.isoformat() if _cred.updated_at else None,
            )
        )

    return MCPIntegrationsResponse(integrations=integrations)


@router.get(
    "/activity",
    response_model=MCPActivityResponse,
    dependencies=[Depends(verify_admin_user)],
)
async def get_mcp_activity(
    session: AsyncSession = Depends(get_db),
) -> MCPActivityResponse:
    """Get recent MCP connection events.

    Returns the last 50 MCP connect/error events from the debug log.
    """
    stmt = (
        select(DebugLog)
        .where(DebugLog.event_type.in_(["mcp_connect", "mcp_error"]))
        .order_by(DebugLog.created_at.desc())
        .limit(50)
    )
    result = await session.execute(stmt)
    logs = result.scalars().all()

    events: list[MCPActivityEvent] = []
    for log in logs:
        data = log.event_data or {}
        is_error = log.event_type == "mcp_error"
        events.append(
            MCPActivityEvent(
                timestamp=log.created_at.isoformat(),
                context_id=log.trace_id,
                provider=data.get("provider"),
                result="error" if is_error else "connected",
                tools_count=data.get("tools_count"),
                error=data.get("error") if is_error else None,
                transport=data.get("transport"),
            )
        )

    return MCPActivityResponse(events=events)


# -- Existing endpoints (kept for programmatic use) --


class MCPHealthResponse(BaseModel):
    """MCP client health status response."""

    health: dict[str, Any]


class MCPStatsResponse(BaseModel):
    """MCP client pool statistics response."""

    stats: dict[str, Any]


class DisconnectResponse(BaseModel):
    """Response after disconnecting MCP clients."""

    success: bool
    message: str
    context_id: UUID


@router.get("/health", response_model=MCPHealthResponse, dependencies=[Depends(verify_admin_user)])
async def get_mcp_client_health() -> MCPHealthResponse:
    """Get health status of all MCP client pools across all contexts.

    Security:
        Requires admin role via Entra ID authentication.
    """
    health = await get_mcp_health()
    return MCPHealthResponse(health=health)


@router.get("/stats", response_model=MCPStatsResponse, dependencies=[Depends(verify_admin_user)])
async def get_mcp_client_stats() -> MCPStatsResponse:
    """Get overall MCP client pool statistics.

    Security:
        Requires admin role via Entra ID authentication.
    """
    stats = get_mcp_stats()
    return MCPStatsResponse(stats=stats)


@router.post(
    "/disconnect/{context_id}",
    response_model=DisconnectResponse,
    dependencies=[Depends(verify_admin_user)],
)
async def disconnect_mcp_clients(context_id: UUID) -> DisconnectResponse:
    """Force disconnect all MCP clients for a context.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        pool = get_mcp_client_pool()
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"MCP client pool not initialized: {e}",
        ) from e

    await pool.disconnect_context(context_id)

    LOGGER.info(f"Admin disconnected MCP clients for context {context_id}")

    return DisconnectResponse(
        success=True,
        message=f"Disconnected all MCP clients for context {context_id}",
        context_id=context_id,
    )


__all__ = ["router"]
