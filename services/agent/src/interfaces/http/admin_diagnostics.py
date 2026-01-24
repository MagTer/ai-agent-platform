# ruff: noqa: E501
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
from interfaces.http.admin_auth import AdminUser, require_admin_or_redirect, verify_admin_user
from interfaces.http.admin_shared import render_admin_page

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platformadmin/diagnostics",
    tags=["platform-admin", "diagnostics"],
)


def get_diagnostics_service(
    settings: Settings = Depends(get_settings),
) -> DiagnosticsService:
    """Create diagnostics service instance."""
    return DiagnosticsService(settings)


@router.get("/traces", response_model=list[TraceGroup], dependencies=[Depends(verify_admin_user)])
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
        Requires admin role via Entra ID authentication.
    """
    return service.get_recent_traces(limit, show_all=show_all)


@router.get("/metrics", dependencies=[Depends(verify_admin_user)])
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
        Requires admin role via Entra ID authentication.
    """
    return service.get_system_health_metrics(window=window)


@router.post("/run", response_model=list[TestResult], dependencies=[Depends(verify_admin_user)])
async def run_diagnostics(
    service: DiagnosticsService = Depends(get_diagnostics_service),
) -> list[TestResult]:
    """Run integration tests on all system components.

    Returns:
        List of test results with component status and latency

    Security:
        Requires admin role via Entra ID authentication.
    """
    return await service.run_diagnostics()


@router.get("/summary", dependencies=[Depends(verify_admin_user)])
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
        Requires admin role via Entra ID authentication.
    """
    return await service.get_diagnostics_summary()


@router.get("/crash-log", dependencies=[Depends(verify_admin_user)])
async def get_crash_log() -> dict[str, Any]:
    """Expose last_crash.log for AI agent consumption.

    Returns:
        - exists: Whether the crash log file exists
        - content: The crash log content (if exists)
        - modified: When the file was last modified (if exists)

    This endpoint enables AI agents to autonomously read crash logs
    for troubleshooting without requiring file system access.

    Security:
        Requires admin role via Entra ID authentication.
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


@router.post("/retention", dependencies=[Depends(verify_admin_user)])
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
        Requires admin role via Entra ID authentication.
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


