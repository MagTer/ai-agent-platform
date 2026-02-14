# ruff: noqa: E501
"""Admin portal for debug logging settings and log viewing."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.observability.debug_logger import DebugLogger, read_debug_logs
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
    trace_id: str | None = Query(None, description="Filter by trace ID"),
    admin_user: AdminUser = Depends(get_admin_user_or_redirect),
    session: AsyncSession = Depends(get_db),
) -> UTF8HTMLResponse:
    """Debug settings dashboard."""
    debug_logger = DebugLogger(session)
    enabled = await debug_logger.is_enabled()

    # Read logs from JSONL file
    limit = 500 if trace_id else 50  # Show more when filtering
    logs = await read_debug_logs(trace_id=trace_id, limit=limit)

    # Get total log count (approximate from file)
    all_logs = await read_debug_logs(limit=10000)
    log_count = len(all_logs)

    # Build log table rows
    log_rows = ""
    for idx, log in enumerate(logs):
        event_data = log.get("event_data", {})
        event_data_str = str(event_data)
        event_data_preview = (
            event_data_str[:100] + "..." if len(event_data_str) > 100 else event_data_str
        )

        # Make trace_id clickable link to diagnostics
        trace_id_val = log.get("trace_id", "")
        trace_id_link = (
            f'<a href="/platformadmin/diagnostics/?trace={trace_id_val}">{trace_id_val[:12]}...</a>'
        )

        # Use index as "id" for the showLogDetail function
        log_rows += f"""
        <tr>
            <td><code>{trace_id_link}</code></td>
            <td><span class="badge badge-{_event_type_color(log.get('event_type', ''))}">{log.get('event_type', '')}</span></td>
            <td>{log.get('conversation_id', '')[:12] + '...' if log.get('conversation_id') else '-'}</td>
            <td title="{_escape_html(event_data_str)}">{_escape_html(event_data_preview)}</td>
            <td>{log.get('timestamp', '')[-15:-7]}</td>
            <td>
                <button class="btn btn-sm" onclick="showLogDetail({idx})">View</button>
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

    # Store logs in JavaScript for modal viewing
    import json

    logs_json = json.dumps(logs, default=str)

    extra_js = f"""
        const ALL_LOGS = {logs_json};

        async function toggleDebug(enabled) {{
            try {{
                const resp = await fetch('/platformadmin/debug/toggle', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{enabled}})
                }});
                if (resp.ok) {{
                    document.querySelector('.toggle-label').textContent = enabled ? 'Enabled' : 'Disabled';
                }} else {{
                    alert('Failed to toggle debug mode');
                    document.getElementById('debug-toggle').checked = !enabled;
                }}
            }} catch (e) {{
                alert('Error: ' + e.message);
                document.getElementById('debug-toggle').checked = !enabled;
            }}
        }}

        function refreshLogs() {{
            location.reload();
        }}

        function showLogDetail(logIdx) {{
            const log = ALL_LOGS[logIdx];
            if (!log) {{
                alert('Log not found');
                return;
            }}
            document.getElementById('log-detail-content').textContent = JSON.stringify(log, null, 2);
            document.getElementById('log-modal').style.display = 'flex';
        }}

        function closeModal() {{
            document.getElementById('log-modal').style.display = 'none';
        }}

        document.getElementById('log-modal').addEventListener('click', function(e) {{
            if (e.target === this) closeModal();
        }});
    """

    # Show filter banner if trace_id is set
    filter_banner = ""
    if trace_id:
        filter_banner = f"""
        <div class="info-box" style="background: #fef3c7; border-left-color: #f59e0b;">
            <strong>Filtered by trace:</strong> <code>{trace_id}</code>
            <a href="/platformadmin/debug/" style="margin-left: 12px; color: #0369a1; text-decoration: none; font-weight: 600;">Show all</a>
        </div>
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
            <div class="stat-label">Total Logs (approx)</div>
        </div>
        <div class="stat-box">
            <div class="stat-value">{len(logs)}</div>
            <div class="stat-label">Showing</div>
        </div>
    </div>

    <div class="stats-row">
        <div class="actions">
            <button class="btn btn-primary" onclick="refreshLogs()">Refresh</button>
        </div>
    </div>

    {filter_banner}

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
        <p style="margin-top: 10px; margin-bottom: 0;">
            Logs stored in <code>data/debug_logs.jsonl</code> with automatic rotation (10MB max, 3 backups).
            Also added to OpenTelemetry traces (prefix: <code>debug.*</code>).
        </p>
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
    from sqlalchemy import select

    from core.db.models import SystemConfig

    enabled = data.get("enabled", False)

    # Update or create SystemConfig entry
    stmt = select(SystemConfig).where(SystemConfig.key == "debug_enabled")
    result = await session.execute(stmt)
    config = result.scalar_one_or_none()

    if config:
        config.value = "true" if enabled else "false"  # type: ignore[assignment]  # JSONB accepts any JSON value
    else:
        config = SystemConfig(
            key="debug_enabled",
            value="true" if enabled else "false",
            description="Debug logging toggle",
        )
        session.add(config)

    await session.commit()

    # Clear the cache to force reload

    globals()["_debug_enabled_cache"] = None

    return {"enabled": enabled}


@router.get("/logs", dependencies=[Depends(verify_admin_user)])
async def list_logs(
    trace_id: str | None = Query(None),
    event_type: str | None = Query(None),
    limit: int = Query(100, le=500),
) -> list[dict[str, Any]]:
    """List debug logs with optional filters from JSONL file."""
    logs = await read_debug_logs(
        trace_id=trace_id,
        event_type=event_type,
        limit=limit,
    )
    return logs
