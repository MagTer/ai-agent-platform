"""Admin endpoints for context management."""

# ruff: noqa: E501
from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.db.models import (
    Context,
    Conversation,
    McpServer,
    ToolPermission,
    User,
    UserContext,
    UserCredential,
    Workspace,
)
from core.db.oauth_models import OAuthToken
from interfaces.http.admin_auth import AdminUser, require_admin_or_redirect, verify_admin_user
from interfaces.http.admin_shared import UTF8HTMLResponse, render_admin_page
from interfaces.http.csrf import require_csrf

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platformadmin/contexts",
    tags=["platform-admin", "contexts"],
)


@router.get("/", response_class=UTF8HTMLResponse)
async def contexts_dashboard(admin: AdminUser = Depends(require_admin_or_redirect)) -> str:
    """Context management dashboard.

    Security:
        Requires admin role via Entra ID authentication.
    """
    content = """
    <h1 class="page-title">Contexts</h1>

    <div class="stats-grid">
        <div class="stat-box">
            <div class="stat-value" id="totalContexts">0</div>
            <div class="stat-label">Total Contexts</div>
        </div>
        <div class="stat-box">
            <div class="stat-value" id="personalContexts">0</div>
            <div class="stat-label">Personal</div>
        </div>
        <div class="stat-box">
            <div class="stat-value" id="sharedContexts">0</div>
            <div class="stat-label">Shared</div>
        </div>
    </div>

    <!-- Active Context Switcher -->
    <div class="card" style="margin-bottom: 16px;">
        <div class="card-header">
            <span class="card-title">Active Chat Context</span>
        </div>
        <div style="padding: 0 0 4px 0;">
            <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 12px;">
                Select which context to use when chatting via OpenWebUI.
            </p>
            <select id="activeContextSelect" onchange="switchActiveContext(this.value)" style="padding: 8px; border: 1px solid var(--border); border-radius: 4px; font-size: 14px; min-width: 300px;">
                <option value="">Loading...</option>
            </select>
            <span id="switchStatus" style="margin-left: 12px; font-size: 13px;"></span>
        </div>
    </div>

    <div class="card">
        <div class="card-header">
            <span>All Contexts <span id="count" class="badge badge-info">0</span></span>
            <div style="display: flex; gap: 8px;">
                <button class="btn btn-primary" onclick="showCreateModal()">+ Create Context</button>
                <button class="btn" onclick="loadContexts()">Refresh</button>
            </div>
        </div>
        <div id="contexts">
            <div class="loading">Loading...</div>
        </div>
    </div>

    <!-- Create Context Modal -->
    <div id="createModal" class="modal" style="display: none;">
        <div class="modal-content">
            <h3>Create Context</h3>
            <form id="createForm" onsubmit="createContext(event)">
                <div class="form-group">
                    <label>Name</label>
                    <input type="text" id="ctxName" required placeholder="e.g., project-backend">
                </div>
                <div class="form-group">
                    <label>Type</label>
                    <select id="ctxType">
                        <option value="shared">Shared</option>
                        <option value="personal">Personal</option>
                    </select>
                </div>
                <div class="modal-actions">
                    <button type="button" class="btn" onclick="hideCreateModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Create</button>
                </div>
            </form>
        </div>
    </div>
"""

    extra_css = """
    .context-card { display: block; padding: 16px; border: 1px solid var(--border); border-radius: 6px; margin-bottom: 8px; text-decoration: none; color: inherit; transition: all 0.15s; }
    .context-card:hover { border-color: var(--primary); background: #f8faff; }
    .context-header { display: flex; justify-content: space-between; align-items: flex-start; }
    .context-name { font-weight: 600; font-size: 15px; margin-bottom: 4px; color: var(--text); }
    .context-id { font-family: monospace; font-size: 11px; color: var(--text-muted); }
    .context-meta { font-size: 12px; color: var(--text-muted); display: flex; gap: 16px; margin-top: 8px; flex-wrap: wrap; }
    .modal { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 1000; }
    .modal-content { background: var(--bg-card); padding: 24px; border-radius: 8px; width: 100%; max-width: 480px; }
    .modal-content h3 { margin: 0 0 16px 0; }
    .form-group { margin-bottom: 16px; }
    .form-group label { display: block; margin-bottom: 4px; font-size: 13px; font-weight: 500; }
    .form-group input, .form-group select { width: 100%; padding: 8px; border: 1px solid var(--border); border-radius: 4px; font-size: 14px; background: var(--bg); color: var(--text); }
    .modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
    .badge-type-personal { background: #dbeafe; color: #1e40af; }
    .badge-type-shared { background: #e5e7eb; color: #374151; }
"""

    extra_js = """
    async function loadContexts() {
        const res = await fetchWithErrorHandling('/platformadmin/contexts');
        if (!res) {
            document.getElementById('contexts').innerHTML = '<div style="color: var(--error)">Failed to load</div>';
            return;
        }
        const data = await res.json();
        renderContexts(data);
    }

    function renderContexts(data) {
        const contexts = data.contexts || [];
        document.getElementById('count').textContent = data.total || 0;
        document.getElementById('totalContexts').textContent = data.total || 0;
        document.getElementById('personalContexts').textContent = contexts.filter(c => c.type === 'personal').length;
        document.getElementById('sharedContexts').textContent = contexts.filter(c => c.type === 'shared').length;

        const el = document.getElementById('contexts');
        if (contexts.length === 0) {
            el.innerHTML = '<div class="empty-state">No contexts found</div>';
            return;
        }
        el.innerHTML = contexts.map(c => {
            const typeBadge = '<span class="badge badge-type-' + c.type + '">' + c.type + '</span>';
            const ownerLine = c.owner_email
                ? '<span style="font-weight:500;">Owner: ' + escapeHtml(c.owner_email) + '</span>'
                : '<span style="color:var(--text-muted);font-style:italic;">No owner</span>';
            return '<a href="/platformadmin/contexts/' + c.id + '/" class="context-card">' +
                '<div class="context-header"><div>' +
                '<div class="context-name">' + escapeHtml(c.name) + ' ' + typeBadge + '</div>' +
                '<div class="context-id">' + c.id + '</div>' +
                '</div></div>' +
                '<div class="context-meta">' +
                ownerLine +
                '<span>Conversations: ' + c.conversation_count + '</span>' +
                '<span>OAuth: ' + c.oauth_token_count + '</span>' +
                '<span>Permissions: ' + c.tool_permission_count + '</span>' +
                '<span>Workspaces: ' + c.workspace_count + '</span>' +
                '<span>MCP: ' + c.mcp_server_count + '</span>' +
                '<span>Credentials: ' + c.credential_count + '</span>' +
                '</div></a>';
        }).join('');
    }

    async function loadMyContexts() {
        const res = await fetchWithErrorHandling('/platformadmin/users/me/contexts');
        if (!res) return;
        const data = await res.json();
        const select = document.getElementById('activeContextSelect');
        const contexts = data.contexts || [];
        if (contexts.length === 0) {
            select.innerHTML = '<option value="">No contexts available</option>';
            return;
        }
        select.innerHTML = contexts.map(c => {
            const label = c.name + (c.is_default ? ' (personal)' : '') + ' [' + c.role + ']';
            return '<option value="' + c.id + '"' + (c.is_active ? ' selected' : '') + '>' + escapeHtml(label) + '</option>';
        }).join('');
    }

    async function switchActiveContext(contextId) {
        const statusEl = document.getElementById('switchStatus');
        statusEl.textContent = 'Switching...';
        statusEl.style.color = 'var(--text-muted)';
        const res = await fetchWithErrorHandling('/platformadmin/users/me/active-context', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ context_id: contextId || null })
        });
        if (res) {
            const data = await res.json();
            statusEl.textContent = data.message || 'Done';
            statusEl.style.color = 'var(--success)';
            setTimeout(() => { statusEl.textContent = ''; }, 3000);
        } else {
            statusEl.textContent = 'Failed';
            statusEl.style.color = 'var(--error)';
        }
    }

    function showCreateModal() { document.getElementById('createModal').style.display = 'flex'; }
    function hideCreateModal() { document.getElementById('createModal').style.display = 'none'; document.getElementById('createForm').reset(); }

    async function createContext(e) {
        e.preventDefault();
        const name = document.getElementById('ctxName').value;
        const type = document.getElementById('ctxType').value;
        const res = await fetchWithErrorHandling('/platformadmin/contexts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name, type: type })
        });
        if (res) {
            showToast('Context created', 'success');
            hideCreateModal();
            loadContexts();
            loadMyContexts();
        }
    }

    function escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    loadContexts();
    loadMyContexts();
"""

    return render_admin_page(
        title="Contexts",
        active_page="/platformadmin/contexts/",
        content=content,
        user_name=admin.display_name or admin.email.split("@")[0],
        user_email=admin.email,
        breadcrumbs=[("Contexts", "#")],
        extra_css=extra_css,
        extra_js=extra_js,
    )