@router.get("/mcp", dependencies=[Depends(verify_admin_user)])
async def get_mcp_health() -> dict[str, Any]:
    """Get health status of all MCP server connections.

    Returns:
        - servers: Dict mapping server name to health info
        - connected_count: Number of connected servers
        - total_tools: Total tools across all servers

    This endpoint enables monitoring of MCP integrations.

    Security:
        Requires admin role via Entra ID authentication.
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


@router.get("/events", dependencies=[Depends(verify_admin_user)])
async def get_system_events(
    limit: int = 500,
    event_type: str | None = None,
    severity: str | None = None,
) -> dict[str, Any]:
    """Get system events that occurred outside of request context.

    These are security and system events that couldn't be attached to a trace,
    such as startup events, background job events, or events that occur before
    tracing is initialized.

    Args:
        limit: Maximum number of events to return (default 500).
        event_type: Filter by event type (e.g., AUTH_FAILURE, RATE_LIMIT_EXCEEDED).
        severity: Filter by severity (INFO, WARNING, ERROR, CRITICAL).

    Returns:
        - events: List of system events (newest first)
        - total_count: Total events in file
        - filters_applied: Applied filters

    Security:
        Requires admin role via Entra ID authentication.

    Note: Most security events during normal requests are attached to traces
    and available via /platformadmin/diagnostics/traces endpoint.
    """
    import json
    from collections import deque

    events_path = Path("data/system_events.jsonl")

    if not events_path.exists():
        return {
            "events": [],
            "total_count": 0,
            "filters_applied": {"event_type": event_type, "severity": severity},
            "message": "No system events file found",
        }

    try:
        # Read last N*2 lines to allow for filtering
        with events_path.open("r", encoding="utf-8") as f:
            lines = deque(f, maxlen=limit * 2)

        events = []
        for line in reversed(list(lines)):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                # Apply filters
                if event_type and event.get("event_type") != event_type:
                    continue
                if severity and event.get("severity") != severity.upper():
                    continue
                events.append(event)
                if len(events) >= limit:
                    break
            except json.JSONDecodeError:
                continue

        return {
            "events": events,
            "total_count": len(events),
            "filters_applied": {"event_type": event_type, "severity": severity},
        }
    except Exception as e:
        LOGGER.error(f"Failed to read system events: {e}")
        return {
            "events": [],
            "total_count": 0,
            "filters_applied": {"event_type": event_type, "severity": severity},
            "error": str(e),
        }


@router.get("/logs", dependencies=[Depends(verify_admin_user)])
async def get_application_logs(
    limit: int = 500,
    level: str | None = None,
    logger_name: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    """Get application logs (warnings, errors, and critical messages).

    These are standard Python logging messages from the application,
    captured at WARNING level and above.

    Args:
        limit: Maximum number of log entries to return (default 500).
        level: Filter by log level (WARNING, ERROR, CRITICAL).
        logger_name: Filter by logger name (e.g., "core.core.service").
        search: Search in log message text.

    Returns:
        - logs: List of log entries (newest first)
        - total_count: Number of entries returned
        - filters_applied: Applied filters

    Security:
        Requires admin role via Entra ID authentication.

    Note: Only WARNING level and above are written to file to reduce noise.
    """
    import json
    from collections import deque

    logs_path = Path("data/app_logs.jsonl")

    if not logs_path.exists():
        return {
            "logs": [],
            "total_count": 0,
            "filters_applied": {"level": level, "logger_name": logger_name, "search": search},
            "message": "No application logs file found",
        }

    try:
        # Read last N*2 lines to allow for filtering
        with logs_path.open("r", encoding="utf-8") as f:
            lines = deque(f, maxlen=limit * 2)

        logs = []
        for line in reversed(list(lines)):
            if not line.strip():
                continue
            try:
                log_entry = json.loads(line)
                # Apply filters
                if level and log_entry.get("level") != level.upper():
                    continue
                if logger_name and not log_entry.get("name", "").startswith(logger_name):
                    continue
                if search and search.lower() not in log_entry.get("message", "").lower():
                    continue
                logs.append(log_entry)
                if len(logs) >= limit:
                    break
            except json.JSONDecodeError:
                continue

        return {
            "logs": logs,
            "total_count": len(logs),
            "filters_applied": {"level": level, "logger_name": logger_name, "search": search},
        }
    except Exception as e:
        LOGGER.error(f"Failed to read application logs: {e}")
        return {
            "logs": [],
            "total_count": 0,
            "filters_applied": {"level": level, "logger_name": logger_name, "search": search},
            "error": str(e),
        }


@router.get("/", response_class=HTMLResponse)
async def diagnostics_dashboard(
    admin: AdminUser = Depends(require_admin_or_redirect),
) -> str:
    """Interactive diagnostics dashboard (HTML).

    Returns:
        HTML dashboard for monitoring system health

    Security:
        Requires admin role via Entra ID authentication.

    Note:
        The dashboard makes AJAX calls to other admin endpoints,
        so authentication must be provided via Entra ID headers.
    """
    content = """
        <h1 class="page-title">Diagnostics Dashboard</h1>
        <p style="color: var(--text-muted); margin-bottom: 24px;">
            System health monitoring, trace analysis, and component diagnostics
        </p>

        <div class="card">
            <div class="card-header">
                <span class="card-title">Quick Actions</span>
            </div>
            <div style="display: flex; gap: 12px; flex-wrap: wrap;">
                <button class="btn btn-primary" onclick="runDiagnostics()">Run Diagnostics</button>
                <button class="btn" onclick="loadTraces()">View Traces</button>
                <button class="btn" onclick="loadMetrics()">System Metrics</button>
                <button class="btn" onclick="loadEvents()">System Events</button>
            </div>
        </div>

        <div class="card">
            <div class="card-header">
                <span class="card-title">Status</span>
            </div>
            <div id="status-content">
                <div class="loading">Loading diagnostics data...</div>
            </div>
        </div>

        <div class="card">
            <div class="card-header">
                <span class="card-title">Details</span>
            </div>
            <div id="details-content">
                <div style="color: var(--text-muted); padding: 20px; text-align: center;">
                    Select a quick action above to view details
                </div>
            </div>
        </div>
    """

    extra_css = """
        .trace-item { padding: 12px; border: 1px solid var(--border); border-radius: 6px; margin-bottom: 8px; }
        .trace-id { font-family: monospace; font-size: 11px; color: var(--text-muted); }
        .metric-box { padding: 16px; border: 1px solid var(--border); border-radius: 8px; margin-bottom: 12px; }
    """

    extra_js = """
        async function runDiagnostics() {
            const statusEl = document.getElementById('status-content');
            const detailsEl = document.getElementById('details-content');
            statusEl.innerHTML = '<div class="loading">Running diagnostics tests...</div>';
            detailsEl.innerHTML = '';

            try {
                const res = await fetch('/platformadmin/diagnostics/run', { method: 'POST' });
                const tests = await res.json();

                statusEl.innerHTML = tests.map(t => `
                    <div class="metric-box">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <strong>${escapeHtml(t.component)}</strong>
                            <span class="badge ${t.status === 'ok' ? 'badge-success' : 'badge-error'}">
                                ${t.status.toUpperCase()}
                            </span>
                        </div>
                        ${t.error ? `<div style="color: var(--error); margin-top: 8px; font-size: 12px;">${escapeHtml(t.error)}</div>` : ''}
                        ${t.latency_ms ? `<div style="color: var(--text-muted); margin-top: 4px; font-size: 12px;">Latency: ${t.latency_ms}ms</div>` : ''}
                    </div>
                `).join('');
            } catch (e) {
                statusEl.innerHTML = '<div style="color: var(--error);">Failed to run diagnostics</div>';
            }
        }

        async function loadTraces() {
            const detailsEl = document.getElementById('details-content');
            detailsEl.innerHTML = '<div class="loading">Loading traces...</div>';

            try {
                const res = await fetch('/platformadmin/diagnostics/traces?limit=50');
                const traces = await res.json();

                if (traces.length === 0) {
                    detailsEl.innerHTML = '<div class="loading">No traces found</div>';
                    return;
                }

                detailsEl.innerHTML = traces.map(t => `
                    <div class="trace-item">
                        <div class="trace-id">${t.trace_id}</div>
                        <div style="margin-top: 4px; font-size: 13px;">${escapeHtml(t.name || 'Unknown')}</div>
                        <div style="margin-top: 4px; font-size: 12px; color: var(--text-muted);">
                            Status: ${t.status} | Duration: ${t.duration_ms}ms | Spans: ${t.span_count}
                        </div>
                    </div>
                `).join('');
            } catch (e) {
                detailsEl.innerHTML = '<div style="color: var(--error);">Failed to load traces</div>';
            }
        }

        async function loadMetrics() {
            const detailsEl = document.getElementById('details-content');
            detailsEl.innerHTML = '<div class="loading">Loading metrics...</div>';

            try {
                const res = await fetch('/platformadmin/diagnostics/metrics');
                const data = await res.json();

                detailsEl.innerHTML = `
                    <div class="metric-box">
                        <strong>Error Rate</strong>
                        <div style="font-size: 24px; color: var(--primary); margin-top: 8px;">
                            ${(data.error_rate * 100).toFixed(1)}%
                        </div>
                    </div>
                    <div class="metric-box">
                        <strong>Average Latency</strong>
                        <div style="font-size: 24px; color: var(--primary); margin-top: 8px;">
                            ${data.avg_latency_ms?.toFixed(0) || 0}ms
                        </div>
                    </div>
                `;
            } catch (e) {
                detailsEl.innerHTML = '<div style="color: var(--error);">Failed to load metrics</div>';
            }
        }

        async function loadEvents() {
            const detailsEl = document.getElementById('details-content');
            detailsEl.innerHTML = '<div class="loading">Loading events...</div>';

            try {
                const res = await fetch('/platformadmin/diagnostics/events?limit=50');
                const data = await res.json();

                if (!data.events || data.events.length === 0) {
                    detailsEl.innerHTML = '<div class="loading">No system events found</div>';
                    return;
                }

                detailsEl.innerHTML = data.events.map(e => `
                    <div class="trace-item">
                        <div style="display: flex; justify-content: space-between;">
                            <strong>${escapeHtml(e.event_type || 'Unknown')}</strong>
                            <span class="badge ${e.severity === 'ERROR' ? 'badge-error' : e.severity === 'WARNING' ? 'badge-warning' : 'badge-info'}">
                                ${e.severity}
                            </span>
                        </div>
                        <div style="margin-top: 8px; font-size: 13px;">${escapeHtml(e.message || '')}</div>
                        <div style="margin-top: 4px; font-size: 12px; color: var(--text-muted);">
                            ${new Date(e.timestamp).toLocaleString()}
                        </div>
                    </div>
                `).join('');
            } catch (e) {
                detailsEl.innerHTML = '<div style="color: var(--error);">Failed to load events</div>';
            }
        }

        function escapeHtml(str) {
            if (!str) return '';
            const div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        }

        // Auto-load status on page load
        runDiagnostics();
    """

    return render_admin_page(
        title="Diagnostics",
        active_page="/platformadmin/diagnostics/",
        content=content,
        user_name=admin.display_name or admin.email.split("@")[0],
        user_email=admin.email,
        breadcrumbs=[("Diagnostics", "#")],
        extra_css=extra_css,
        extra_js=extra_js,
    )


__all__ = ["router"]
