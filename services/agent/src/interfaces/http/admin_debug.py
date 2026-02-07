# ruff: noqa: E501
"""Admin portal for debug logging settings and log viewing."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.db.models import DebugLog
from core.debug import DebugLogger
from interfaces.http.admin_auth import AdminUser, get_admin_user_or_redirect, verify_admin_user
from interfaces.http.admin_shared import UTF8HTMLResponse, render_admin_page
from interfaces.http.csrf import require_csrf

router = APIRouter(prefix="/platformadmin/debug", tags=["admin-debug"])


class DebugSettingsResponse(BaseModel):
    """Response model for debug settings."""

    enabled: bool
    log_count: int
    oldest_log: str | None
    newest_log: str | None


class DebugLogEntry(BaseModel):
    """Response model for a debug log entry."""

    id: str
    trace_id: str
    conversation_id: str | None
    event_type: str
    event_data: dict[str, Any]
    created_at: str


def _event_type_color(event_type: str) -> str:
    """Get badge color for event type."""
    colors = {
        "request": "blue",
        "history": "purple",
        "plan": "green",
        "tool_call": "orange",
        "supervisor": "gray",
        "completion_prompt": "blue",
        "completion_response": "green",
    }
    return colors.get(event_type, "gray")


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


@router.get("/", response_class=UTF8HTMLResponse)
async def debug_dashboard(
    admin_user: AdminUser = Depends(get_admin_user_or_redirect),
    session: AsyncSession = Depends(get_db),
) -> UTF8HTMLResponse:
    """Debug settings dashboard."""
    debug_logger = DebugLogger(session)
    enabled = await debug_logger.is_enabled()

    # Get log stats
    count_stmt = select(func.count()).select_from(DebugLog)
    count_result = await session.execute(count_stmt)
    log_count = count_result.scalar() or 0

    # Get recent logs
    logs_stmt = select(DebugLog).order_by(DebugLog.created_at.desc()).limit(50)
    logs_result = await session.execute(logs_stmt)
    logs = list(logs_result.scalars().all())

    # Build log table rows
    log_rows = ""
    for log in logs:
        event_data_preview = (
            str(log.event_data)[:100] + "..."
            if len(str(log.event_data)) > 100
            else str(log.event_data)
        )
        log_rows += f"""
        <tr>
            <td><code>{log.trace_id[:12]}...</code></td>
            <td><span class="badge badge-{_event_type_color(log.event_type)}">{log.event_type}</span></td>
            <td>{log.conversation_id[:12] + '...' if log.conversation_id else '-'}</td>
            <td title="{_escape_html(str(log.event_data))}">{_escape_html(event_data_preview)}</td>
            <td>{log.created_at.strftime('%H:%M:%S')}</td>
            <td>
                <button class="btn btn-sm" onclick="showLogDetail('{log.id}')">View</button>
            </td>
        </tr>
        """

    extra_css = """
        .header-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }
        .toggle-container {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .toggle {
            position: relative;
            display: inline-block;
            width: 60px;
            height: 34px;
        }
        .toggle input {
            opacity: 0;
            width: 0;
            height: 0;
        }
        .toggle-slider {
            position: absolute;
            cursor: pointer;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background-color: #ccc;
            transition: .4s;
            border-radius: 34px;
        }
        .toggle-slider:before {
            position: absolute;
            content: "";
            height: 26px;
            width: 26px;
            left: 4px;
            bottom: 4px;
            background-color: white;
            transition: .4s;
            border-radius: 50%;
        }
        .toggle input:checked + .toggle-slider {
            background-color: var(--success);
        }
        .toggle input:checked + .toggle-slider:before {
            transform: translateX(26px);
        }
        .toggle-label {
            font-weight: 600;
        }
        .stats-row {
            display: flex;
            gap: 16px;
            margin-bottom: 20px;
            align-items: center;
        }
        .actions {
            margin-left: auto;
            display: flex;
            gap: 10px;
        }
        .info-box {
            background: #dbeafe;
            border-left: 4px solid var(--primary);
            padding: 16px;
            margin-bottom: 20px;
            border-radius: 0 8px 8px 0;
        }
        .info-box ul {
            margin: 10px 0;
            padding-left: 20px;
        }
        .info-box li {
            margin: 5px 0;
        }
        .badge-blue { background: #dbeafe; color: #1e40af; }
        .badge-green { background: #d1fae5; color: #065f46; }
        .badge-orange { background: #ffedd5; color: #9a3412; }
        .badge-purple { background: #f3e8ff; color: #6b21a8; }
        .badge-gray { background: #f3f4f6; color: #374151; }
        .modal {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
            display: flex;
            justify-content: center;
            align-items: center;
            z-index: 1000;
        }
        .modal-content {
            background: var(--bg-card);
            border-radius: 12px;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1);
            max-width: 900px;
            width: 90%;
            max-height: 80vh;
            display: flex;
            flex-direction: column;
        }
        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 16px 20px;
            border-bottom: 1px solid var(--border);
        }
        .modal-header h3 {
            margin: 0;
            font-size: 16px;
        }
        .modal-close {
            background: none;
            border: none;
            font-size: 24px;
            cursor: pointer;
            color: var(--text-muted);
            padding: 0;
            line-height: 1;
        }
        .modal-close:hover {
            color: var(--text);
        }
        .modal-body {
            padding: 20px;
            overflow: auto;
        }
        #log-detail-content {
            background: #1e293b;
            color: #e2e8f0;
            padding: 16px;
            border-radius: 8px;
            overflow: auto;
            max-height: 500px;
            white-space: pre-wrap;
            word-wrap: break-word;
            font-family: ui-monospace, monospace;
            font-size: 13px;
        }
    """

    extra_js = """
        async function toggleDebug(enabled) {
            try {
                const resp = await fetch('/platformadmin/debug/toggle', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({enabled})
                });
                if (resp.ok) {
                    document.querySelector('.toggle-label').textContent = enabled ? 'Enabled' : 'Disabled';
                } else {
                    alert('Failed to toggle debug mode');
                    document.getElementById('debug-toggle').checked = !enabled;
                }
            } catch (e) {
                alert('Error: ' + e.message);
                document.getElementById('debug-toggle').checked = !enabled;
            }
        }

        async function cleanupLogs() {
            if (!confirm('Delete logs older than 24 hours?')) return;
            try {
                const resp = await fetch('/platformadmin/debug/cleanup', {method: 'POST'});
                const data = await resp.json();
                alert('Deleted ' + data.deleted + ' logs');
                location.reload();
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }

        function refreshLogs() {
            location.reload();
        }

        async function showLogDetail(logId) {
            try {
                const resp = await fetch('/platformadmin/debug/log/' + logId);
                const data = await resp.json();
                document.getElementById('log-detail-content').textContent = JSON.stringify(data, null, 2);
                document.getElementById('log-modal').style.display = 'flex';
            } catch (e) {
                alert('Error loading log: ' + e.message);
            }
        }

        function closeModal() {
            document.getElementById('log-modal').style.display = 'none';
        }

        document.getElementById('log-modal').addEventListener('click', function(e) {
            if (e.target === this) closeModal();
        });
    """

    content = f"""
    <h1 class="page-title">Debug Logging</h1>

    <div class="card">
        <div class="header-row">
            <div class="card-title">Debug Mode</div>
            <div class="toggle-container">
                <label class="toggle">
                    <input type="checkbox" id="debug-toggle" {"checked" if enabled else ""}
                           onchange="toggleDebug(this.checked)">
                    <span class="toggle-slider"></span>
                </label>
                <span class="toggle-label">{"Enabled" if enabled else "Disabled"}</span>
            </div>
        </div>
    </div>

    <div class="stats-grid">
        <div class="stat-box">
            <div class="stat-value">{log_count}</div>
            <div class="stat-label">Total Logs</div>
        </div>
        <div class="stat-box">
            <div class="stat-value">{len(logs)}</div>
            <div class="stat-label">Recent (50)</div>
        </div>
    </div>

    <div class="stats-row">
        <div class="actions">
            <button class="btn" onclick="cleanupLogs()">Cleanup Old Logs</button>
            <button class="btn btn-primary" onclick="refreshLogs()">Refresh</button>
        </div>
    </div>

    <div class="info-box">
        <strong>Debug Logging captures:</strong>
        <ul>
            <li><strong>request</strong> - Incoming prompt, messages, metadata</li>
            <li><strong>history</strong> - Message history source and contents</li>
            <li><strong>plan</strong> - Generated execution plan</li>
            <li><strong>tool_call</strong> - Tool executions with args and results</li>
            <li><strong>supervisor</strong> - Supervisor decisions (SUCCESS/RETRY/REPLAN/ABORT)</li>
            <li><strong>completion_prompt</strong> - Full prompt sent to completion LLM</li>
            <li><strong>completion_response</strong> - LLM response</li>
        </ul>
        <p style="margin-top: 10px; margin-bottom: 0;">Logs are also added to OpenTelemetry traces (prefix: <code>debug.*</code>)</p>
    </div>

    <div class="card">
        <div class="card-header">
            <div class="card-title">Recent Logs</div>
        </div>
        <table>
            <thead>
                <tr>
                    <th>Trace ID</th>
                    <th>Event</th>
                    <th>Conversation</th>
                    <th>Data Preview</th>
                    <th>Time</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody id="logs-table">
                {log_rows if log_rows else '<tr><td colspan="6" class="empty-state">No logs yet. Enable debug logging and make a request.</td></tr>'}
            </tbody>
        </table>
    </div>

    <!-- Log Detail Modal -->
    <div id="log-modal" class="modal" style="display:none;">
        <div class="modal-content">
            <div class="modal-header">
                <h3>Log Details</h3>
                <button class="modal-close" onclick="closeModal()">&times;</button>
            </div>
            <div class="modal-body">
                <pre id="log-detail-content"></pre>
            </div>
        </div>
    </div>
    """

    return UTF8HTMLResponse(
        render_admin_page(
            title="Debug Logging",
            active_page="debug",
            content=content,
            user_name=admin_user.display_name or admin_user.email,
            user_email=admin_user.email,
            breadcrumbs=[("Debug Logs", "#")],
            extra_css=extra_css,
            extra_js=extra_js,
        )
    )


@router.post("/toggle", dependencies=[Depends(verify_admin_user), Depends(require_csrf)])
async def toggle_debug(
    data: dict[str, bool],
    session: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    """Toggle debug logging on/off."""
    enabled = data.get("enabled", False)
    debug_logger = DebugLogger(session)
    await debug_logger.set_enabled(enabled)
    return {"enabled": enabled}


@router.post("/cleanup", dependencies=[Depends(verify_admin_user), Depends(require_csrf)])
async def cleanup_logs(
    session: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Cleanup old debug logs."""
    debug_logger = DebugLogger(session)
    deleted = await debug_logger.cleanup_old_logs(retention_hours=24)
    return {"deleted": deleted}


@router.get("/log/{log_id}", dependencies=[Depends(verify_admin_user)])
async def get_log_detail(
    log_id: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get full details of a specific log entry."""
    from uuid import UUID

    stmt = select(DebugLog).where(DebugLog.id == UUID(log_id))
    result = await session.execute(stmt)
    log = result.scalar_one_or_none()

    if not log:
        return {"error": "Log not found"}

    return {
        "id": str(log.id),
        "trace_id": log.trace_id,
        "conversation_id": log.conversation_id,
        "event_type": log.event_type,
        "event_data": log.event_data,
        "created_at": log.created_at.isoformat(),
    }


@router.get("/logs", dependencies=[Depends(verify_admin_user)])
async def list_logs(
    trace_id: str | None = Query(None),
    event_type: str | None = Query(None),
    limit: int = Query(100, le=500),
    session: AsyncSession = Depends(get_db),
) -> list[DebugLogEntry]:
    """List debug logs with optional filters."""
    debug_logger = DebugLogger(session)
    logs = await debug_logger.get_logs(
        trace_id=trace_id,
        event_type=event_type,
        limit=limit,
    )

    return [
        DebugLogEntry(
            id=str(log.id),
            trace_id=log.trace_id,
            conversation_id=log.conversation_id,
            event_type=log.event_type,
            event_data=log.event_data,
            created_at=log.created_at.isoformat(),
        )
        for log in logs
    ]