class ContextInfo(BaseModel):
    """Context information for admin display."""

    id: UUID
    name: str
    type: str
    config: dict[str, Any]
    pinned_files: list[str]
    default_cwd: str
    owner_email: str | None
    conversation_count: int
    oauth_token_count: int
    tool_permission_count: int
    workspace_count: int
    mcp_server_count: int
    credential_count: int


class ContextList(BaseModel):
    """List of contexts."""

    contexts: list[ContextInfo]
    total: int


class ContextDetailResponse(BaseModel):
    """Detailed context information."""

    id: UUID
    name: str
    type: str
    config: dict[str, Any]
    pinned_files: list[str]
    default_cwd: str
    conversations: list[dict[str, Any]]
    oauth_tokens: list[dict[str, Any]]
    tool_permissions: list[dict[str, Any]]
    credentials: list[dict[str, Any]]


class CreateContextRequest(BaseModel):
    """Request to create a new context."""

    name: str
    type: str = "shared"
    config: dict[str, Any] = {}
    pinned_files: list[str] = []
    default_cwd: str = "/tmp"  # noqa: S108


class CreateContextResponse(BaseModel):
    """Response after creating a context."""

    success: bool
    message: str
    context_id: UUID


class DeleteContextResponse(BaseModel):
    """Response after deleting a context."""

    success: bool
    message: str
    deleted_context_id: UUID


