"""Admin endpoints for MCP client management."""

# ruff: noqa: E501
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

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
    """MCP client management dashboard.

    Security:
        Requires admin role via Entra ID authentication.
    """
    content = """
        <h1 class="page-title">MCP Servers</h1>

        <div class="card">
            <div class="card-header">
                <span class="card-title">Pool Statistics</span>
                <button class="btn btn-sm" onclick="loadData()">Refresh</button>
            </div>
            <div class="stats-grid" id="stats">
                <div class="loading">Loading...</div>
            </div>
        </div>

        <div class="card">
            <div class="card-header">
                <span class="card-title">Connected Clients</span>
            </div>
            <div id="clients">
                <div class="loading">Loading...</div>
            </div>
        </div>
    """

    extra_css = """
        .client { padding: 12px; border: 1px solid var(--border); border-radius: 6px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }
        .client-name { font-weight: 500; }
        .client-meta { font-size: 12px; color: var(--text-muted); margin-top: 4px; }
        .badge-ok { background: #d1fae5; color: #065f46; }
        .badge-err { background: #fee2e2; color: #991b1b; }
    """

    extra_js = """
        async function loadData() {
            try {
                const [statsRes, healthRes] = await Promise.all([
                    fetch('/platformadmin/mcp/stats'),
                    fetch('/platformadmin/mcp/health')
                ]);
                const stats = await statsRes.json();
                const health = await healthRes.json();
                renderStats(stats.stats);
                renderClients(health.health);
            } catch (e) {
                document.getElementById('stats').innerHTML = '<div style="color: var(--error)">Failed to load data</div>';
            }
        }

        function renderStats(s) {
            document.getElementById('stats').innerHTML = `
                <div class="stat-box"><div class="stat-value">${s.total_contexts || 0}</div><div class="stat-label">Contexts</div></div>
                <div class="stat-box"><div class="stat-value">${s.total_clients || 0}</div><div class="stat-label">Total Clients</div></div>
                <div class="stat-box"><div class="stat-value">${s.connected_clients || 0}</div><div class="stat-label">Connected</div></div>
                <div class="stat-box"><div class="stat-value">${s.disconnected_clients || 0}</div><div class="stat-label">Disconnected</div></div>
            `;
        }

        function renderClients(health) {
            const el = document.getElementById('clients');
            const contexts = Object.entries(health);
            if (contexts.length === 0) {
                el.innerHTML = '<div class="empty-state"><div class="empty-state-icon">&#128268;</div><p>No MCP clients connected</p></div>';
                return;
            }
            let html = '';
            for (const [ctxId, data] of contexts) {
                for (const client of (data.clients || [])) {
                    const badge = client.connected
                        ? '<span class="badge badge-ok">Connected</span>'
                        : '<span class="badge badge-err">Disconnected</span>';
                    html += `<div class="client">
                        <div>
                            <div class="client-name">${client.name || 'Unknown'}</div>
                            <div class="client-meta">Context: ${ctxId.slice(0,8)}... | Tools: ${client.tools_count || 0}</div>
                        </div>
                        ${badge}
                    </div>`;
                }
            }
            el.innerHTML = html || '<div class="empty-state">No MCP clients connected</div>';
        }

        loadData();
    """

    return render_admin_page(
        title="MCP Servers",
        active_page="/platformadmin/mcp/",
        content=content,
        user_name=admin.display_name or admin.email.split("@")[0],
        user_email=admin.email,
        breadcrumbs=[("MCP Servers", "#")],
        extra_css=extra_css,
        extra_js=extra_js,
    )


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

    Returns:
        Health status for each context, including:
        - Client connection status
        - Number of tools/resources/prompts
        - Cache staleness

    Security:
        Requires admin role via Entra ID authentication.

    Example response:
        {
            "health": {
                "abc-123-def-456": {
                    "clients": [
                        {
                            "name": "Homey",
                            "connected": true,
                            "state": "CONNECTED",
                            "tools_count": 15,
                            "resources_count": 0,
                            "prompts_count": 0,
                            "cache_stale": false
                        }
                    ],
                    "total_clients": 1
                }
            }
        }
    """
    health = await get_mcp_health()
    return MCPHealthResponse(health=health)


@router.get("/stats", response_model=MCPStatsResponse, dependencies=[Depends(verify_admin_user)])
async def get_mcp_client_stats() -> MCPStatsResponse:
    """Get overall MCP client pool statistics.

    Returns:
        Statistics including:
        - Total number of contexts
        - Total number of clients
        - Number of connected clients
        - Number of disconnected clients

    Security:
        Requires admin role via Entra ID authentication.

    Example response:
        {
            "stats": {
                "total_contexts": 3,
                "total_clients": 5,
                "connected_clients": 4,
                "disconnected_clients": 1
            }
        }
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

    This will:
    1. Disconnect all active MCP clients for the context
    2. Remove them from the client pool cache
    3. Next request will create fresh connections

    Useful for:
    - Forcing re-authentication after OAuth token changes
    - Recovering from stuck connections
    - Resetting MCP client state

    Args:
        context_id: Context UUID

    Returns:
        Success confirmation

    Raises:
        HTTPException: 503 if MCP client pool not initialized

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
