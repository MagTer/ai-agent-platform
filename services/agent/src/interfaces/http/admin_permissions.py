# ruff: noqa: E501
"""Admin endpoints for tool permission management."""

from __future__ import annotations

import logging
from uuid import UUID

import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from shared.sanitize import sanitize_log
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.db.models import Context, ToolPermission, User, UserContext
from core.runtime.config import Settings, get_settings
from interfaces.http.admin_auth import AdminUser, require_admin_or_redirect, verify_admin_user
from interfaces.http.admin_shared import UTF8HTMLResponse, render_admin_page
from interfaces.http.csrf import require_csrf

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platformadmin/permissions",
    tags=["admin", "permissions"],
)


def _load_available_tools(settings: Settings) -> list[dict[str, str]]:
    """Load tool names and descriptions from tools.yaml.

    Returns a list of dicts with 'name' and 'description' keys.
    """
    config_path = settings.tools_config_path
    if not config_path.exists():
        LOGGER.warning("tools.yaml not found at %s", config_path)
        return []

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        LOGGER.error("Failed to read tools.yaml: %s", exc)
        return []

    if not isinstance(raw, list):
        return []

    tools: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        description = str(item.get("description", "")).strip()
        tools.append({"name": name, "description": description})

    return sorted(tools, key=lambda t: t["name"])


# --- Pydantic Models ---


class ContextUserInfo(BaseModel):
    """User linked to a context."""

    display_name: str
    email: str
    role: str  # UserContext role: "owner", "member", "viewer"


class ContextPermissionSummary(BaseModel):
    """Summary of a context's permission state."""

    context_id: str
    context_name: str
    context_type: str
    users: list[ContextUserInfo]
    permission_count: int
    allowed_count: int
    denied_count: int
    state: str  # "default" (no permissions) or "customized"


class ToolPermissionDetail(BaseModel):
    """Detail of a single tool permission."""

    tool_name: str
    tool_description: str
    allowed: bool | None  # None = no explicit permission (default allow)
    has_explicit_permission: bool


class ContextPermissionDetail(BaseModel):
    """Full permission detail for a context."""

    context_id: str
    context_name: str
    state: str
    tools: list[ToolPermissionDetail]


class SetPermissionsRequest(BaseModel):
    """Request to set permissions for a context."""

    permissions: dict[str, bool]  # tool_name -> allowed


class BulkActionResponse(BaseModel):
    """Response for bulk permission actions."""

    success: bool
    message: str
    affected_count: int


# --- HTML Dashboard ---