@router.get("", response_model=ContextList, dependencies=[Depends(verify_admin_user)])
async def list_contexts(
    type_filter: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> ContextList:
    """List all contexts with summary statistics.

    Args:
        type_filter: Optional context type filter
        session: Database session

    Returns:
        List of contexts with counts of related entities

    Security:
        Requires admin role via Entra ID authentication.
    """
    # Subquery: owner email per context (role='owner')
    owner_subq = (
        select(UserContext.context_id, func.min(User.email).label("owner_email"))
        .join(User, User.id == UserContext.user_id)
        .where(UserContext.role == "owner")
        .group_by(UserContext.context_id)
        .subquery()
    )

    # Single query with LEFT JOINs to avoid N+1 pattern
    # For N contexts, this runs 1 query instead of 1 + 3N queries
    stmt = (
        select(
            Context,
            func.count(distinct(Conversation.id)).label("conv_count"),
            func.count(distinct(OAuthToken.id)).label("oauth_count"),
            func.count(distinct(ToolPermission.id)).label("perm_count"),
            func.count(distinct(Workspace.id)).label("ws_count"),
            func.count(distinct(McpServer.id)).label("mcp_count"),
            func.count(distinct(UserCredential.id)).label("cred_count"),
            owner_subq.c.owner_email,
        )
        .outerjoin(Conversation, Conversation.context_id == Context.id)
        .outerjoin(OAuthToken, OAuthToken.context_id == Context.id)
        .outerjoin(ToolPermission, ToolPermission.context_id == Context.id)
        .outerjoin(Workspace, Workspace.context_id == Context.id)
        .outerjoin(McpServer, McpServer.context_id == Context.id)
        .outerjoin(UserCredential, UserCredential.context_id == Context.id)
        .outerjoin(owner_subq, owner_subq.c.context_id == Context.id)
        .group_by(Context.id, owner_subq.c.owner_email)
        .order_by(Context.name)
    )

    if type_filter:
        stmt = stmt.where(Context.type == type_filter)

    result = await session.execute(stmt)
    rows = result.all()

    context_infos = []
    for (
        ctx,
        conv_count,
        oauth_count,
        perm_count,
        ws_count,
        mcp_count,
        cred_count,
        owner_email,
    ) in rows:
        context_infos.append(
            ContextInfo(
                id=ctx.id,
                name=ctx.name,
                type=ctx.type,
                config=ctx.config,
                pinned_files=ctx.pinned_files,
                default_cwd=ctx.default_cwd,
                owner_email=owner_email,
                conversation_count=conv_count,
                oauth_token_count=oauth_count,
                tool_permission_count=perm_count,
                workspace_count=ws_count,
                mcp_server_count=mcp_count,
                credential_count=cred_count,
            )
        )

    return ContextList(contexts=context_infos, total=len(context_infos))


@router.get(
    "/{context_id}", response_model=ContextDetailResponse, dependencies=[Depends(verify_admin_user)]
)
async def get_context_details(
    context_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> ContextDetailResponse:
    """Get detailed information about a specific context.

    Args:
        context_id: Context UUID
        session: Database session

    Returns:
        Detailed context information including all related entities

    Raises:
        HTTPException: 404 if context not found

    Security:
        Requires admin role via Entra ID authentication.
    """
    # Get context
    stmt = select(Context).where(Context.id == context_id)
    result = await session.execute(stmt)
    ctx = result.scalar_one_or_none()

    if not ctx:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Context {context_id} not found",
        )

    # Get conversations
    conv_stmt = select(Conversation).where(Conversation.context_id == context_id)
    conv_result = await session.execute(conv_stmt)
    conversations = conv_result.scalars().all()

    # Get OAuth tokens (with sensitive data masked)
    oauth_stmt = select(OAuthToken).where(OAuthToken.context_id == context_id)
    oauth_result = await session.execute(oauth_stmt)
    oauth_tokens = oauth_result.scalars().all()

    # Get tool permissions
    perm_stmt = select(ToolPermission).where(ToolPermission.context_id == context_id)
    perm_result = await session.execute(perm_stmt)
    tool_permissions = perm_result.scalars().all()

    # Get credentials (without decrypted values)
    cred_stmt = select(UserCredential).where(UserCredential.context_id == context_id)
    cred_result = await session.execute(cred_stmt)
    credentials = cred_result.scalars().all()

    now = datetime.now(UTC).replace(tzinfo=None)

    return ContextDetailResponse(
        id=ctx.id,
        name=ctx.name,
        type=ctx.type,
        config=ctx.config,
        pinned_files=ctx.pinned_files,
        default_cwd=ctx.default_cwd,
        conversations=[
            {
                "id": str(conv.id),
                "platform": conv.platform,
                "platform_id": conv.platform_id,
                "current_cwd": conv.current_cwd,
                "created_at": conv.created_at.isoformat(),
            }
            for conv in conversations
        ],
        oauth_tokens=[
            {
                "id": str(token.id),
                "provider": token.provider,
                "token_type": token.token_type,
                "expires_at": token.expires_at.isoformat(),
                "is_expired": token.expires_at < now,
                "has_refresh_token": token.has_refresh_token(),
                "scope": token.scope,
                "created_at": token.created_at.isoformat(),
                "updated_at": token.updated_at.isoformat(),
            }
            for token in oauth_tokens
        ],
        tool_permissions=[
            {
                "id": str(perm.id),
                "tool_name": perm.tool_name,
                "allowed": perm.allowed,
                "created_at": perm.created_at.isoformat(),
            }
            for perm in tool_permissions
        ],
        credentials=[
            {
                "id": str(cred.id),
                "credential_type": cred.credential_type,
                "metadata": cred.credential_metadata or {},
                "created_at": cred.created_at.isoformat(),
                "updated_at": cred.updated_at.isoformat(),
            }
            for cred in credentials
        ],
    )


@router.get("/{context_id}/", response_class=UTF8HTMLResponse)
async def context_detail_page(
    context_id: UUID,
    admin: AdminUser = Depends(require_admin_or_redirect),
    session: AsyncSession = Depends(get_db),
) -> str:
    """Context detail page with tabbed sub-views."""
    stmt = select(Context).where(Context.id == context_id)
    result = await session.execute(stmt)
    ctx = result.scalar_one_or_none()

    if not ctx:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Context {context_id} not found",
        )

    template_path = Path(__file__).parent / "templates" / "admin_context_detail.html"
    parts = template_path.read_text(encoding="utf-8").split("<!-- SECTION_SEPARATOR -->")

    content = (parts[0] if len(parts) > 0 else "").replace("__CONTEXT_ID__", str(context_id))
    extra_css = parts[1] if len(parts) > 1 else ""
    extra_js = (parts[2] if len(parts) > 2 else "").replace("__CONTEXT_ID__", str(context_id))

    return render_admin_page(
        title=f"Context: {ctx.name}",
        active_page=f"/platformadmin/contexts/{context_id}/",
        content=content,
        user_name=admin.display_name or admin.email.split("@")[0],
        user_email=admin.email,
        breadcrumbs=[
            ("Contexts", "/platformadmin/contexts/"),
            (ctx.name, "#"),
        ],
        extra_css=extra_css,
        extra_js=extra_js,
    )


