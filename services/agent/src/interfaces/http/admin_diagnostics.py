# ruff: noqa: E501
"""Admin diagnostics endpoints (secured version of diagnostics router)."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from core.core.config import Settings, get_settings
from core.diagnostics.service import DiagnosticsService, TestResult, TraceGroup
from interfaces.http.admin_auth import AdminUser, require_admin_or_redirect, verify_admin_user
from interfaces.http.admin_shared import UTF8HTMLResponse, render_admin_page

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


def _get_diagnostics_content() -> str:
    """Return the main content HTML for the diagnostics dashboard."""
    return """
        <!-- Toolbar with tabs and actions -->
        <div class="diag-toolbar">
            <div class="diag-tab-nav">
                <div id="tab-traces" class="diag-nav-item active" onclick="switchTab('traces')">Trace Waterfall</div>
                <div id="tab-metrics" class="diag-nav-item" onclick="switchTab('metrics')">Metrics &amp; Insights</div>
                <div id="tab-health" class="diag-nav-item" onclick="switchTab('health')">System Health</div>
                <div id="tab-logs" class="diag-nav-item" onclick="switchTab('logs')">Logs &amp; Events</div>
            </div>
            <div class="diag-toolbar-actions">
                <button onclick="viewCrashLog()" class="btn">View Crash Log</button>
                <button onclick="refreshCurrent()" class="btn">Refresh</button>
            </div>
        </div>

        <!-- Trace Screen -->
        <div class="diag-screen" id="view-traces" style="display:flex">
            <div class="diag-sidebar">
                <div class="diag-sidebar-header">
                    <div style="display:flex; justify-content:space-between; align-items:center; width:100%">
                        <span>RECENT REQUESTS</span>
                        <span id="trace-count">0</span>
                    </div>
                    <input type="text" id="traceSearch" placeholder="Search by Trace ID..."
                           oninput="onTraceSearchInput()"
                           class="diag-search-input">
                    <label class="diag-checkbox-label">
                        <input type="checkbox" id="showAllTraces" onchange="loadTraces()">
                        Show diagnostic/health traces
                    </label>
                </div>
                <div class="diag-request-list" id="reqList"></div>
            </div>

            <div class="diag-main">
                <div id="emptyState" class="diag-empty-state">
                    <div style="font-size:40px; margin-bottom:10px">Select a request</div>
                    <div>Select a request to view details</div>
                </div>

                <div id="detailView" class="diag-trace-detail diag-hidden">
                    <div class="diag-detail-header">
                        <div class="diag-dh-title" id="dTitle">Query...</div>
                        <div class="diag-dh-meta">
                            <span id="dId">ID</span>
                            <span id="dTime">Time</span>
                            <span id="dDur">Duration</span>
                        </div>
                    </div>

                    <div class="diag-waterfall-scroll">
                        <div class="diag-waterfall-canvas" id="waterfall"></div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Health Screen -->
        <div class="diag-screen diag-health-screen" id="view-health">
            <div style="max-width: 1000px; margin: 0 auto;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:24px">
                    <div>
                        <h2 class="diag-section-title" style="margin:0">Component Health Status</h2>
                        <div style="color:var(--text-muted); font-size:13px; margin-top:4px">Real-time integration tests</div>
                    </div>
                    <button onclick="runHealthChecks()" class="btn btn-primary">Run Integration Tests</button>
                </div>

                <div class="diag-health-grid" id="healthGrid">
                    <div style="grid-column: 1/-1; padding:40px; text-align:center; color:var(--text-muted); border: 2px dashed var(--border); border-radius:8px;">
                        Click "Run Integration Tests" to start probing.
                    </div>
                </div>

                <h2 class="diag-section-title" style="margin-top:40px; margin-bottom:16px">MCP Server Status</h2>
                <div id="mcpStatusContainer">
                    <div style="padding:40px; text-align:center; color:var(--text-muted); border: 2px dashed var(--border); border-radius:8px; background:white;">
                        Loading MCP server status...
                    </div>
                </div>
            </div>
        </div>

        <!-- Metrics Screen -->
        <div class="diag-screen diag-health-screen" id="view-metrics">
            <div style="max-width: 1000px; margin: 0 auto;">
                <h2 class="diag-section-title">System Metrics (Last 60 Traces)</h2>
                <div class="diag-metric-cards">
                    <div class="diag-m-card">
                        <div class="diag-m-title">Total Requests</div>
                        <div class="diag-m-value" id="mTotal">-</div>
                    </div>
                    <div class="diag-m-card">
                        <div class="diag-m-title">Error Rate</div>
                        <div class="diag-m-value" id="mRate">-</div>
                    </div>
                    <div class="diag-m-card">
                        <div class="diag-m-title">Failed Requests</div>
                        <div class="diag-m-value" id="mCount">-</div>
                    </div>
                </div>

                <h2 class="diag-section-title">Insights: Failing Components</h2>
                <table class="diag-table" id="hotspotsTable">
                    <thead>
                        <tr>
                            <th style="width:200px">Component / Tool</th>
                            <th style="width:100px">Failures</th>
                            <th>Top Error Reasons</th>
                        </tr>
                    </thead>
                    <tbody id="hotspotsBody">
                        <tr><td colspan="3" style="text-align:center; color:#999">Loading...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Logs & Events Screen -->
        <div class="diag-screen diag-health-screen" id="view-logs">
            <div style="max-width: 1200px; margin: 0 auto;">
                <h2 class="diag-section-title">Application Logs</h2>
                <div style="margin-bottom:16px; display:flex; gap:12px; align-items:center">
                    <select id="logLevelFilter" onchange="loadLogs()" class="diag-select">
                        <option value="">All Levels</option>
                        <option value="WARNING">WARNING</option>
                        <option value="ERROR">ERROR</option>
                        <option value="CRITICAL">CRITICAL</option>
                    </select>
                    <input type="text" id="logSearchBox" placeholder="Search logs..." oninput="loadLogs()" class="diag-search-input" style="flex:1; max-width:400px">
                </div>
                <div style="overflow-x:auto; margin-bottom:40px">
                    <table class="diag-table" id="logsTable">
                        <thead>
                            <tr>
                                <th style="width:150px">Timestamp</th>
                                <th style="width:80px">Level</th>
                                <th style="width:200px">Logger</th>
                                <th>Message</th>
                            </tr>
                        </thead>
                        <tbody id="logsBody">
                            <tr><td colspan="4" style="text-align:center; color:#999">Loading...</td></tr>
                        </tbody>
                    </table>
                </div>

                <h2 class="diag-section-title">System Events</h2>
                <div style="margin-bottom:16px; display:flex; gap:12px; align-items:center">
                    <select id="eventTypeFilter" onchange="loadEvents()" class="diag-select">
                        <option value="">All Event Types</option>
                    </select>
                    <select id="eventSeverityFilter" onchange="loadEvents()" class="diag-select">
                        <option value="">All Severities</option>
                        <option value="INFO">INFO</option>
                        <option value="WARNING">WARNING</option>
                        <option value="ERROR">ERROR</option>
                        <option value="CRITICAL">CRITICAL</option>
                    </select>
                </div>
                <div style="overflow-x:auto">
                    <table class="diag-table" id="eventsTable">
                        <thead>
                            <tr>
                                <th style="width:150px">Timestamp</th>
                                <th style="width:150px">Event Type</th>
                                <th style="width:100px">Severity</th>
                                <th>Details</th>
                            </tr>
                        </thead>
                        <tbody id="eventsBody">
                            <tr><td colspan="4" style="text-align:center; color:#999">Loading...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Drawer -->
        <div class="diag-drawer" id="attrDrawer">
            <div class="diag-drawer-header">
                <h3 style="margin:0; font-size:14px;">Span Details</h3>
                <div class="diag-close-drawer" onclick="closeDrawer()">&times;</div>
            </div>
            <div class="diag-drawer-content" id="drawerContent"></div>
        </div>

        <!-- Crash Log Modal -->
        <div class="diag-modal" id="crashLogModal" onclick="if(event.target===this) closeCrashLogModal()">
            <div class="diag-modal-content">
                <div class="diag-modal-header">
                    <h3 style="margin:0; font-size:16px; font-weight:600">Crash Log</h3>
                    <div class="diag-close-drawer" onclick="closeCrashLogModal()">&times;</div>
                </div>
                <div class="diag-modal-body">
                    <div id="crashLogContent">Loading...</div>
                </div>
            </div>
        </div>
    """


def _get_diagnostics_css() -> str:
    """Return CSS specific to the diagnostics dashboard."""
    return """
        /* Override admin-content padding for full-height layout */
        .admin-content {
            padding: 0;
            display: flex;
            flex-direction: column;
            height: calc(100vh - var(--header-height));
            overflow: hidden;
        }

        /* Toolbar */
        .diag-toolbar {
            background: var(--bg-card);
            border-bottom: 1px solid var(--border);
            padding: 0 20px;
            height: 56px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-shrink: 0;
        }

        .diag-tab-nav {
            display: flex;
            gap: 24px;
            font-size: 13px;
            font-weight: 500;
            height: 100%;
        }

        .diag-nav-item {
            display: flex;
            align-items: center;
            cursor: pointer;
            border-bottom: 2px solid transparent;
            color: var(--text-muted);
            transition: all 0.2s;
            padding: 0 4px;
        }

        .diag-nav-item:hover { color: var(--primary); }
        .diag-nav-item.active { border-bottom-color: var(--primary); color: var(--primary); }

        .diag-toolbar-actions { display: flex; gap: 10px; }

        /* Screens */
        .diag-screen { display: none; flex: 1; overflow: hidden; }
        .diag-health-screen { padding: 40px; background: #fafafa; overflow-y: auto; }
        #view-traces { flex-direction: row; }

        /* Sidebar */
        .diag-sidebar {
            width: 350px;
            background: var(--bg-card);
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
            flex-shrink: 0;
        }

        .diag-sidebar-header {
            padding: 12px;
            border-bottom: 1px solid var(--border);
            background: #f9fafb;
            font-size: 12px;
            font-weight: 600;
            color: var(--text-muted);
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        .diag-search-input {
            width: 100%;
            padding: 6px 10px;
            border: 1px solid var(--border);
            border-radius: 4px;
            font-size: 12px;
            box-sizing: border-box;
        }

        .diag-checkbox-label {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 11px;
            color: var(--text-muted);
            cursor: pointer;
            font-weight: normal;
        }

        .diag-select {
            padding: 6px 10px;
            border: 1px solid var(--border);
            border-radius: 4px;
            font-size: 13px;
        }

        .diag-request-list { flex: 1; overflow-y: auto; }

        .diag-req-card {
            padding: 16px;
            border-bottom: 1px solid var(--border);
            cursor: pointer;
            transition: all 0.1s;
            border-left: 3px solid transparent;
        }

        .diag-req-card:hover { background: #f9fafb; }
        .diag-req-card.active { background: #eff6ff; border-left-color: var(--primary); }
        .diag-req-card.error { border-left-color: var(--error); background: #fef2f2; }

        .diag-req-top { display: flex; justify-content: space-between; margin-bottom: 6px; }

        .diag-req-status {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--text-muted);
            display: inline-block;
        }

        .diag-req-status.ok { background: var(--success); }
        .diag-req-status.err { background: var(--error); }

        .diag-req-time { font-size: 11px; color: var(--text-muted); font-weight: 500; }

        .diag-req-query {
            font-size: 13px;
            font-weight: 500;
            margin-bottom: 8px;
            line-height: 1.4;
            overflow: hidden;
            text-overflow: ellipsis;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
        }

        .diag-req-meta { display: flex; gap: 12px; font-size: 11px; color: var(--text-muted); }
        .diag-badge { background: #f3f4f6; padding: 2px 6px; border-radius: 4px; font-weight: 500; }

        /* Main view */
        .diag-main {
            flex: 1;
            display: flex;
            flex-direction: column;
            background: var(--bg-card);
            overflow: hidden;
            position: relative;
        }

        .diag-empty-state {
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            color: var(--text-muted);
            flex-direction: column;
        }

        .diag-hidden { display: none !important; }

        .diag-trace-detail { display: flex; flex-direction: column; height: 100%; }

        .diag-detail-header {
            padding: 20px;
            border-bottom: 1px solid var(--border);
            background: var(--bg-card);
        }

        .diag-dh-title { font-size: 18px; font-weight: 600; margin-bottom: 8px; }
        .diag-dh-meta { display: flex; gap: 20px; font-size: 12px; color: var(--text-muted); font-family: monospace; }

        .diag-waterfall-scroll { flex: 1; overflow-y: auto; padding: 20px; position: relative; background: #fafafa; }
        .diag-waterfall-canvas { position: relative; min-height: 200px; }

        .diag-span-row { position: relative; height: 32px; margin-bottom: 4px; }

        .diag-span-bar {
            position: absolute;
            height: 24px;
            border-radius: 4px;
            font-size: 11px;
            color: white;
            display: flex;
            align-items: center;
            padding: 0 8px;
            overflow: hidden;
            white-space: nowrap;
            cursor: pointer;
            box-shadow: 0 1px 2px rgba(0,0,0,0.05);
            transition: opacity 0.2s;
        }

        .diag-span-bar:hover { opacity: 0.9; z-index: 10; }

        .diag-bg-ai { background: #3b82f6; }
        .diag-bg-tool { background: #14b8a6; }
        .diag-bg-db { background: #f59e0b; }
        .diag-bg-err { background: #ef4444; }
        .diag-bg-def { background: #9ca3af; }

        /* Tables */
        .diag-table {
            width: 100%;
            border-collapse: collapse;
            background: white;
            border-radius: 8px;
            overflow: hidden;
            border: 1px solid var(--border);
            font-size: 13px;
        }

        .diag-table th {
            text-align: left;
            padding: 12px 16px;
            background: #f9fafb;
            font-weight: 600;
            color: var(--text-muted);
            border-bottom: 1px solid var(--border);
        }

        .diag-table td {
            padding: 12px 16px;
            border-bottom: 1px solid var(--border);
            vertical-align: top;
        }

        .diag-table tr:last-child td { border-bottom: none; }

        .diag-reason-tag {
            display: inline-block;
            background: #fef2f2;
            color: #b91c1c;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 11px;
            margin-right: 4px;
            border: 1px solid #fecaca;
            margin-bottom: 4px;
        }

        /* Health & Metrics */
        .diag-section-title { font-size: 16px; font-weight: 600; margin-bottom: 16px; color: var(--text); }

        .diag-metric-cards {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 32px;
        }

        .diag-m-card {
            background: white;
            padding: 24px;
            border-radius: 8px;
            border: 1px solid var(--border);
            box-shadow: 0 1px 2px rgba(0,0,0,0.05);
        }

        .diag-m-title { color: var(--text-muted); font-size: 13px; font-weight: 500; margin-bottom: 8px; text-transform: uppercase; }
        .diag-m-value { font-size: 28px; font-weight: 700; color: var(--text); }
        .diag-m-card.bad .diag-m-value { color: var(--error); }

        .diag-health-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; }

        .diag-health-card {
            background: white;
            padding: 20px;
            border-radius: 8px;
            border: 1px solid var(--border);
            border-top-width: 4px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }

        .diag-health-card.ok { border-top-color: var(--success); }
        .diag-health-card.fail { border-top-color: var(--error); }

        /* Drawer */
        .diag-drawer {
            position: fixed;
            bottom: -350px;
            left: 220px;
            right: 0;
            height: 350px;
            background: white;
            border-top: 1px solid var(--border);
            box-shadow: 0 -4px 15px rgba(0,0,0,0.1);
            transition: bottom 0.3s cubic-bezier(0.16, 1, 0.3, 1);
            z-index: 100;
            display: flex;
            flex-direction: column;
        }

        .diag-drawer.open { bottom: 0; }

        .diag-drawer-header {
            padding: 16px 20px;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: var(--bg-card);
        }

        .diag-drawer-content {
            flex: 1;
            overflow-y: auto;
            padding: 20px;
            background: #f9fafb;
            display: flex;
            gap: 24px;
        }

        .diag-drawer-attrs { flex: 1; min-width: 300px; }
        .diag-drawer-json { flex: 1; min-width: 300px; }

        .diag-attr-card {
            background: white;
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 12px;
            margin-bottom: 8px;
        }

        .diag-attr-label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; margin-bottom: 4px; }
        .diag-attr-value { font-size: 13px; font-weight: 500; word-break: break-all; }

        #drawerPre {
            background: #1e293b;
            color: #e2e8f0;
            padding: 12px;
            border-radius: 6px;
            font-size: 11px;
            overflow-x: auto;
            font-family: 'Menlo', monospace;
            max-height: 250px;
        }

        .diag-close-drawer {
            cursor: pointer;
            font-size: 20px;
            color: var(--text-muted);
            width: 32px;
            height: 32px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 4px;
        }

        .diag-close-drawer:hover { background: var(--bg); }

        /* Modal */
        .diag-modal {
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0,0,0,0.5);
            align-items: center;
            justify-content: center;
        }

        .diag-modal.open { display: flex; }

        .diag-modal-content {
            background: white;
            border-radius: 8px;
            width: 90%;
            max-width: 900px;
            max-height: 80vh;
            display: flex;
            flex-direction: column;
            box-shadow: 0 4px 20px rgba(0,0,0,0.2);
        }

        .diag-modal-header {
            padding: 20px;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .diag-modal-body { padding: 20px; overflow-y: auto; flex: 1; }

        .diag-modal-pre {
            background: #1e293b;
            color: #e2e8f0;
            padding: 16px;
            border-radius: 6px;
            font-size: 12px;
            overflow-x: auto;
            font-family: 'Menlo', monospace;
            white-space: pre-wrap;
        }
    """


def _get_diagnostics_js() -> str:
    """Return JavaScript for the diagnostics dashboard."""
    return """
        const API_BASE = '/platformadmin/diagnostics';

        let currentTab = 'traces';
        let traceGroups = [];
        let selectedTraceId = null;

        window.switchTab = switchTab;
        window.refreshCurrent = refreshCurrent;
        window.runHealthChecks = runHealthChecks;
        window.closeDrawer = closeDrawer;
        window.viewCrashLog = viewCrashLog;
        window.closeCrashLogModal = closeCrashLogModal;
        window.loadLogs = loadLogs;
        window.loadEvents = loadEvents;
        window.loadTraces = loadTraces;
        window.onTraceSearchInput = onTraceSearchInput;

        loadTraces();

        function switchTab(tab) {
            currentTab = tab;
            document.querySelectorAll('.diag-nav-item').forEach(el => el.classList.remove('active'));
            document.getElementById(`tab-${tab}`).classList.add('active');

            document.querySelectorAll('.diag-screen').forEach(el => el.style.display = 'none');
            const view = document.getElementById(`view-${tab}`);

            if (tab === 'traces') {
                view.style.display = 'flex';
                loadTraces();
            } else {
                view.style.display = 'block';
                if (tab === 'metrics') loadMetrics();
                else if (tab === 'health') loadMcpStatus();
                else if (tab === 'logs') {
                    loadLogs();
                    loadEvents();
                }
            }
        }

        async function refreshCurrent() {
            const btn = event?.target;
            if (btn) { btn.disabled = true; btn.innerText = 'Loading...'; }
            try {
                if (currentTab === 'traces') await loadTraces();
                else if (currentTab === 'metrics') await loadMetrics();
                else if (currentTab === 'health') await loadMcpStatus();
                else if (currentTab === 'logs') { await loadLogs(); await loadEvents(); }
            } finally {
                if (btn) { btn.disabled = false; btn.innerText = 'Refresh'; }
            }
        }

        async function loadMetrics() {
            try {
                const res = await fetch(`${API_BASE}/metrics?window=60`);
                const data = await res.json();

                document.getElementById('mTotal').innerText = data.metrics?.total_requests || 0;
                document.getElementById('mCount').innerText = data.metrics?.error_count || 0;

                const rateEl = document.getElementById('mRate');
                const rate = ((data.metrics?.error_rate || 0) * 100).toFixed(1) + '%';
                rateEl.innerText = rate;
                if ((data.metrics?.error_rate || 0) > 0.1) rateEl.parentElement.classList.add('bad');
                else rateEl.parentElement.classList.remove('bad');

                const tbody = document.getElementById('hotspotsBody');
                tbody.innerHTML = '';

                if ((data.metrics?.error_count || 0) === 0) {
                    tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; padding:30px; color:#999">No errors detected.</td></tr>';
                    return;
                }

                if (data.insights && data.insights.hotspots) {
                    data.insights.hotspots.forEach(h => {
                        const tr = document.createElement('tr');
                        let reasonsHtml = h.top_reasons.map(r => `<span class="diag-reason-tag">${escapeHtml(r)}</span>`).join('');
                        tr.innerHTML = `<td style="font-weight:600">${escapeHtml(h.name)}</td><td>${h.count}</td><td>${reasonsHtml}</td>`;
                        tbody.appendChild(tr);
                    });
                }
            } catch (e) { console.error(e); }
        }

        async function runHealthChecks() {
            const grid = document.getElementById('healthGrid');
            grid.innerHTML = '<div style="padding:20px; text-align:center">Running integration tests...</div>';

            try {
                const res = await fetch(`${API_BASE}/run`, {method: 'POST'});
                const results = await res.json();
                grid.innerHTML = '';

                results.forEach(r => {
                    const isOk = r.status === 'ok';
                    const el = document.createElement('div');
                    el.className = `diag-health-card ${isOk ? 'ok' : 'fail'}`;
                    el.innerHTML = `
                        <div style="display:flex; justify-content:space-between; margin-bottom:10px">
                            <span style="font-weight:600; font-size:14px">${escapeHtml(r.component)}</span>
                            <span style="font-size:10px; font-weight:bold; padding:2px 6px; border-radius:4px; color:white"
                                  class="${isOk ? 'diag-bg-tool' : 'diag-bg-err'}">${isOk ? 'Active' : 'Failed'}</span>
                        </div>
                        <div style="font-size:24px; font-weight:700; margin-bottom:4px">${r.latency_ms.toFixed(0)}<span style="font-size:12px; font-weight:400; color:#999; margin-left:4px">ms</span></div>
                        ${!isOk && r.message ? `<div style="font-size:12px; color:var(--error); margin-top:8px">${escapeHtml(r.message)}</div>` : ''}
                    `;
                    grid.appendChild(el);
                });
            } catch (e) {
                grid.innerHTML = `<div style="color:red">Failed: ${e}</div>`;
            }
        }

        async function loadMcpStatus() {
            const container = document.getElementById('mcpStatusContainer');
            try {
                const res = await fetch(`${API_BASE}/mcp`);
                const data = await res.json();
                container.innerHTML = '';

                if (!data.servers || Object.keys(data.servers).length === 0) {
                    container.innerHTML = '<div style="padding:40px; text-align:center; color:var(--text-muted); border: 2px dashed var(--border); border-radius:8px; background:white;">No MCP servers configured</div>';
                    return;
                }

                const grid = document.createElement('div');
                grid.className = 'diag-health-grid';

                Object.entries(data.servers).forEach(([name, info]) => {
                    const isConnected = info.connected;
                    const card = document.createElement('div');
                    card.className = `diag-health-card ${isConnected ? 'ok' : 'fail'}`;
                    card.innerHTML = `
                        <div style="display:flex; justify-content:space-between; margin-bottom:10px">
                            <span style="font-weight:600; font-size:14px">${escapeHtml(name)}</span>
                            <span style="font-size:10px; font-weight:bold; padding:2px 6px; border-radius:4px; color:white"
                                  class="${isConnected ? 'diag-bg-tool' : 'diag-bg-err'}">${isConnected ? 'Connected' : 'Disconnected'}</span>
                        </div>
                        <div style="font-size:24px; font-weight:700; margin-bottom:4px">${info.tools_count || 0}<span style="font-size:12px; font-weight:400; color:#999; margin-left:4px">tools</span></div>
                        ${info.error ? `<div style="font-size:12px; color:var(--error); margin-top:8px">${escapeHtml(info.error)}</div>` : ''}
                    `;
                    grid.appendChild(card);
                });
                container.appendChild(grid);
            } catch (e) {
                container.innerHTML = `<div style="color:red; padding:20px">Failed: ${e}</div>`;
            }
        }

        async function loadLogs() {
            const tbody = document.getElementById('logsBody');
            const level = document.getElementById('logLevelFilter')?.value || '';
            const search = document.getElementById('logSearchBox')?.value || '';

            try {
                let url = `${API_BASE}/logs?limit=100`;
                if (level) url += `&level=${encodeURIComponent(level)}`;
                if (search) url += `&search=${encodeURIComponent(search)}`;

                const res = await fetch(url);
                const data = await res.json();
                tbody.innerHTML = '';

                if (!data.logs || data.logs.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:30px; color:#999">No logs found</td></tr>';
                    return;
                }

                data.logs.forEach(log => {
                    const tr = document.createElement('tr');
                    const levelColor = log.level === 'CRITICAL' ? 'var(--error)' : log.level === 'ERROR' ? '#f59e0b' : 'var(--text-muted)';
                    tr.innerHTML = `
                        <td style="font-family:monospace; font-size:11px">${escapeHtml(log.timestamp || '')}</td>
                        <td><span style="color:${levelColor}; font-weight:600; font-size:11px">${escapeHtml(log.level || '')}</span></td>
                        <td style="font-family:monospace; font-size:11px">${escapeHtml(log.name || '')}</td>
                        <td style="font-size:12px">${escapeHtml(log.message || '')}</td>
                    `;
                    tbody.appendChild(tr);
                });
            } catch (e) {
                tbody.innerHTML = `<tr><td colspan="4" style="color:red; padding:20px">Failed: ${e}</td></tr>`;
            }
        }

        async function loadEvents() {
            const tbody = document.getElementById('eventsBody');
            const eventType = document.getElementById('eventTypeFilter')?.value || '';
            const severity = document.getElementById('eventSeverityFilter')?.value || '';

            try {
                let url = `${API_BASE}/events?limit=100`;
                if (eventType) url += `&event_type=${encodeURIComponent(eventType)}`;
                if (severity) url += `&severity=${encodeURIComponent(severity)}`;

                const res = await fetch(url);
                const data = await res.json();
                tbody.innerHTML = '';

                if (!data.events || data.events.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:30px; color:#999">No events found</td></tr>';
                    return;
                }

                const eventTypeFilter = document.getElementById('eventTypeFilter');
                if (eventTypeFilter.options.length === 1) {
                    const eventTypes = new Set(data.events.map(e => e.event_type).filter(Boolean));
                    eventTypes.forEach(type => {
                        const option = document.createElement('option');
                        option.value = type;
                        option.textContent = type;
                        eventTypeFilter.appendChild(option);
                    });
                }

                data.events.forEach(event => {
                    const tr = document.createElement('tr');
                    const severityColor = event.severity === 'CRITICAL' || event.severity === 'ERROR' ? 'var(--error)' : event.severity === 'WARNING' ? '#f59e0b' : 'var(--text)';
                    const details = typeof event.details === 'object' ? JSON.stringify(event.details) : event.details || '';
                    tr.innerHTML = `
                        <td style="font-family:monospace; font-size:11px">${escapeHtml(event.timestamp || '')}</td>
                        <td style="font-weight:600; font-size:11px">${escapeHtml(event.event_type || '')}</td>
                        <td><span style="color:${severityColor}; font-weight:600; font-size:11px">${escapeHtml(event.severity || '')}</span></td>
                        <td style="font-size:12px">${escapeHtml(details)}</td>
                    `;
                    tbody.appendChild(tr);
                });
            } catch (e) {
                tbody.innerHTML = `<tr><td colspan="4" style="color:red; padding:20px">Failed: ${e}</td></tr>`;
            }
        }

        async function viewCrashLog() {
            const modal = document.getElementById('crashLogModal');
            const content = document.getElementById('crashLogContent');
            content.innerHTML = 'Loading...';
            modal.classList.add('open');

            try {
                const res = await fetch(`${API_BASE}/crash-log`);
                const data = await res.json();

                if (!data.exists) {
                    content.innerHTML = '<div style="padding:20px; text-align:center; color:var(--text-muted)">No crash log found</div>';
                    return;
                }

                const pre = document.createElement('pre');
                pre.className = 'diag-modal-pre';
                pre.textContent = data.content;
                content.innerHTML = '';
                content.appendChild(pre);

                if (data.modified) {
                    const timestamp = document.createElement('div');
                    timestamp.style.cssText = 'font-size:12px; color:var(--text-muted); margin-top:12px';
                    timestamp.textContent = `Last modified: ${new Date(data.modified).toLocaleString()}`;
                    content.appendChild(timestamp);
                }
            } catch (e) {
                content.innerHTML = `<div style="color:red; padding:20px">Failed: ${e}</div>`;
            }
        }

        function closeCrashLogModal() { document.getElementById('crashLogModal').classList.remove('open'); }

        let filteredTraceGroups = [];

        async function loadTraces(searchTraceId = null) {
            const list = document.getElementById('reqList');
            const showAll = document.getElementById('showAllTraces')?.checked || false;
            try {
                let url = `${API_BASE}/traces?limit=500&show_all=${showAll}`;
                if (searchTraceId && searchTraceId.length >= 8) url += `&trace_id=${encodeURIComponent(searchTraceId)}`;

                const res = await fetch(url);
                if (!res.ok) throw new Error("API " + res.status);
                traceGroups = await res.json();
                filterTraces();

                if (selectedTraceId) {
                    const idx = traceGroups.findIndex(g => g.trace_id === selectedTraceId);
                    if (idx >= 0) selectTrace(idx);
                }
            } catch (e) {
                list.innerHTML = `<div style="padding:20px; color:red">Error: ${e}</div>`;
            }
        }

        function filterTraces() {
            const query = (document.getElementById('traceSearch')?.value || '').toLowerCase();
            filteredTraceGroups = query ? traceGroups.filter(g => g.trace_id.toLowerCase().includes(query)) : traceGroups;
            renderTraceList(document.getElementById('reqList'));
        }

        let searchTimeout = null;
        function onTraceSearchInput() {
            const query = document.getElementById('traceSearch')?.value || '';
            clearTimeout(searchTimeout);
            if (query.length >= 8 && /^[a-f0-9]+$/i.test(query)) {
                searchTimeout = setTimeout(() => loadTraces(query), 300);
            } else {
                filterTraces();
            }
        }

        function renderTraceList(list) {
            list.innerHTML = '';
            document.getElementById('trace-count').innerText = filteredTraceGroups.length;

            filteredTraceGroups.forEach((g, idx) => {
                const el = document.createElement('div');
                el.className = `diag-req-card ${g.status === 'ERR' ? 'error' : ''}`;
                const originalIdx = traceGroups.findIndex(t => t.trace_id === g.trace_id);
                el.onclick = () => selectTrace(originalIdx);

                const time = new Date(g.start_time).toLocaleTimeString();
                const intent = extractUserIntent(g.root);

                el.innerHTML = `
                    <div class="diag-req-top">
                        <span class="diag-req-status ${g.status === 'ERR' ? 'err' : 'ok'}"></span>
                        <span class="diag-req-time">${time}</span>
                    </div>
                    <div class="diag-req-query">${escapeHtml(intent)}</div>
                    <div class="diag-req-meta">
                        <span class="diag-badge" title="${g.trace_id}" style="font-family:monospace; font-size:10px">${g.trace_id.substring(0,8)}</span>
                        <span class="diag-badge">${(g.total_duration_ms/1000).toFixed(1)}s</span>
                        <span class="diag-badge">${g.spans.length} spans</span>
                    </div>
                `;
                list.appendChild(el);
            });
        }

        function selectTrace(idx) {
            const g = traceGroups[idx];
            if (!g) return;

            selectedTraceId = g.trace_id;

            document.getElementById('emptyState').classList.add('diag-hidden');
            document.getElementById('detailView').classList.remove('diag-hidden');

            document.getElementById('dTitle').innerText = extractUserIntent(g.root);
            document.getElementById('dId').innerText = g.trace_id;
            document.getElementById('dDur').innerText = `${g.total_duration_ms.toFixed(0)} ms`;
            document.getElementById('dTime').innerText = new Date(g.start_time).toLocaleString();

            renderWaterfall(g);
        }

        function renderWaterfall(g) {
            const container = document.getElementById('waterfall');
            container.innerHTML = '';

            const totalDur = Math.max(g.total_duration_ms, 1);
            const baseTime = new Date(g.start_time).getTime();

            g.spans.forEach(span => {
                const start = new Date(span.start_time).getTime();
                const offset = start - baseTime;

                let bg = 'diag-bg-def';
                const name = (span.name || '').toLowerCase();
                const type = span.attributes?.type;

                if (span.status === 'ERROR' || span.status === 'fail') bg = 'diag-bg-err';
                else if (name.includes('completion') || type === 'ai') bg = 'diag-bg-ai';
                else if (name.includes('tool') || span.attributes?.['tool.name']) bg = 'diag-bg-tool';
                else if (name.includes('postgres') || name.includes('db')) bg = 'diag-bg-db';

                const left = (offset / totalDur) * 100;
                const width = Math.max((span.duration_ms / totalDur) * 100, 0.5);

                const row = document.createElement('div');
                row.className = 'diag-span-row';

                const bar = document.createElement('div');
                bar.className = `diag-span-bar ${bg}`;
                bar.style.left = `${left}%`;
                bar.style.width = `${width}%`;

                let label = span.name;
                const attrs = span.attributes || {};

                if (label.startsWith('executor.step_run')) label = `Step ${attrs.step || '?'}`;
                else if (label.startsWith('skill.tool.') || label.includes('tool.call.')) {
                    const toolName = attrs['tool.name'] || label.split('.').pop();
                    label = `Tool ${toolName}`;
                } else if (label.startsWith('skill.execution.')) {
                    label = `Skill: ${label.replace('skill.execution.', '')}`;
                }

                bar.innerText = label;
                bar.onclick = () => showDetails(span);

                row.appendChild(bar);
                container.appendChild(row);
            });
        }

        function showDetails(span) {
            const drawer = document.getElementById('attrDrawer');
            const content = document.getElementById('drawerContent');
            const attrs = span.attributes || {};

            let attrCards = '';

            if (attrs['error.type'] || attrs['error.message']) {
                attrCards += `
                    <div class="diag-attr-card" style="background:#fef2f2; border-color:#fecaca">
                        <div class="diag-attr-label" style="color:#b91c1c">Exception</div>
                        <div class="diag-attr-value" style="color:#b91c1c; font-weight:600">${escapeHtml(attrs['error.type'] || 'Error')}</div>
                        ${attrs['error.message'] ? `<div class="diag-attr-value" style="margin-top:8px; font-size:12px; color:#991b1b">${escapeHtml(attrs['error.message'])}</div>` : ''}
                    </div>
                `;
            }

            const keyAttrs = [
                { key: 'tool.name', label: 'Tool Name' },
                { key: 'tool.output_preview', label: 'Output Preview' },
                { key: 'tool.status', label: 'Status' },
            ];

            keyAttrs.forEach(({key, label}) => {
                if (attrs[key]) {
                    attrCards += `
                        <div class="diag-attr-card">
                            <div class="diag-attr-label">${label}</div>
                            <div class="diag-attr-value">${escapeHtml(String(attrs[key]))}</div>
                        </div>
                    `;
                }
            });

            content.innerHTML = `
                <div class="diag-drawer-attrs">
                    <div style="font-weight:600; font-size:16px; margin-bottom:12px">${escapeHtml(span.name)}</div>
                    <div style="margin-bottom:16px">
                        <span class="diag-badge ${span.status==='ERROR' ? 'diag-bg-err' : 'diag-bg-ai'}" style="color:white">${span.status}</span>
                        <span class="diag-badge">${span.duration_ms.toFixed(1)} ms</span>
                    </div>
                    ${attrCards || '<div style="color:var(--text-muted)">No key attributes</div>'}
                </div>
                <div class="diag-drawer-json">
                    <div class="diag-attr-label" style="margin-bottom:8px">Raw Span Data</div>
                    <pre id="drawerPre">${JSON.stringify(span, null, 2)}</pre>
                </div>
            `;

            drawer.classList.add('open');
        }

        function closeDrawer() { document.getElementById('attrDrawer').classList.remove('open'); }

        function extractUserIntent(span) {
            if (!span || !span.attributes) return "System Action";
            const body = span.attributes['http.request.body'] || span.attributes['body'];
            if (body) {
                try {
                    const obj = typeof body === 'string' ? JSON.parse(body) : body;
                    if (obj.messages) {
                        const user = obj.messages.find(m => m.role === 'user');
                        if (user) return user.content;
                    }
                    if (obj.prompt) return obj.prompt;
                } catch(e) { if (typeof body === 'string' && body.length > 4) return body; }
            }
            return span.name;
        }

        function escapeHtml(str) {
            if (!str) return '';
            return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }
    """


@router.get("/", response_class=UTF8HTMLResponse)
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
    user_name = admin.display_name or admin.email.split("@")[0]

    return render_admin_page(
        title="Diagnostics",
        active_page="diagnostics",
        content=_get_diagnostics_content(),
        user_name=user_name,
        user_email=admin.email,
        breadcrumbs=[("Diagnostics", "#")],
        extra_css=_get_diagnostics_css(),
        extra_js=_get_diagnostics_js(),
    )


__all__ = ["router"]