@router.get("/", response_class=UTF8HTMLResponse)
async def permissions_dashboard(admin: AdminUser = Depends(require_admin_or_redirect)) -> str:
    """Tool permissions management dashboard."""
    content = """
        <h1 class="page-title">Tool Permissions</h1>

        <div class="stats-grid" style="margin-bottom: 24px;">
            <div class="stat-box">
                <div class="stat-value" id="totalContexts">0</div>
                <div class="stat-label">Total Contexts</div>
            </div>
            <div class="stat-box">
                <div class="stat-value" id="customizedContexts">0</div>
                <div class="stat-label">Customized</div>
            </div>
            <div class="stat-box">
                <div class="stat-value" id="defaultContexts">0</div>
                <div class="stat-label">Default (All Allowed)</div>
            </div>
            <div class="stat-box">
                <div class="stat-value" id="totalTools">0</div>
                <div class="stat-label">Available Tools</div>
            </div>
        </div>

        <div class="card" id="contextListCard">
            <div class="card-header">
                <span class="card-title">Contexts</span>
                <button class="btn" onclick="loadContexts()">Refresh</button>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>User</th>
                        <th>Context</th>
                        <th>State</th>
                        <th>Permissions</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody id="contextListBody">
                    <tr><td colspan="5" class="loading">Loading...</td></tr>
                </tbody>
            </table>
        </div>

        <!-- Context detail panel (hidden by default) -->
        <div class="card" id="contextDetailCard" style="display: none;">
            <div class="card-header">
                <span class="card-title" id="detailTitle">Context Permissions</span>
                <div>
                    <button class="btn btn-sm" onclick="bulkAction('allow_all')" title="Allow all tools">Allow All</button>
                    <button class="btn btn-sm" onclick="bulkAction('deny_all')" title="Deny all tools">Deny All</button>
                    <button class="btn btn-sm" onclick="bulkAction('reset')" title="Remove all permissions (revert to default allow-all)">Reset to Default</button>
                    <button class="btn" onclick="closeDetail()">Close</button>
                </div>
            </div>
            <p class="detail-info" id="detailInfo"></p>
            <table>
                <thead>
                    <tr>
                        <th>Tool</th>
                        <th>Description</th>
                        <th>Status</th>
                        <th>Toggle</th>
                    </tr>
                </thead>
                <tbody id="toolListBody">
                </tbody>
            </table>
        </div>

    """

    extra_css = """
        .detail-info {
            font-size: 13px;
            color: var(--text-muted);
            margin-bottom: 16px;
            padding: 8px 12px;
            background: var(--bg);
            border-radius: 4px;
        }
        .toggle-switch {
            position: relative;
            display: inline-block;
            width: 44px;
            height: 24px;
        }
        .toggle-switch input {
            opacity: 0;
            width: 0;
            height: 0;
        }
        .toggle-slider {
            position: absolute;
            cursor: pointer;
            top: 0; left: 0; right: 0; bottom: 0;
            background-color: #e2e8f0;
            transition: 0.2s;
            border-radius: 24px;
        }
        .toggle-slider:before {
            position: absolute;
            content: "";
            height: 18px;
            width: 18px;
            left: 3px;
            bottom: 3px;
            background-color: white;
            transition: 0.2s;
            border-radius: 50%;
        }
        input:checked + .toggle-slider {
            background-color: var(--success);
        }
        input:checked + .toggle-slider:before {
            transform: translateX(20px);
        }
        .tool-status-default {
            color: var(--text-muted);
            font-style: italic;
            font-size: 12px;
        }
    """

    extra_js = """
        let currentContextId = null;

        async function loadContexts() {
            const res = await fetchWithErrorHandling('/platformadmin/permissions/contexts');
            if (!res) {
                document.getElementById('contextListBody').innerHTML =
                    '<tr><td colspan="5" style="color: var(--error); text-align: center;">Failed to load contexts</td></tr>';
                return;
            }
            const data = await res.json();
            renderContextList(data);
        }

        function renderContextList(data) {
            document.getElementById('totalContexts').textContent = data.total || 0;
            document.getElementById('customizedContexts').textContent = data.customized || 0;
            document.getElementById('defaultContexts').textContent = data.default_count || 0;
            document.getElementById('totalTools').textContent = data.tool_count || 0;

            const tbody = document.getElementById('contextListBody');
            const contexts = data.contexts || [];

            if (contexts.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5" class="loading">No contexts found</td></tr>';
                return;
            }

            tbody.innerHTML = contexts.map(c => {
                const stateBadge = c.state === 'default'
                    ? '<span class="badge badge-muted">Default (All Allowed)</span>'
                    : '<span class="badge badge-info">Customized</span>';
                const permInfo = c.state === 'default'
                    ? '-'
                    : '<span class="badge badge-success">' + c.allowed_count + ' allowed</span> <span class="badge badge-error">' + c.denied_count + ' denied</span>';
                const usersHtml = (c.users || []).length > 0
                    ? c.users.map(u => '<div style="font-weight: 500;">' + escapeHtml(u.display_name) + '</div><div style="font-size: 11px; color: var(--text-muted);">' + escapeHtml(u.email) + ' <span class="badge badge-muted">' + u.role + '</span></div>').join('')
                    : '<span style="color: var(--text-muted); font-style: italic;">No users</span>';
                return '<tr><td>' + usersHtml + '</td>' +
                    '<td><div style="font-weight: 500;">' + escapeHtml(c.context_name) + '</div><div style="font-size: 11px; color: var(--text-muted);">' + escapeHtml(c.context_type) + '</div></td>' +
                    '<td>' + stateBadge + '</td>' +
                    '<td>' + permInfo + '</td>' +
                    "<td><button class=\\"btn btn-sm btn-primary\\" onclick=\\"openDetail('" + c.context_id + "')\\">Manage</button></td></tr>";
            }).join('');
        }

        async function openDetail(contextId) {
            currentContextId = contextId;
            document.getElementById('contextDetailCard').style.display = 'block';
            document.getElementById('toolListBody').innerHTML =
                '<tr><td colspan="4" class="loading">Loading...</td></tr>';

            const res = await fetchWithErrorHandling('/platformadmin/permissions/contexts/' + contextId);
            if (!res) {
                document.getElementById('toolListBody').innerHTML =
                    '<tr><td colspan="4" style="color: var(--error); text-align: center;">Failed to load permissions: ' + e.message + '</td></tr>';
            }

            // Scroll to detail panel
            document.getElementById('contextDetailCard').scrollIntoView({ behavior: 'smooth' });
        }

        function renderToolList(data) {
            document.getElementById('detailTitle').textContent = 'Permissions: ' + data.context_name;

            const stateText = data.state === 'default'
                ? 'No explicit permissions defined. All tools are allowed by default. Toggle any tool to create explicit permissions.'
                : 'Explicit permissions are configured. Only tools marked as allowed will be available. Use "Reset to Default" to remove all permissions and allow all tools.';
            document.getElementById('detailInfo').textContent = stateText;

            const tbody = document.getElementById('toolListBody');
            const tools = data.tools || [];

            tbody.innerHTML = tools.map(t => {
                // When state is "default", all tools show as allowed but with no explicit permission
                const isAllowed = t.allowed === null ? true : t.allowed;
                const statusHtml = !t.has_explicit_permission
                    ? '<span class="tool-status-default">default (allowed)</span>'
                    : (t.allowed
                        ? '<span class="badge badge-success">Allowed</span>'
                        : '<span class="badge badge-error">Denied</span>');

                return '<tr>' +
                    '<td style="font-weight: 500; font-family: monospace; font-size: 13px;">' + escapeHtml(t.tool_name) + '</td>' +
                    '<td style="font-size: 13px; color: var(--text-muted);">' + escapeHtml(t.tool_description) + '</td>' +
                    '<td>' + statusHtml + '</td>' +
                    "<td><label class=\\"toggle-switch\\"><input type=\\"checkbox\\" " + (isAllowed ? "checked" : "") + " onchange=\\"toggleTool('" + escapeHtml(t.tool_name) + "', this.checked)\\"><span class=\\"toggle-slider\\"></span></label></td>" +
                    '</tr>';
            }).join('');
        }

        async function toggleTool(toolName, allowed) {
            if (!currentContextId) return;

            const res = await fetchWithErrorHandling('/platformadmin/permissions/contexts/' + currentContextId + '/tools/' + encodeURIComponent(toolName), {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ allowed: allowed })
            });

            if (res) {
                const data = await res.json();
                showToast(data.message, 'success');
                loadContexts();
                openDetail(currentContextId);
            } else {
                // Refresh to revert UI state on error
                openDetail(currentContextId);
            }
        }

        async function bulkAction(action) {
            if (!currentContextId) return;

            let confirmMsg = '';
            if (action === 'allow_all') confirmMsg = 'Allow ALL tools for this context?';
            else if (action === 'deny_all') confirmMsg = 'Deny ALL tools for this context? The agent will have no tools available.';
            else if (action === 'reset') confirmMsg = 'Remove all explicit permissions? All tools will be allowed by default.';

            if (!confirm(confirmMsg)) return;

            const res = await fetchWithErrorHandling('/platformadmin/permissions/contexts/' + currentContextId + '/bulk', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: action })
            });

            if (res) {
                const data = await res.json();
                showToast(data.message, 'success');
                loadContexts();
                openDetail(currentContextId);
            }
        }

        function closeDetail() {
            document.getElementById('contextDetailCard').style.display = 'none';
            currentContextId = null;
        }

        // Initial load
        loadContexts();
    """

    return render_admin_page(
        title="Permissions",
        active_page="/platformadmin/permissions/",
        content=content,
        user_name=admin.display_name or admin.email.split("@")[0],
        user_email=admin.email,
        breadcrumbs=[("Permissions", "#")],
        extra_css=extra_css,
        extra_js=extra_js,
    )


