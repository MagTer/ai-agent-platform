"""Admin endpoints for MCP client management."""

# ruff: noqa: E501
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from core.tools.mcp_loader import get_mcp_client_pool, get_mcp_health, get_mcp_stats
from interfaces.http.admin_auth import require_admin_or_redirect, verify_admin_user

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platformadmin/mcp",
    tags=["platform-admin", "mcp"],
)


@router.get("/", response_class=HTMLResponse, dependencies=[Depends(require_admin_or_redirect)])
async def mcp_dashboard() -> str:
    """MCP client management dashboard.

    Security:
        Requires admin role via Entra ID authentication.
    """
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MCP Servers - Admin</title>
    <style>
        :root { --primary: #8b5cf6; --bg: #f8fafc; --card: #fff; --border: #e2e8f0; --text: #1e293b; --muted: #64748b; --success: #10b981; --error: #ef4444; }
        body { font-family: system-ui, sans-serif; margin: 0; background: var(--bg); color: var(--text); }
        .header { background: linear-gradient(135deg, #1e293b, #334155); color: white; padding: 24px; }
        .header h1 { margin: 0 0 4px 0; font-size: 20px; }
        .header p { margin: 0; opacity: 0.8; font-size: 13px; }
        .nav { padding: 8px 24px; background: var(--card); border-bottom: 1px solid var(--border); }
        .nav a { color: var(--primary); text-decoration: none; font-size: 13px; }
        .container { max-width: 900px; margin: 24px auto; padding: 0 24px; }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 16px; }
        .card h2 { margin: 0 0 16px 0; font-size: 16px; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 16px; }
        .stat { text-align: center; }
        .stat-value { font-size: 28px; font-weight: 600; color: var(--primary); }
        .stat-label { font-size: 12px; color: var(--muted); }
        .client-list { margin-top: 16px; }
        .client { padding: 12px; border: 1px solid var(--border); border-radius: 6px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }
        .client-name { font-weight: 500; }
        .client-meta { font-size: 12px; color: var(--muted); }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; }
        .badge-ok { background: #d1fae5; color: #065f46; }
        .badge-err { background: #fee2e2; color: #991b1b; }
        .loading { color: var(--muted); font-style: italic; }
        .btn { padding: 6px 12px; border-radius: 4px; font-size: 12px; cursor: pointer; border: 1px solid var(--border); background: var(--card); }
        .btn:hover { background: var(--bg); }
        .btn-refresh { margin-left: auto; }
    </style>
</head>
<body>
    <div class="header">
        <h1>MCP Servers</h1>
        <p>Model Context Protocol client management</p>
    </div>
    <div class="nav"><a href="/platformadmin/">&larr; Back to Admin Portal</a></div>
    <div class="container">
        <div class="card">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <h2>Pool Statistics</h2>
                <button class="btn btn-refresh" onclick="loadData()">Refresh</button>
            </div>
            <div class="stats" id="stats">
                <div class="loading">Loading...</div>
            </div>
        </div>
        <div class="card">
            <h2>Connected Clients</h2>
            <div class="client-list" id="clients">
                <div class="loading">Loading...</div>
            </div>
        </div>
    </div>
    <script>
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
                <div class="stat"><div class="stat-value">${s.total_contexts || 0}</div><div class="stat-label">Contexts</div></div>
                <div class="stat"><div class="stat-value">${s.total_clients || 0}</div><div class="stat-label">Total Clients</div></div>
                <div class="stat"><div class="stat-value">${s.connected_clients || 0}</div><div class="stat-label">Connected</div></div>
                <div class="stat"><div class="stat-value">${s.disconnected_clients || 0}</div><div class="stat-label">Disconnected</div></div>
            `;
        }
        function renderClients(health) {
            const el = document.getElementById('clients');
            const contexts = Object.entries(health);
            if (contexts.length === 0) {
                el.innerHTML = '<div class="loading">No MCP clients connected</div>';
                return;
            }
            let html = '';
            for (const [ctxId, data] of contexts) {
                for (const client of (data.clients || [])) {
                    const badge = client.connected ? '<span class="badge badge-ok">Connected</span>' : '<span class="badge badge-err">Disconnected</span>';
                    html += `<div class="client">
                        <div>
                            <div class="client-name">${client.name || 'Unknown'}</div>
                            <div class="client-meta">Context: ${ctxId.slice(0,8)}... | Tools: ${client.tools_count || 0}</div>
                        </div>
                        ${badge}
                    </div>`;
                }
            }
            el.innerHTML = html || '<div class="loading">No MCP clients connected</div>';
        }
        loadData();
    </script>
</body>
</html>"""


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
