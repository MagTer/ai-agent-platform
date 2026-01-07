"""Admin diagnostics endpoints (secured version of diagnostics router)."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from core.core.config import Settings, get_settings
from core.diagnostics.service import DiagnosticsService, TestResult, TraceGroup

from .admin_auth import verify_admin_api_key

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/diagnostics",
    tags=["admin", "diagnostics"],
    dependencies=[Depends(verify_admin_api_key)],
)


def get_diagnostics_service(
    settings: Settings = Depends(get_settings),
) -> DiagnosticsService:
    """Create diagnostics service instance."""
    return DiagnosticsService(settings)


@router.get("/traces", response_model=list[TraceGroup])
async def get_traces(
    limit: int = 1000,
    show_all: bool = False,
    service: DiagnosticsService = Depends(get_diagnostics_service),
) -> list[TraceGroup]:
    """Get recent traces, filtering out diagnostic/health-check traces by default.

    Args:
        limit: Maximum number of traces to return
        show_all: If True, include diagnostic/health traces. Default False
        service: Diagnostics service

    Returns:
        List of trace groups

    Security:
        Requires admin API key via X-API-Key header
    """
    return service.get_recent_traces(limit, show_all=show_all)


@router.get("/metrics")
async def get_metrics(
    window: int = 60,
    service: DiagnosticsService = Depends(get_diagnostics_service),
) -> dict[str, Any]:
    """Get system health metrics.

    Args:
        window: Number of traces to analyze (default 60)
        service: Diagnostics service

    Returns:
        System health metrics including error rates and insights

    Security:
        Requires admin API key via X-API-Key header
    """
    return service.get_system_health_metrics(window=window)


@router.post("/run", response_model=list[TestResult])
async def run_diagnostics(
    service: DiagnosticsService = Depends(get_diagnostics_service),
) -> list[TestResult]:
    """Run integration tests on all system components.

    Returns:
        List of test results with component status and latency

    Security:
        Requires admin API key via X-API-Key header
    """
    return await service.run_diagnostics()


@router.get("/summary")
async def get_diagnostics_summary(
    service: DiagnosticsService = Depends(get_diagnostics_service),
) -> dict[str, Any]:
    """Get a machine-readable diagnostics summary for AI agent consumption.

    Returns a structured report with:
    - overall_status: HEALTHY | DEGRADED | CRITICAL
    - failed_components: List of components with failure details and error codes
    - recommended_actions: Prioritized list of fixes with recovery hints
    - healthy_components: List of working components
    - metrics: System health metrics from recent traces

    This endpoint is optimized for AI agent self-diagnosis and automated remediation.

    Security:
        Requires admin API key via X-API-Key header
    """
    return await service.get_diagnostics_summary()


@router.get("/crash-log")
async def get_crash_log() -> dict[str, Any]:
    """Expose last_crash.log for AI agent consumption.

    Returns:
        - exists: Whether the crash log file exists
        - content: The crash log content (if exists)
        - modified: When the file was last modified (if exists)

    This endpoint enables AI agents to autonomously read crash logs
    for troubleshooting without requiring file system access.

    Security:
        Requires admin API key via X-API-Key header
    """
    log_path = Path("services/agent/last_crash.log")
    if not log_path.exists():
        return {"exists": False, "content": None, "message": "No crash log found"}

    try:
        content = log_path.read_text(encoding="utf-8")
        modified = datetime.fromtimestamp(log_path.stat().st_mtime).isoformat()
        return {
            "exists": True,
            "content": content,
            "modified": modified,
        }
    except Exception as e:
        LOGGER.error(f"Failed to read crash log: {e}")
        return {"exists": False, "content": None, "message": f"Read error: {e}"}


@router.post("/retention")
async def run_retention(
    message_days: int = 30,
    inactive_days: int = 90,
    max_messages: int = 500,
) -> dict[str, Any]:
    """Run database retention cleanup.

    Args:
        message_days: Delete messages older than this (default 30)
        inactive_days: Delete conversations inactive for this long (default 90)
        max_messages: Max messages per conversation (default 500)

    Returns:
        Summary of deleted records

    Security:
        Requires admin API key via X-API-Key header
    """
    from core.db.engine import AsyncSessionLocal
    from core.db.retention import run_retention_cleanup

    async with AsyncSessionLocal() as session:
        results = await run_retention_cleanup(
            session,
            message_retention_days=message_days,
            inactive_conversation_days=inactive_days,
            max_messages_per_conversation=max_messages,
        )
        return {
            "status": "completed",
            "settings": {
                "message_days": message_days,
                "inactive_days": inactive_days,
                "max_messages": max_messages,
            },
            **results,
        }


@router.get("/mcp")
async def get_mcp_health() -> dict[str, Any]:
    """Get health status of all MCP server connections.

    Returns:
        - servers: Dict mapping server name to health info
        - connected_count: Number of connected servers
        - total_tools: Total tools across all servers

    This endpoint enables monitoring of MCP integrations.

    Security:
        Requires admin API key via X-API-Key header
    """
    from core.tools.mcp_loader import get_mcp_health as fetch_mcp_health

    try:
        health = await fetch_mcp_health()
        connected = sum(1 for s in health.values() if s.get("connected"))
        total_tools = sum(s.get("tools_count", 0) for s in health.values())

        return {
            "status": "ok" if connected == len(health) or len(health) == 0 else "degraded",
            "servers": health,
            "connected_count": connected,
            "total_count": len(health),
            "total_tools": total_tools,
        }
    except Exception as e:
        LOGGER.error("Failed to get MCP health: %s", e)
        return {
            "status": "error",
            "message": str(e),
            "servers": {},
            "connected_count": 0,
            "total_count": 0,
            "total_tools": 0,
        }


@router.get("/", response_class=HTMLResponse)
async def diagnostics_dashboard(
    service: DiagnosticsService = Depends(get_diagnostics_service),
) -> str:
    """Interactive diagnostics dashboard (HTML).

    Returns:
        HTML dashboard for monitoring system health

    Security:
        Requires admin API key via X-API-Key header

    Note:
        The dashboard makes AJAX calls to other admin endpoints,
        so the X-API-Key header must be included in all requests.
    """
    # Professional Split-Pane Dashboard
    html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Diagnostics Dashboard</title>
    <style>
        :root { --sidebar-w: 350px; --primary: #2563eb; --bg: #f3f4f6; --white: #fff; --border: #e5e7eb; --text: #1f2937; --text-muted: #6b7280; --success: #10b981; --error: #ef4444; }
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; margin: 0; background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; overflow: hidden; }

        /* Header */
        .header { background: #fff; border-bottom: 1px solid var(--border); padding: 0 20px; height: 56px; display: flex; align-items: center; justify-content: space-between; flex-shrink: 0; z-index: 10; }
        .brand { font-weight: 600; font-size: 16px; display: flex; align-items: center; gap: 8px; }
        .notice { background: #fef3c7; color: #92400e; padding: 4px 12px; border-radius: 6px; font-size: 12px; }

        /* Alert banner */
        .alert { padding: 12px 20px; background: #fef2f2; border-bottom: 1px solid #fecaca; color: #b91c1c; font-size: 13px; }

        /* Content */
        .content { flex: 1; overflow-y: auto; padding: 40px; max-width: 1200px; margin: 0 auto; width: 100%; }

        h1 { font-size: 24px; margin: 0 0 24px 0; }
        h2 { font-size: 18px; margin: 32px 0 16px 0; color: var(--text); }

        .card { background: white; border: 1px solid var(--border); border-radius: 8px; padding: 24px; margin-bottom: 24px; }

        .button { background: var(--primary); color: white; border: none; padding: 8px 16px; border-radius: 6px; font-weight: 500; cursor: pointer; font-size: 14px; }
        .button:hover { opacity: 0.9; }
        .button:disabled { opacity: 0.5; cursor: not-allowed; }

        code { background: #f3f4f6; padding: 2px 6px; border-radius: 4px; font-family: monospace; font-size: 13px; }

        pre { background: #1e293b; color: #e2e8f0; padding: 16px; border-radius: 6px; overflow-x: auto; font-size: 12px; }

        .info-box { background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 6px; padding: 16px; margin: 16px 0; }
    </style>
</head>
<body>
    <div class="header">
        <div class="brand">🔐 Admin Diagnostics Dashboard</div>
        <div class="notice">Secured with API Key</div>
    </div>

    <div class="alert" id="authAlert" style="display:none;">
        ⚠️ Authentication required. The dashboard is trying to load but may fail if X-API-Key header is not set.
    </div>

    <div class="content">
        <h1>System Diagnostics & Monitoring</h1>

        <div class="card">
            <h2>Dashboard Access Note</h2>
            <div class="info-box">
                <p><strong>Important:</strong> This dashboard requires admin authentication via <code>X-API-Key</code> header.</p>
                <p>The interactive features on this page make AJAX requests to secured endpoints. To use the dashboard:</p>
                <ol>
                    <li>Access this page with <code>X-API-Key: YOUR_ADMIN_KEY</code> header</li>
                    <li>Or use the full diagnostics endpoints via API (recommended for automation)</li>
                </ol>
                <p>Available endpoints (all require <code>X-API-Key</code> header):</p>
                <ul>
                    <li><code>GET /admin/diagnostics/traces</code> - Get recent traces</li>
                    <li><code>GET /admin/diagnostics/metrics</code> - Get system metrics</li>
                    <li><code>POST /admin/diagnostics/run</code> - Run integration tests</li>
                    <li><code>GET /admin/diagnostics/summary</code> - Get diagnostics summary</li>
                    <li><code>GET /admin/diagnostics/crash-log</code> - Get crash log</li>
                    <li><code>POST /admin/diagnostics/retention</code> - Run retention cleanup</li>
                    <li><code>GET /admin/diagnostics/mcp</code> - Get MCP health</li>
                </ul>
            </div>
        </div>

        <div class="card">
            <h2>Quick Actions</h2>
            <p>Use these commands to interact with the diagnostics API:</p>

            <h3 style="margin-top: 20px; font-size: 14px;">Get System Summary</h3>
            <pre>curl -H "X-API-Key: YOUR_KEY" http://localhost:8000/admin/diagnostics/summary</pre>

            <h3 style="margin-top: 20px; font-size: 14px;">Run Integration Tests</h3>
            <pre>curl -X POST -H "X-API-Key: YOUR_KEY" http://localhost:8000/admin/diagnostics/run</pre>

            <h3 style="margin-top: 20px; font-size: 14px;">Get Recent Traces</h3>
            <pre>curl -H "X-API-Key: YOUR_KEY" http://localhost:8000/admin/diagnostics/traces?limit=10</pre>

            <h3 style="margin-top: 20px; font-size: 14px;">Check MCP Health</h3>
            <pre>curl -H "X-API-Key: YOUR_KEY" http://localhost:8000/admin/diagnostics/mcp</pre>
        </div>

        <div class="card">
            <h2>Alternative: Use Original Diagnostics Dashboard</h2>
            <p>For the full interactive dashboard experience without authentication requirements, use the legacy endpoint:</p>
            <p><a href="/diagnostics" style="color: var(--primary);">/diagnostics</a> (No authentication required)</p>
            <p><em>Note: The legacy diagnostics endpoint will be deprecated in favor of this secured admin version.</em></p>
        </div>
    </div>
</body>
</html>
"""
    return html_content


__all__ = ["router"]