# --- API Endpoints ---


@router.get("/contexts")
async def list_context_permissions(
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """List all contexts with their permission summary."""
    # Load available tools for count
    available_tools = _load_available_tools(settings)

    # Get all contexts
    stmt = select(Context).order_by(Context.name)
    result = await session.execute(stmt)
    contexts = result.scalars().all()

    summaries: list[dict[str, object]] = []
    customized_count = 0

    for ctx in contexts:
        # Count permissions for this context
        count_stmt = (
            select(func.count())
            .select_from(ToolPermission)
            .where(ToolPermission.context_id == ctx.id)
        )
        perm_count = await session.scalar(count_stmt) or 0

        allowed_count_stmt = (
            select(func.count())
            .select_from(ToolPermission)
            .where(ToolPermission.context_id == ctx.id, ToolPermission.allowed.is_(True))
        )
        allowed_count = await session.scalar(allowed_count_stmt) or 0

        # Get users linked to this context
        user_stmt = (
            select(User.display_name, User.email, UserContext.role)
            .join(UserContext, User.id == UserContext.user_id)
            .where(UserContext.context_id == ctx.id)
            .order_by(UserContext.role, User.display_name)
        )
        user_result = await session.execute(user_stmt)
        ctx_users = [
            ContextUserInfo(
                display_name=display_name or email.split("@")[0],
                email=email,
                role=role,
            )
            for display_name, email, role in user_result.all()
        ]

        state = "default" if perm_count == 0 else "customized"
        if state == "customized":
            customized_count += 1

        summaries.append(
            ContextPermissionSummary(
                context_id=str(ctx.id),
                context_name=ctx.name,
                context_type=ctx.type,
                users=ctx_users,
                permission_count=perm_count,
                allowed_count=allowed_count,
                denied_count=perm_count - allowed_count,
                state=state,
            ).model_dump()
        )

    return {
        "contexts": summaries,
        "total": len(summaries),
        "customized": customized_count,
        "default_count": len(summaries) - customized_count,
        "tool_count": len(available_tools),
    }


@router.get("/contexts/{context_id}")
async def get_context_permission_detail(
    context_id: UUID,
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Get detailed tool permissions for a specific context."""
    # Verify context exists
    ctx_stmt = select(Context).where(Context.id == context_id)
    ctx_result = await session.execute(ctx_stmt)
    ctx = ctx_result.scalar_one_or_none()

    if not ctx:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Context {context_id} not found",
        )

    # Load available tools
    available_tools = _load_available_tools(settings)

    # Load current permissions
    perm_stmt = select(ToolPermission).where(ToolPermission.context_id == context_id)
    perm_result = await session.execute(perm_stmt)
    permissions = {perm.tool_name: perm.allowed for perm in perm_result.scalars().all()}

    state = "default" if not permissions else "customized"

    # Build tool list with permission status
    tool_details: list[ToolPermissionDetail] = []
    for tool in available_tools:
        tool_name = tool["name"]
        has_explicit = tool_name in permissions
        tool_details.append(
            ToolPermissionDetail(
                tool_name=tool_name,
                tool_description=tool.get("description", ""),
                allowed=permissions.get(tool_name),
                has_explicit_permission=has_explicit,
            )
        )

    return ContextPermissionDetail(
        context_id=str(context_id),
        context_name=ctx.name,
        state=state,
        tools=tool_details,
    ).model_dump()


class TogglePermissionRequest(BaseModel):
    """Request to toggle a single tool permission."""

    allowed: bool


@router.put("/contexts/{context_id}/tools/{tool_name}", dependencies=[Depends(require_csrf)])
async def set_tool_permission(
    context_id: UUID,
    tool_name: str,
    request: TogglePermissionRequest,
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Set permission for a single tool in a context.

    Creates or updates the ToolPermission record. When toggling a single tool,
    if the context had no permissions before (default state), this will also
    create explicit 'allowed=True' records for all OTHER tools -- because
    the permission semantics require that once ANY permission exists, tools
    without explicit allow are denied.
    """
    # Verify context exists
    ctx_stmt = select(Context).where(Context.id == context_id)
    ctx_result = await session.execute(ctx_stmt)
    ctx = ctx_result.scalar_one_or_none()

    if not ctx:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Context {context_id} not found",
        )

    # Validate tool name exists in tools.yaml
    available_tools = _load_available_tools(settings)
    tool_names = {t["name"] for t in available_tools}
    if tool_name not in tool_names:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown tool: {tool_name}. Available tools: {sorted(tool_names)}",
        )

    # Check if context currently has ANY permissions
    existing_count_stmt = (
        select(func.count())
        .select_from(ToolPermission)
        .where(ToolPermission.context_id == context_id)
    )
    existing_count = await session.scalar(existing_count_stmt) or 0

    if existing_count == 0:
        # Transitioning from default -> customized state
        # Create explicit "allowed=True" for all tools, then override the target tool
        LOGGER.info(
            "Context %s transitioning from default to customized permissions",
            sanitize_log(context_id),
        )
        for t_name in tool_names:
            allowed = request.allowed if t_name == tool_name else True
            perm = ToolPermission(
                context_id=context_id,
                tool_name=t_name,
                allowed=allowed,
            )
            session.add(perm)
    else:
        # Already has permissions -- upsert the specific tool
        existing_stmt = select(ToolPermission).where(
            ToolPermission.context_id == context_id,
            ToolPermission.tool_name == tool_name,
        )
        existing_result = await session.execute(existing_stmt)
        existing = existing_result.scalar_one_or_none()

        if existing:
            existing.allowed = request.allowed
        else:
            perm = ToolPermission(
                context_id=context_id,
                tool_name=tool_name,
                allowed=request.allowed,
            )
            session.add(perm)

    await session.commit()

    action = "allowed" if request.allowed else "denied"
    LOGGER.info(
        "Admin %s %s tool '%s' for context %s (%s)",
        sanitize_log(admin.email),
        action,
        sanitize_log(tool_name),
        sanitize_log(context_id),
        sanitize_log(ctx.name),
    )

    return {
        "success": True,
        "message": f"Tool '{tool_name}' {action} for context '{ctx.name}'",
    }


