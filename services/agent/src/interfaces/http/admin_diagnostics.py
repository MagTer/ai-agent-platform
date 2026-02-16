# ruff: noqa: E501
"""Admin diagnostics endpoints (secured version of diagnostics router)."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.diagnostics.service import DiagnosticsService, TestResult, TraceGroup
from core.observability.debug_logger import DebugLogger, read_debug_logs
from core.runtime.config import Settings, get_settings
from interfaces.http.admin_auth import AdminUser, require_admin_or_redirect, verify_admin_user
from interfaces.http.admin_shared import UTF8HTMLResponse, render_admin_page
from interfaces.http.csrf import require_csrf

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platformadmin/diagnostics",
    tags=["platform-admin", "diagnostics"],
)

# Load template at module level
_TEMPLATE_PATH = Path(__file__).parent / "templates" / "admin_diagnostics.html"
_TEMPLATE_PARTS = _TEMPLATE_PATH.read_text(encoding="utf-8").split("<!-- CONTENT_SECTION -->")
if len(_TEMPLATE_PARTS) >= 2:
    _TEMPLATE_SECTIONS = _TEMPLATE_PARTS[1].split("<!-- CSS_SECTION -->")
    _CONTENT = _TEMPLATE_SECTIONS[0].strip()
    if len(_TEMPLATE_SECTIONS) >= 2:
        _CSS_JS_PARTS = _TEMPLATE_SECTIONS[1].split("<!-- JS_SECTION -->")
        _CSS = _CSS_JS_PARTS[0].strip()
        _JS = _CSS_JS_PARTS[1].strip() if len(_CSS_JS_PARTS) >= 2 else ""
    else:
        _CSS = ""
        _JS = ""
else:
    _CONTENT = ""
    _CSS = ""
    _JS = ""


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
    return await service.get_recent_traces(limit, show_all=show_all)


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
    return await service.get_system_health_metrics(window=window)


@router.get("/otel-metrics", dependencies=[Depends(verify_admin_user)])
async def get_otel_metrics() -> dict[str, float]:
    """Get OpenTelemetry metric snapshot for dashboard display.

    Returns:
        Raw metric counters from in-memory snapshot.

    Security:
        Requires admin role via Entra ID authentication.
    """
    from core.observability.metrics import get_metric_snapshot

    return get_metric_snapshot()


@router.post(
    "/run",
    response_model=list[TestResult],
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
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
    log_path = Path("data/crash.log")
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
        return {"exists": False, "content": None, "message": "Failed to read crash log"}


@router.post("/retention", dependencies=[Depends(verify_admin_user), Depends(require_csrf)])
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
            "message": "Failed to retrieve MCP health status",
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
            "error": "Failed to read system events",
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
        logger_name: Filter by logger name (e.g., "core.runtime.service").
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
            "error": "Failed to read application logs",
        }


@router.get("/debug-logs", dependencies=[Depends(verify_admin_user)])
async def get_debug_logs(
    trace_id: str | None = Query(None),
    event_type: str | None = Query(None),
    limit: int = Query(100, le=500),
) -> list[dict[str, Any]]:
    """Get debug log entries from JSONL file."""
    return await read_debug_logs(
        trace_id=trace_id,
        event_type=event_type,
        limit=limit,
    )


@router.post(
    "/debug-toggle",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def toggle_debug_logging(
    data: dict[str, bool],
    session: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    """Toggle debug logging on/off."""
    from sqlalchemy import select

    from core.db.models import SystemConfig

    enabled = data.get("enabled", False)

    stmt = select(SystemConfig).where(SystemConfig.key == "debug_enabled")
    result = await session.execute(stmt)
    config = result.scalar_one_or_none()

    if config:
        config.value = "true" if enabled else "false"  # type: ignore[assignment]
    else:
        config = SystemConfig(
            key="debug_enabled",
            value="true" if enabled else "false",
            description="Debug logging toggle",
        )
        session.add(config)

    await session.commit()
    return {"enabled": enabled}


@router.get("/debug-status", dependencies=[Depends(verify_admin_user)])
async def get_debug_status(
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get debug logging status and stats."""
    debug_logger = DebugLogger(session)
    enabled = await debug_logger.is_enabled()

    all_logs = await read_debug_logs(limit=10000)
    log_count = len(all_logs)

    return {"enabled": enabled, "log_count": log_count}


def _get_diagnostics_content() -> str:
    """Return the main content HTML for the diagnostics dashboard."""
    return _CONTENT


def _get_diagnostics_css() -> str:
    """Return CSS specific to the diagnostics dashboard."""
    return _CSS


def _get_diagnostics_js() -> str:
    """Return JavaScript for the diagnostics dashboard."""
    return _JS


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