@router.get(
    "/{context_id}/members",
    dependencies=[Depends(verify_admin_user)],
)
async def get_context_members(
    context_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Get users linked to a context."""
    stmt = (
        select(User.id, User.email, User.display_name, UserContext.role, UserContext.is_default)
        .join(UserContext, User.id == UserContext.user_id)
        .where(UserContext.context_id == context_id)
        .order_by(UserContext.role, User.display_name)
    )
    result = await session.execute(stmt)
    rows = result.all()

    members = [
        {
            "user_id": str(uid),
            "email": email,
            "display_name": display_name or email.split("@")[0],
            "role": role,
            "is_default": is_default,
        }
        for uid, email, display_name, role, is_default in rows
    ]

    return {"members": members, "total": len(members)}


@router.post(
    "",
    response_model=CreateContextResponse,
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def create_context(
    request: CreateContextRequest,
    session: AsyncSession = Depends(get_db),
) -> CreateContextResponse:
    """Create a new context.

    Args:
        request: Context creation parameters
        session: Database session

    Returns:
        Created context ID

    Raises:
        HTTPException: 400 if context name already exists

    Security:
        Requires admin role via Entra ID authentication.
    """
    # Check if context name already exists
    stmt = select(Context).where(Context.name == request.name)
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Context with name '{request.name}' already exists",
        )

    # Create new context
    context = Context(
        name=request.name,
        type=request.type,
        config=request.config,
        pinned_files=request.pinned_files,
        default_cwd=request.default_cwd,
    )

    session.add(context)
    await session.commit()
    await session.refresh(context)

    LOGGER.info(f"Admin created context {context.id} (name: {context.name})")

    return CreateContextResponse(
        success=True,
        message=f"Created context '{context.name}'",
        context_id=context.id,
    )


@router.delete(
    "/{context_id}",
    response_model=DeleteContextResponse,
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def delete_context(
    context_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> DeleteContextResponse:
    """Delete a context and all related data.

    This will cascade delete:
    - All conversations in this context
    - All OAuth tokens for this context
    - All tool permissions for this context

    Args:
        context_id: Context UUID to delete
        session: Database session

    Returns:
        Success confirmation

    Raises:
        HTTPException: 404 if context not found

    Security:
        Requires admin role via Entra ID authentication.
    """
    stmt = select(Context).where(Context.id == context_id)
    result = await session.execute(stmt)
    ctx = result.scalar_one_or_none()

    if not ctx:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Context {context_id} not found",
        )

    context_name = ctx.name

    # Delete context (cascade will delete related entities)
    await session.delete(ctx)
    await session.commit()

    LOGGER.info("Admin deleted context (found and removed)")

    # Note: MCP clients will be automatically cleaned up on next access
    # since the context no longer exists in the database

    return DeleteContextResponse(
        success=True,
        message=f"Deleted context '{context_name}' and all related data",
        deleted_context_id=context_id,
    )


@router.get(
    "/{context_id}/credentials",
    dependencies=[Depends(verify_admin_user)],
)
async def get_context_credentials(
    context_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Get credentials for a context (without decrypted values)."""
    stmt = select(UserCredential).where(UserCredential.context_id == context_id)
    result = await session.execute(stmt)
    credentials = result.scalars().all()

    return {
        "credentials": [
            {
                "id": str(cred.id),
                "credential_type": cred.credential_type,
                "metadata": cred.credential_metadata or {},
                "created_at": cred.created_at.isoformat(),
                "updated_at": cred.updated_at.isoformat(),
            }
            for cred in credentials
        ],
        "total": len(credentials),
    }


@router.post(
    "/{context_id}/credentials",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def create_context_credential(
    context_id: UUID,
    request: dict[str, Any],
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Create a credential for a context."""
    from core.auth.credential_service import CredentialService
    from core.core.config import get_settings

    settings = get_settings()
    if not settings.credential_encryption_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credential encryption not configured",
        )

    # Verify context exists
    ctx_stmt = select(Context).where(Context.id == context_id)
    ctx_result = await session.execute(ctx_stmt)
    ctx = ctx_result.scalar_one_or_none()
    if not ctx:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Context not found")

    cred_service = CredentialService(settings.credential_encryption_key)
    credential = await cred_service.store_credential(
        context_id=context_id,
        credential_type=request.get("credential_type", "azure_devops_pat"),
        value=request.get("value", ""),
        metadata=request.get("metadata"),
        session=session,
    )
    await session.commit()

    return {"success": True, "credential_id": str(credential.id)}


@router.delete(
    "/{context_id}/credentials/{credential_id}",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def delete_context_credential(
    context_id: UUID,
    credential_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Delete a credential from a context."""
    stmt = select(UserCredential).where(
        UserCredential.id == credential_id,
        UserCredential.context_id == context_id,
    )
    result = await session.execute(stmt)
    credential = result.scalar_one_or_none()

    if not credential:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")

    await session.delete(credential)
    await session.commit()

    return {"success": True, "message": f"Deleted {credential.credential_type} credential"}


__all__ = ["router"]