class BulkActionRequest(BaseModel):
    """Request for bulk permission action."""

    action: str  # "allow_all", "deny_all", "reset"


@router.post("/contexts/{context_id}/bulk", dependencies=[Depends(require_csrf)])
async def bulk_permission_action(
    context_id: UUID,
    request: BulkActionRequest,
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Execute a bulk permission action for a context.

    Actions:
        - allow_all: Set all tools to allowed=True
        - deny_all: Set all tools to allowed=False
        - reset: Delete all permissions (revert to default allow-all)
    """
    # Verify context exists
    ctx_stmt = select(Context).where(Context.id == context_id)
    ctx_result = await session.execute(ctx_stmt)
    ctx = ctx_result.scalar_one_or_none()

    if not ctx:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Context {context_id} not found",
        )

    if request.action not in ("allow_all", "deny_all", "reset"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid action: {request.action}. Valid: allow_all, deny_all, reset",
        )

    if request.action == "reset":
        # Delete all permissions for this context
        del_stmt = delete(ToolPermission).where(ToolPermission.context_id == context_id)
        await session.execute(del_stmt)
        await session.commit()

        LOGGER.info(
            "Admin %s reset permissions for context %s (%s)",
            sanitize_log(admin.email),
            sanitize_log(context_id),
            sanitize_log(ctx.name),
        )

        return BulkActionResponse(
            success=True,
            message=f"Reset permissions for '{ctx.name}'. All tools now allowed by default.",
            affected_count=0,
        ).model_dump()

    # allow_all or deny_all: set all tools to the specified state
    allowed = request.action == "allow_all"
    available_tools = _load_available_tools(settings)
    tool_names = {t["name"] for t in available_tools}

    # Delete existing permissions first
    del_stmt = delete(ToolPermission).where(ToolPermission.context_id == context_id)
    await session.execute(del_stmt)

    # Create new permissions for all tools
    for t_name in tool_names:
        perm = ToolPermission(
            context_id=context_id,
            tool_name=t_name,
            allowed=allowed,
        )
        session.add(perm)

    await session.commit()

    action_desc = "allowed" if allowed else "denied"
    LOGGER.info(
        "Admin %s set all tools to %s for context %s (%s)",
        sanitize_log(admin.email),
        action_desc,
        sanitize_log(context_id),
        sanitize_log(ctx.name),
    )

    return BulkActionResponse(
        success=True,
        message=f"All {len(tool_names)} tools {action_desc} for '{ctx.name}'.",
        affected_count=len(tool_names),
    ).model_dump()


@router.get("/tools")
async def list_available_tools(
    admin: AdminUser = Depends(verify_admin_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """List all available tools from tools.yaml."""
    tools = _load_available_tools(settings)
    return {
        "tools": tools,
        "total": len(tools),
    }


__all__ = ["router"]
