"""Admin endpoints for context management."""

# ruff: noqa: E501
from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from shared.sanitize import sanitize_log
from sqlalchemy import desc, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.db.models import (
    Context,
    Conversation,
    McpServer,
    Message,
    Session,
    SkillImprovementProposal,
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

    <div style="display: flex; justify-content: flex-end; gap: 8px; margin-bottom: 16px;">
        <button class="btn btn-primary" onclick="showCreateModal()">+ Create Context</button>
        <button class="btn" onclick="loadContexts()">Refresh</button>
    </div>

    <div class="card" id="personalSection" style="display:none;">
        <div class="card-header">
            <span class="card-title">Personal Contexts <span id="personalCount" class="badge badge-info">0</span></span>
        </div>
        <div id="personalList"></div>
    </div>

    <div class="card" id="sharedSection" style="display:none;">
        <div class="card-header">
            <span class="card-title">Shared Contexts <span id="sharedCount" class="badge badge-info">0</span></span>
        </div>
        <div id="sharedList"></div>
    </div>

    <details id="systemSection" style="display:none; margin-bottom: 16px;">
        <summary style="cursor:pointer; padding: 12px 16px; background: var(--bg-card); border: 1px solid var(--border); border-radius: 6px; font-weight: 600; font-size: 14px; color: var(--text-muted); list-style: none; display: flex; align-items: center; gap: 8px;">
            <span>&#x25B6;</span> System <span id="systemCount" class="badge" style="background:#e5e7eb;color:#374151;">0</span>
        </summary>
        <div id="systemList" style="margin-top: 8px;"></div>
    </details>

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
    .badge-type-system { background: #d1d5db; color: #374151; font-size: 10px; letter-spacing: 0.05em; }
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

    function renderContextCard(c) {
        const ownerLine = c.owner_email
            ? '<span style="font-weight:500;">Owner: ' + escapeHtml(c.owner_email) + '</span>'
            : '<span style="color:var(--text-muted);font-style:italic;">No owner</span>';
        const systemBadge = c.type === 'system'
            ? '<span class="badge badge-type-system" style="margin-left:8px;">SYSTEM</span>'
            : '';
        return '<a href="/platformadmin/contexts/' + c.id + '/" class="context-card">' +
            '<div class="context-header"><div style="display:flex;align-items:center;">' +
            '<div class="context-name">' + escapeHtml(c.name) + '</div>' +
            systemBadge +
            '</div></div>' +
            '<div class="context-id">' + c.id + '</div>' +
            '<div class="context-meta">' +
            ownerLine +
            '<span>Conversations: ' + c.conversation_count + '</span>' +
            '<span>OAuth: ' + c.oauth_token_count + '</span>' +
            '<span>Permissions: ' + c.tool_permission_count + '</span>' +
            '<span>Workspaces: ' + c.workspace_count + '</span>' +
            '<span>MCP: ' + c.mcp_server_count + '</span>' +
            '<span>Credentials: ' + c.credential_count + '</span>' +
            '</div></a>';
    }

    function renderContexts(data) {
        const contexts = data.contexts || [];
        const system   = contexts.filter(c => c.type === 'system');
        const personal = contexts.filter(c => c.type === 'personal');
        const shared   = contexts.filter(c => c.type !== 'personal' && c.type !== 'system');

        document.getElementById('totalContexts').textContent = data.total || 0;
        document.getElementById('personalContexts').textContent = personal.length;
        document.getElementById('sharedContexts').textContent = shared.length;

        const personalSection = document.getElementById('personalSection');
        const sharedSection = document.getElementById('sharedSection');
        const systemSection = document.getElementById('systemSection');

        if (personal.length > 0) {
            personalSection.style.display = '';
            document.getElementById('personalCount').textContent = personal.length;
            document.getElementById('personalList').innerHTML = personal.map(renderContextCard).join('');
        } else {
            personalSection.style.display = 'none';
        }

        if (shared.length > 0) {
            sharedSection.style.display = '';
            document.getElementById('sharedCount').textContent = shared.length;
            document.getElementById('sharedList').innerHTML = shared.map(renderContextCard).join('');
        } else {
            sharedSection.style.display = 'none';
        }

        if (system.length > 0) {
            systemSection.style.display = '';
            document.getElementById('systemCount').textContent = system.length;
            document.getElementById('systemList').innerHTML = system.map(renderContextCard).join('');
        } else {
            systemSection.style.display = 'none';
        }

        if (contexts.length === 0) {
            sharedSection.style.display = '';
            document.getElementById('sharedList').innerHTML = '<div class="empty-state">No contexts found</div>';
        }
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
    display_name: str | None
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
    is_personal: bool


class ContextList(BaseModel):
    """List of contexts."""

    contexts: list[ContextInfo]
    total: int


class ContextDetailResponse(BaseModel):
    """Detailed context information."""

    id: UUID
    name: str
    display_name: str | None
    type: str
    config: dict[str, Any]
    pinned_files: list[str]
    default_cwd: str
    conversations: list[dict[str, Any]]
    oauth_tokens: list[dict[str, Any]]
    tool_permissions: list[dict[str, Any]]
    credentials: list[dict[str, Any]]
    is_personal: bool


class CreateContextRequest(BaseModel):
    """Request to create a new context."""

    name: str = Field(..., min_length=1, max_length=255, description="Context name")
    type: str = Field(default="shared", description="Context type")
    config: dict[str, Any] = Field(default_factory=dict, description="Context configuration")
    pinned_files: list[str] = Field(default_factory=list, description="Pinned files")
    default_cwd: str = Field(default="/tmp", description="Default working directory")  # noqa: S108

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate context name format."""
        if not v.strip():
            raise ValueError("Context name cannot be empty or whitespace only")
        # Only allow alphanumeric, underscore, hyphen, and space
        import re

        if not re.match(r"^[a-zA-Z0-9_\- ]+$", v):
            raise ValueError(
                "Context name can only contain letters, numbers, spaces, hyphens, and underscores"
            )
        return v.strip()

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        """Validate context type."""
        valid_types = {"personal", "shared", "virtual", "git_repo", "devops", "system"}
        if v not in valid_types:
            raise ValueError(f"Invalid context type. Must be one of: {', '.join(valid_types)}")
        return v


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


class CreateCredentialRequest(BaseModel):
    """Request to create a credential for a context."""

    credential_type: str = Field(..., min_length=1, max_length=100, description="Credential type")
    value: str = Field(..., min_length=1, description="Credential value (will be encrypted)")
    metadata: dict[str, Any] | None = Field(default=None, description="Optional metadata")

    @field_validator("credential_type")
    @classmethod
    def validate_credential_type(cls, v: str) -> str:
        """Validate credential type format."""
        if not v.strip():
            raise ValueError("Credential type cannot be empty")
        return v.strip()

    @field_validator("value")
    @classmethod
    def validate_value(cls, v: str) -> str:
        """Validate credential value is not empty."""
        if not v.strip():
            raise ValueError("Credential value cannot be empty")
        return v


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

    # Subquery: is_personal (context has a default UserContext)
    personal_subq = (
        select(UserContext.context_id, func.bool_or(UserContext.is_default).label("is_personal"))
        .where(UserContext.is_default == True)  # noqa: E712
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
            personal_subq.c.is_personal,
        )
        .outerjoin(Conversation, Conversation.context_id == Context.id)
        .outerjoin(OAuthToken, OAuthToken.context_id == Context.id)
        .outerjoin(ToolPermission, ToolPermission.context_id == Context.id)
        .outerjoin(Workspace, Workspace.context_id == Context.id)
        .outerjoin(McpServer, McpServer.context_id == Context.id)
        .outerjoin(UserCredential, UserCredential.context_id == Context.id)
        .outerjoin(owner_subq, owner_subq.c.context_id == Context.id)
        .outerjoin(personal_subq, personal_subq.c.context_id == Context.id)
        .group_by(Context.id, owner_subq.c.owner_email, personal_subq.c.is_personal)
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
        is_personal,
    ) in rows:
        context_infos.append(
            ContextInfo(
                id=ctx.id,
                name=ctx.name,
                display_name=ctx.display_name,
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
                is_personal=bool(is_personal),
            )
        )

    return ContextList(contexts=context_infos, total=len(context_infos))


@router.get(
    "/{context_id}", response_model=ContextDetailResponse, dependencies=[Depends(verify_admin_user)]
)
async def get_context_details(
    context_id: UUID,
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_db),
) -> ContextDetailResponse:
    """Get detailed information about a specific context.

    Args:
        context_id: Context UUID
        limit: Maximum number of conversations to return (default 50)
        offset: Number of conversations to skip (default 0)
        session: Database session

    Returns:
        Detailed context information including paginated conversations

    Raises:
        HTTPException: 404 if context not found

    Security:
        Requires admin role via Entra ID authentication.
    """
    # Validate pagination parameters
    if limit < 1 or limit > 500:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Limit must be between 1 and 500",
        )
    if offset < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Offset must be non-negative",
        )

    # Get context
    stmt = select(Context).where(Context.id == context_id)
    result = await session.execute(stmt)
    ctx = result.scalar_one_or_none()

    if not ctx:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Context {context_id} not found",
        )

    # Get conversations with enriched data (message count, last activity)
    conv_stmt = (
        select(
            Conversation,
            func.count(Message.id).label("message_count"),
            func.max(Message.created_at).label("last_activity"),
        )
        .outerjoin(Session, Session.conversation_id == Conversation.id)
        .outerjoin(Message, Message.session_id == Session.id)
        .where(Conversation.context_id == context_id)
        .group_by(Conversation.id)
        .order_by(desc(func.max(Message.created_at)))
        .limit(limit)
        .offset(offset)
    )
    conv_result = await session.execute(conv_stmt)
    conv_rows = conv_result.all()

    # Enrich conversations with additional details
    enriched_conversations = []
    for row in conv_rows:
        conv = row[0]
        message_count = row[1] or 0
        last_activity = row[2]

        # Get last user message for preview
        last_user_msg_stmt = (
            select(Message.content, Message.trace_id)
            .join(Session, Session.id == Message.session_id)
            .where(Session.conversation_id == conv.id, Message.role == "user")
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        last_msg_result = await session.execute(last_user_msg_stmt)
        last_msg_row = last_msg_result.first()
        last_user_msg = last_msg_row[0][:100] if last_msg_row else None
        last_trace_id = last_msg_row[1] if last_msg_row else None

        # Get error count from messages with trace_ids linked to error spans
        # For now, we'll approximate by checking if conversation_metadata has errors
        error_count = 0
        if conv.conversation_metadata:
            # Check for pending_hitl or other error indicators
            if "pending_hitl" in conv.conversation_metadata:
                error_count = 1

        enriched_conversations.append(
            {
                "conversation": conv,
                "message_count": message_count,
                "last_activity": last_activity,
                "last_user_message": last_user_msg,
                "last_trace_id": last_trace_id,
                "error_count": error_count,
            }
        )

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

    # Check if this is a personal context (has a default UserContext)
    personal_check_stmt = select(UserContext).where(
        UserContext.context_id == context_id, UserContext.is_default == True  # noqa: E712
    )
    personal_check_result = await session.execute(personal_check_stmt)
    is_personal = personal_check_result.scalar_one_or_none() is not None

    now = datetime.now(UTC).replace(tzinfo=None)

    return ContextDetailResponse(
        id=ctx.id,
        name=ctx.name,
        display_name=ctx.display_name,
        type=ctx.type,
        config=ctx.config,
        pinned_files=ctx.pinned_files,
        default_cwd=ctx.default_cwd,
        is_personal=is_personal,
        conversations=[
            {
                "id": str(enriched["conversation"].id),
                "platform": enriched["conversation"].platform,
                "platform_id": enriched["conversation"].platform_id,
                "current_cwd": enriched["conversation"].current_cwd,
                "created_at": enriched["conversation"].created_at.isoformat(),
                "updated_at": (
                    enriched["conversation"].updated_at.isoformat()
                    if enriched["conversation"].updated_at
                    else None
                ),
                "message_count": enriched["message_count"],
                "last_activity": (
                    enriched["last_activity"].isoformat() if enriched["last_activity"] else None
                ),
                "last_user_message": enriched["last_user_message"],
                "last_trace_id": enriched["last_trace_id"],
                "error_count": enriched["error_count"],
            }
            for enriched in enriched_conversations
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

    # Check if this is the system context - prevent deletion
    if ctx.type == "system":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the system context.",
        )

    # Check if this is a personal/default context - prevent deletion
    default_check = select(UserContext).where(
        UserContext.context_id == context_id, UserContext.is_default == True  # noqa: E712
    )
    default_result = await session.execute(default_check)
    if default_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete a personal (default) context",
        )

    context_name = ctx.name

    # Delete context (cascade will delete related entities)
    await session.delete(ctx)
    await session.commit()

    LOGGER.info("Admin deleted context %s", sanitize_log(context_id))

    # Note: MCP clients will be automatically cleaned up on next access
    # since the context no longer exists in the database

    return DeleteContextResponse(
        success=True,
        message=f"Deleted context '{context_name}' and all related data",
        deleted_context_id=context_id,
    )


class UpdateContextRequest(BaseModel):
    """Request to update a context."""

    name: str | None = Field(default=None, max_length=255, description="Context name")
    display_name: str | None = Field(default=None, description="Friendly display name")
    type: str | None = Field(default=None, description="Context type")
    default_cwd: str | None = Field(default=None, description="Default working directory")
    config: dict[str, Any] | None = Field(default=None, description="Context configuration")
    pinned_files: list[str] | None = Field(default=None, description="Pinned files")
    memory_file: str | None = Field(default=None, description="Memory file name")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not v.strip():
            raise ValueError("Context name cannot be empty or whitespace only")
        import re

        if not re.match(r"^[a-zA-Z0-9_\- ]+$", v):
            raise ValueError(
                "Context name can only contain letters, numbers, spaces, hyphens, and underscores"
            )
        return v.strip()

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str | None) -> str | None:
        if v is None:
            return v
        valid_types = {"personal", "shared", "virtual", "git_repo", "devops"}
        if v not in valid_types:
            raise ValueError(f"Invalid context type. Must be one of: {', '.join(valid_types)}")
        return v


@router.put(
    "/{context_id}",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def update_context(
    context_id: UUID,
    request: UpdateContextRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Update context details."""
    stmt = select(Context).where(Context.id == context_id)
    result = await session.execute(stmt)
    ctx = result.scalar_one_or_none()
    if not ctx:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Context not found")

    if request.name is not None:
        # Check duplicate name (exclude self)
        dup_stmt = select(Context).where(Context.name == request.name, Context.id != context_id)
        dup_result = await session.execute(dup_stmt)
        if dup_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Context with name '{request.name}' already exists",
            )
        ctx.name = request.name
    if request.display_name is not None:
        ctx.display_name = request.display_name
    if request.type is not None:
        ctx.type = request.type
    if request.default_cwd is not None:
        ctx.default_cwd = request.default_cwd
    if request.config is not None:
        ctx.config = request.config
    if request.pinned_files is not None:
        ctx.pinned_files = request.pinned_files
    if request.memory_file is not None:
        config = dict(ctx.config) if ctx.config else {}
        config["memory_file"] = request.memory_file
        ctx.config = config

    await session.commit()
    LOGGER.info("Admin updated context %s", sanitize_log(context_id))
    return {"success": True, "message": f"Context '{ctx.name}' updated"}


class AddMemberRequest(BaseModel):
    """Request to add a member to a context."""

    email: str = Field(..., description="User email address")
    role: str = Field(default="member", description="Role: owner, member, or viewer")

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        valid_roles = {"owner", "member", "viewer"}
        if v not in valid_roles:
            raise ValueError(f"Invalid role. Must be one of: {', '.join(valid_roles)}")
        return v


@router.post(
    "/{context_id}/members",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def add_context_member(
    context_id: UUID,
    request: AddMemberRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Add a user as a member of a context."""
    # Verify context exists
    ctx_stmt = select(Context).where(Context.id == context_id)
    ctx_result = await session.execute(ctx_stmt)
    if not ctx_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Context not found")

    # Find user by email
    user_stmt = select(User).where(User.email == request.email)
    user_result = await session.execute(user_stmt)
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with email '{request.email}' not found",
        )

    # Check if already a member
    existing_stmt = select(UserContext).where(
        UserContext.context_id == context_id,
        UserContext.user_id == user.id,
    )
    existing_result = await session.execute(existing_stmt)
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is already a member of this context",
        )

    # Add membership
    user_context = UserContext(
        user_id=user.id,
        context_id=context_id,
        role=request.role,
        is_default=False,
    )
    session.add(user_context)
    await session.commit()

    LOGGER.info(
        "Admin added user %s to context %s with role %s",
        sanitize_log(user.email),
        sanitize_log(context_id),
        sanitize_log(request.role),
    )
    return {"success": True, "message": f"Added {user.email} as {request.role}"}


class UpdateMemberRequest(BaseModel):
    """Request to update a member's role."""

    role: str = Field(..., description="New role: owner, member, or viewer")

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        valid_roles = {"owner", "member", "viewer"}
        if v not in valid_roles:
            raise ValueError(f"Invalid role. Must be one of: {', '.join(valid_roles)}")
        return v


@router.put(
    "/{context_id}/members/{user_id}",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def update_context_member(
    context_id: UUID,
    user_id: UUID,
    request: UpdateMemberRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Update a member's role in a context."""
    stmt = select(UserContext).where(
        UserContext.context_id == context_id,
        UserContext.user_id == user_id,
    )
    result = await session.execute(stmt)
    uc = result.scalar_one_or_none()
    if not uc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found in this context"
        )

    uc.role = request.role
    await session.commit()

    LOGGER.info(
        "Admin updated member %s role to %s in context %s",
        sanitize_log(user_id),
        sanitize_log(request.role),
        sanitize_log(context_id),
    )
    return {"success": True, "message": f"Updated role to {request.role}"}


@router.delete(
    "/{context_id}/members/{user_id}",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def remove_context_member(
    context_id: UUID,
    user_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Remove a member from a context."""
    stmt = select(UserContext).where(
        UserContext.context_id == context_id,
        UserContext.user_id == user_id,
    )
    result = await session.execute(stmt)
    uc = result.scalar_one_or_none()
    if not uc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found in this context"
        )

    await session.delete(uc)
    await session.commit()

    LOGGER.info(
        "Admin removed member %s from context %s", sanitize_log(user_id), sanitize_log(context_id)
    )
    return {"success": True, "message": "Member removed"}


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
    request: CreateCredentialRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Create a credential for a context.

    Args:
        context_id: Context UUID
        request: Credential creation request with validation
        session: Database session

    Returns:
        Created credential ID

    Raises:
        HTTPException: 404 if context not found
        HTTPException: 503 if encryption not configured

    Security:
        Requires admin role via Entra ID authentication.
        Credential value is encrypted before storage.
    """
    from core.auth.credential_service import CredentialService
    from core.runtime.config import get_settings

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
        credential_type=request.credential_type,
        value=request.value,
        metadata=request.metadata,
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


# File management endpoints for pinned files


@router.get(
    "/{context_id}/api/files",
    dependencies=[Depends(verify_admin_user)],
)
async def list_context_files(
    context_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """List all files in the context data directory with pinned status.

    Args:
        context_id: Context UUID
        session: Database session

    Returns:
        List of files with name, size, pinned status, modified time

    Security:
        Requires admin role via Entra ID authentication.
    """
    from core.context.files import get_context_dir

    # Get context to check pinned files
    stmt = select(Context).where(Context.id == context_id)
    result = await session.execute(stmt)
    ctx = result.scalar_one_or_none()
    if not ctx:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Context not found")

    context_dir = get_context_dir(context_id)
    files_dir = context_dir / "files"

    # Ensure directory exists
    files_dir.mkdir(parents=True, exist_ok=True)

    # List files
    files = []
    for file_path in files_dir.iterdir():
        if file_path.is_file():
            stat = file_path.stat()
            abs_path = str(file_path.resolve())
            files.append(
                {
                    "name": file_path.name,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                    "pinned": abs_path in ctx.pinned_files,
                }
            )

    # Sort by name
    files.sort(key=lambda f: cast(str, f["name"]))

    return {"files": files, "total": len(files)}


@router.get(
    "/{context_id}/api/files/{file_name}",
    dependencies=[Depends(verify_admin_user)],
)
async def read_context_file(
    context_id: UUID,
    file_name: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Read a file from the context data directory.

    Args:
        context_id: Context UUID
        file_name: File name (basename only)
        session: Database session

    Returns:
        File content as string

    Raises:
        HTTPException: 404 if context or file not found
        HTTPException: 400 if file_name contains path traversal

    Security:
        Requires admin role via Entra ID authentication.
        Path traversal protection (basename only).
    """
    from core.context.files import get_context_dir

    # Security: Only allow basename (no path traversal)
    if "/" in file_name or ".." in file_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file name (path traversal not allowed)",
        )

    # Verify context exists
    stmt = select(Context).where(Context.id == context_id)
    result = await session.execute(stmt)
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Context not found")

    context_dir = get_context_dir(context_id)
    file_path = context_dir / "files" / file_name

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="File is not valid UTF-8 text",
        ) from e

    return {"name": file_name, "content": content}


class WriteFileRequest(BaseModel):
    """Request to write a file."""

    content: str = Field(..., description="File content")


class ProposalResponse(BaseModel):
    """Skill improvement proposal for API responses."""

    id: str
    skill_name: str
    skill_file_name: str
    change_summary: str
    total_executions: int
    failed_executions: int
    failure_rate: float
    status: str
    reviewed_by: str | None
    reviewed_at: str | None
    created_at: str


class ProposalDetailResponse(ProposalResponse):
    """Full proposal detail including content."""

    original_content: str
    proposed_content: str
    failure_signals: list[dict[str, object]]


@router.put(
    "/{context_id}/api/files/{file_name}",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def write_context_file(
    context_id: UUID,
    file_name: str,
    request: WriteFileRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Write a file to the context data directory.

    Args:
        context_id: Context UUID
        file_name: File name (basename only)
        request: File content
        session: Database session

    Returns:
        Success message with file size

    Raises:
        HTTPException: 404 if context not found
        HTTPException: 400 if file_name contains path traversal or invalid characters

    Security:
        Requires admin role via Entra ID authentication.
        Path traversal protection (basename only).
    """
    from core.context.files import ensure_context_directories

    # Security: Only allow basename (no path traversal)
    if "/" in file_name or ".." in file_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file name (path traversal not allowed)",
        )

    # Only allow markdown and text files
    if not file_name.endswith((".md", ".txt")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .md and .txt files are allowed",
        )

    # Verify context exists
    stmt = select(Context).where(Context.id == context_id)
    result = await session.execute(stmt)
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Context not found")

    # Ensure directory exists
    context_dir = ensure_context_directories(context_id)
    file_path = context_dir / "files" / file_name

    # Write file
    file_path.write_text(request.content, encoding="utf-8")

    LOGGER.info(
        "Admin wrote file %s for context %s (%d bytes)",
        sanitize_log(file_name),
        sanitize_log(context_id),
        len(request.content),
    )

    return {
        "success": True,
        "message": f"File '{file_name}' saved",
        "size": len(request.content.encode("utf-8")),
    }


@router.delete(
    "/{context_id}/api/files/{file_name}",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def delete_context_file(
    context_id: UUID,
    file_name: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Delete a file from the context data directory.

    Automatically unpins the file if it was pinned.

    Args:
        context_id: Context UUID
        file_name: File name (basename only)
        session: Database session

    Returns:
        Success message

    Raises:
        HTTPException: 404 if context or file not found
        HTTPException: 400 if file_name contains path traversal

    Security:
        Requires admin role via Entra ID authentication.
        Path traversal protection (basename only).
    """
    from core.context.files import get_context_dir

    # Security: Only allow basename (no path traversal)
    if "/" in file_name or ".." in file_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file name (path traversal not allowed)",
        )

    # Get context
    stmt = select(Context).where(Context.id == context_id)
    result = await session.execute(stmt)
    ctx = result.scalar_one_or_none()
    if not ctx:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Context not found")

    context_dir = get_context_dir(context_id)
    file_path = context_dir / "files" / file_name

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    # Auto-unpin if pinned
    abs_path = str(file_path.resolve())
    if abs_path in ctx.pinned_files:
        ctx.pinned_files = [p for p in ctx.pinned_files if p != abs_path]
        await session.commit()
        LOGGER.info(
            "Auto-unpinned file %s for context %s",
            sanitize_log(file_name),
            sanitize_log(context_id),
        )

    # Delete file
    file_path.unlink()

    LOGGER.info(
        "Admin deleted file %s for context %s", sanitize_log(file_name), sanitize_log(context_id)
    )

    return {"success": True, "message": f"File '{file_name}' deleted"}


@router.post(
    "/{context_id}/api/files/{file_name}/pin",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def toggle_pin_context_file(
    context_id: UUID,
    file_name: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Toggle pin status for a file.

    Args:
        context_id: Context UUID
        file_name: File name (basename only)
        session: Database session

    Returns:
        New pin status

    Raises:
        HTTPException: 404 if context or file not found
        HTTPException: 400 if file_name contains path traversal

    Security:
        Requires admin role via Entra ID authentication.
        Path traversal protection (basename only).
    """
    from core.context.files import get_context_dir

    # Security: Only allow basename (no path traversal)
    if "/" in file_name or ".." in file_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file name (path traversal not allowed)",
        )

    # Get context
    stmt = select(Context).where(Context.id == context_id)
    result = await session.execute(stmt)
    ctx = result.scalar_one_or_none()
    if not ctx:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Context not found")

    context_dir = get_context_dir(context_id)
    file_path = context_dir / "files" / file_name

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    # Toggle pin status (use absolute path)
    abs_path = str(file_path.resolve())
    if abs_path in ctx.pinned_files:
        # Unpin
        ctx.pinned_files = [p for p in ctx.pinned_files if p != abs_path]
        await session.commit()
        LOGGER.info(
            "Admin unpinned file %s for context %s",
            sanitize_log(file_name),
            sanitize_log(context_id),
        )
        return {"success": True, "pinned": False, "message": f"File '{file_name}' unpinned"}
    else:
        # Pin
        ctx.pinned_files = list(ctx.pinned_files) + [abs_path]
        await session.commit()
        LOGGER.info(
            "Admin pinned file %s for context %s", sanitize_log(file_name), sanitize_log(context_id)
        )
        return {"success": True, "pinned": True, "message": f"File '{file_name}' pinned"}


# Skill management endpoints for per-context skills


@router.get(
    "/{context_id}/api/skills",
    dependencies=[Depends(verify_admin_user)],
)
async def list_context_skills(
    context_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """List all skill files in the context skills directory.

    Args:
        context_id: Context UUID
        session: Database session

    Returns:
        List of skills with name, size, modified, and parsed frontmatter

    Security:
        Requires admin role via Entra ID authentication.
    """
    from core.context.files import get_context_dir

    # Verify context exists
    stmt = select(Context).where(Context.id == context_id)
    result = await session.execute(stmt)
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Context not found")

    context_dir = get_context_dir(context_id)
    skills_dir = context_dir / "skills"

    # Ensure directory exists
    skills_dir.mkdir(parents=True, exist_ok=True)

    # List skill files
    skills = []
    for file_path in skills_dir.iterdir():
        if file_path.is_file() and file_path.suffix == ".md":
            stat = file_path.stat()
            skill_info: dict[str, object] = {
                "name": file_path.name,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            }

            # Try to parse frontmatter
            try:
                from core.skills.registry import parse_skill_content

                content = file_path.read_text(encoding="utf-8")
                skill_obj = parse_skill_content(file_path, content, skills_dir)
                if skill_obj:
                    skill_info["description"] = skill_obj.description
                    skill_info["model"] = skill_obj.model
                    skill_info["tools"] = skill_obj.tools
                    skill_info["max_turns"] = skill_obj.max_turns
            except Exception as e:
                LOGGER.warning(f"Failed to parse skill {file_path}: {e}")
                # Still return the file, just without parsed metadata

            skills.append(skill_info)

    # Sort by name
    skills.sort(key=lambda s: cast(str, s["name"]))

    return {"skills": skills, "total": len(skills)}


@router.get(
    "/{context_id}/api/global-skills",
    dependencies=[Depends(verify_admin_user)],
)
async def list_global_skills(
    context_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """List all global (repo) skills with override status for this context.

    Args:
        context_id: Context UUID (to check for overrides)
        request: FastAPI request (to access app state)
        session: Database session

    Returns:
        List of global skills with override indicators

    Security:
        Requires admin role via Entra ID authentication.
    """
    from core.context.files import get_context_dir

    # Verify context exists
    stmt = select(Context).where(Context.id == context_id)
    result = await session.execute(stmt)
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Context not found")

    # Get global skill registry from app state
    factory = request.app.state.service_factory
    global_registry = factory._skill_registry
    if global_registry is None:
        return {"skills": [], "total": 0}

    global_skills = global_registry.list_all_skills()

    # Check which global skills have context overrides
    context_dir = get_context_dir(context_id)
    skills_dir = context_dir / "skills"

    # Load context skill names for override detection
    context_skill_names: set[str] = set()
    if skills_dir.exists():
        from core.skills.registry import parse_skill_content

        for file_path in skills_dir.iterdir():
            if file_path.is_file() and file_path.suffix == ".md":
                try:
                    content = file_path.read_text(encoding="utf-8")
                    skill_obj = parse_skill_content(file_path, content, skills_dir)
                    if skill_obj:
                        context_skill_names.add(skill_obj.name)
                except Exception as exc:  # noqa: BLE001
                    LOGGER.debug("Skipping unreadable context skill %s: %s", file_path.name, exc)

    # Annotate global skills with override status
    for skill in global_skills:
        skill["has_override"] = skill["name"] in context_skill_names

    return {"skills": global_skills, "total": len(global_skills)}


@router.post(
    "/{context_id}/api/global-skills/{skill_name}/fork",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def fork_global_skill(
    context_id: UUID,
    skill_name: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Fork a global skill into the context for customization.

    Copies the global skill's raw content to the context skills directory.
    The context skill will then override the global skill (same name in frontmatter).

    Args:
        context_id: Context UUID
        skill_name: Global skill name (from frontmatter)
        request: FastAPI request (to access app state)
        session: Database session

    Returns:
        Success message with created file name

    Raises:
        HTTPException: 404 if context or skill not found
        HTTPException: 409 if context override already exists

    Security:
        Requires admin role via Entra ID authentication.
        CSRF protection for mutation.
    """
    from core.context.files import ensure_context_directories, get_context_dir

    # Security: validate skill_name (no path traversal)
    if "/" in skill_name or ".." in skill_name or "\\" in skill_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid skill name",
        )

    # Verify context exists
    stmt = select(Context).where(Context.id == context_id)
    result = await session.execute(stmt)
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Context not found")

    # Get global skill
    factory = request.app.state.service_factory
    global_registry = factory._skill_registry
    if global_registry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Skill registry not available"
        )

    skill = global_registry.get(skill_name)
    if skill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Global skill '{skill_name}' not found",
        )

    # Check if context already has an override (by filename)
    context_dir = get_context_dir(context_id)
    skills_dir = context_dir / "skills"
    target_file = skills_dir / skill.path.name

    if target_file.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Context already has a skill file '{skill.path.name}'. Delete it first or edit it directly.",
        )

    # Copy raw content to context skills directory
    context_dir = ensure_context_directories(context_id)
    skills_dir = context_dir / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    target_file = skills_dir / skill.path.name

    target_file.write_text(skill.raw_content, encoding="utf-8")

    LOGGER.info(
        "Admin forked global skill '%s' to context %s as %s",
        sanitize_log(skill_name),
        sanitize_log(context_id),
        sanitize_log(skill.path.name),
    )

    return {
        "success": True,
        "message": f"Forked '{skill_name}' to context",
        "file_name": skill.path.name,
    }


@router.get(
    "/{context_id}/api/skills/{file_name}",
    dependencies=[Depends(verify_admin_user)],
)
async def read_context_skill(
    context_id: UUID,
    file_name: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Read a skill file from the context skills directory.

    Args:
        context_id: Context UUID
        file_name: File name (basename only, must be .md)
        session: Database session

    Returns:
        Skill content and parsed frontmatter

    Raises:
        HTTPException: 404 if context or file not found
        HTTPException: 400 if file_name contains path traversal

    Security:
        Requires admin role via Entra ID authentication.
        Path traversal protection (basename only).
    """
    from core.context.files import get_context_dir

    # Security: Only allow basename (no path traversal)
    if "/" in file_name or ".." in file_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file name (path traversal not allowed)",
        )

    # Only allow .md files
    if not file_name.endswith(".md"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .md files are allowed",
        )

    # Verify context exists
    stmt = select(Context).where(Context.id == context_id)
    result = await session.execute(stmt)
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Context not found")

    context_dir = get_context_dir(context_id)
    file_path = context_dir / "skills" / file_name

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill file not found")

    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="File is not valid UTF-8 text",
        ) from e

    # Parse frontmatter
    frontmatter: dict[str, object] = {}
    try:
        from core.skills.registry import parse_skill_content

        skill_obj = parse_skill_content(file_path, content, context_dir / "skills")
        if skill_obj:
            frontmatter = {
                "name": skill_obj.name,
                "description": skill_obj.description,
                "model": skill_obj.model,
                "tools": skill_obj.tools,
                "max_turns": skill_obj.max_turns,
                "variables": skill_obj.variables,
            }
    except Exception as e:
        LOGGER.warning(f"Failed to parse skill frontmatter: {e}")

    return {"name": file_name, "content": content, "frontmatter": frontmatter}


@router.put(
    "/{context_id}/api/skills/{file_name}",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def write_context_skill(
    context_id: UUID,
    file_name: str,
    request: WriteFileRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Write a skill file to the context skills directory.

    Args:
        context_id: Context UUID
        file_name: File name (basename only, must be .md)
        request: File content
        session: Database session

    Returns:
        Success message with file size

    Raises:
        HTTPException: 404 if context not found
        HTTPException: 400 if file_name contains path traversal or invalid characters

    Security:
        Requires admin role via Entra ID authentication.
        Path traversal protection (basename only).
    """
    from core.context.files import ensure_context_directories

    # Security: Only allow basename (no path traversal)
    if "/" in file_name or ".." in file_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file name (path traversal not allowed)",
        )

    # Only allow markdown files
    if not file_name.endswith(".md"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .md files are allowed",
        )

    # Verify context exists
    stmt = select(Context).where(Context.id == context_id)
    result = await session.execute(stmt)
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Context not found")

    # Ensure directory exists
    context_dir = ensure_context_directories(context_id)
    skills_dir = context_dir / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    file_path = skills_dir / file_name

    # Write file
    file_path.write_text(request.content, encoding="utf-8")

    LOGGER.info(
        "Admin wrote skill %s for context %s (%d bytes)",
        sanitize_log(file_name),
        sanitize_log(context_id),
        len(request.content),
    )

    return {
        "success": True,
        "message": f"Skill '{file_name}' saved",
        "size": len(request.content.encode("utf-8")),
    }


@router.delete(
    "/{context_id}/api/skills/{file_name}",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def delete_context_skill(
    context_id: UUID,
    file_name: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Delete a skill file from the context skills directory.

    Args:
        context_id: Context UUID
        file_name: File name (basename only)
        session: Database session

    Returns:
        Success message

    Raises:
        HTTPException: 404 if context or file not found
        HTTPException: 400 if file_name contains path traversal

    Security:
        Requires admin role via Entra ID authentication.
        Path traversal protection (basename only).
    """
    from core.context.files import get_context_dir

    # Security: Only allow basename (no path traversal)
    if "/" in file_name or ".." in file_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file name (path traversal not allowed)",
        )

    # Verify context exists
    stmt = select(Context).where(Context.id == context_id)
    result = await session.execute(stmt)
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Context not found")

    context_dir = get_context_dir(context_id)
    file_path = context_dir / "skills" / file_name

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill file not found")

    # Delete file
    file_path.unlink()

    LOGGER.info(
        "Admin deleted skill %s for context %s", sanitize_log(file_name), sanitize_log(context_id)
    )

    return {"success": True, "message": f"Skill '{file_name}' deleted"}


# --- Skill Improvement Proposals ---


@router.get(
    "/{context_id}/api/skill-proposals",
    dependencies=[Depends(verify_admin_user)],
)
async def list_skill_proposals(
    context_id: UUID,
    status_filter: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """List skill improvement proposals for a context.

    Args:
        context_id: Context UUID.
        status_filter: Optional status filter (applied, reverted, promoted).
        session: Database session.

    Returns:
        List of proposals with metadata.
    """
    stmt = (
        select(SkillImprovementProposal)
        .where(SkillImprovementProposal.context_id == context_id)
        .order_by(SkillImprovementProposal.created_at.desc())
    )

    if status_filter:
        stmt = stmt.where(SkillImprovementProposal.status == status_filter)

    result = await session.execute(stmt)
    proposals = result.scalars().all()

    items = []
    for p in proposals:
        total = p.total_executions or 1
        items.append(
            ProposalResponse(
                id=str(p.id),
                skill_name=p.skill_name,
                skill_file_name=p.skill_file_name,
                change_summary=p.change_summary,
                total_executions=p.total_executions,
                failed_executions=p.failed_executions,
                failure_rate=p.failed_executions / total,
                status=p.status,
                reviewed_by=p.reviewed_by,
                reviewed_at=p.reviewed_at.isoformat() if p.reviewed_at else None,
                created_at=p.created_at.isoformat(),
            ).model_dump()
        )

    applied_count = sum(1 for p in proposals if p.status == "applied")

    return {"proposals": items, "total": len(items), "pending_count": applied_count}


@router.get(
    "/{context_id}/api/skill-proposals/{proposal_id}",
    dependencies=[Depends(verify_admin_user)],
)
async def get_skill_proposal(
    context_id: UUID,
    proposal_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Get full detail for a skill improvement proposal.

    Args:
        context_id: Context UUID.
        proposal_id: Proposal UUID.
        session: Database session.

    Returns:
        Full proposal including original and proposed content.
    """
    stmt = select(SkillImprovementProposal).where(
        SkillImprovementProposal.id == proposal_id,
        SkillImprovementProposal.context_id == context_id,
    )
    result = await session.execute(stmt)
    proposal = result.scalar_one_or_none()

    if not proposal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposal not found")

    total = proposal.total_executions or 1
    return ProposalDetailResponse(
        id=str(proposal.id),
        skill_name=proposal.skill_name,
        skill_file_name=proposal.skill_file_name,
        change_summary=proposal.change_summary,
        total_executions=proposal.total_executions,
        failed_executions=proposal.failed_executions,
        failure_rate=proposal.failed_executions / total,
        status=proposal.status,
        reviewed_by=proposal.reviewed_by,
        reviewed_at=proposal.reviewed_at.isoformat() if proposal.reviewed_at else None,
        created_at=proposal.created_at.isoformat(),
        original_content=proposal.original_content,
        proposed_content=proposal.proposed_content,
        failure_signals=proposal.failure_signals,
    ).model_dump()


@router.post(
    "/{context_id}/api/skill-proposals/{proposal_id}/revert",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def revert_skill_proposal(
    context_id: UUID,
    proposal_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Revert an applied skill improvement proposal.

    The improvement was already written to the context overlay when the
    analyser ran. This undoes that change by restoring the original content:
    - If original came from global skill: delete the overlay file entirely
      (CompositeSkillRegistry falls back to global automatically)
    - If original was a previous context overlay: restore it

    Args:
        context_id: Context UUID.
        proposal_id: Proposal UUID.
        request: FastAPI request.
        session: Database session.

    Returns:
        Success message.
    """
    stmt = select(SkillImprovementProposal).where(
        SkillImprovementProposal.id == proposal_id,
        SkillImprovementProposal.context_id == context_id,
        SkillImprovementProposal.status == "applied",
    )
    result = await session.execute(stmt)
    proposal = result.scalar_one_or_none()

    if not proposal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Proposal not found or not in 'applied' status",
        )

    from core.context.files import get_context_dir

    context_dir = get_context_dir(context_id)
    overlay_file = context_dir / "skills" / proposal.skill_file_name

    # Determine whether original came from global or a previous overlay
    global_skills_base = Path("skills")  # Mount path
    global_candidates = list(global_skills_base.rglob(proposal.skill_file_name))

    original_was_global = False
    if global_candidates:
        try:
            global_content = global_candidates[0].read_text(encoding="utf-8")
            original_was_global = global_content.strip() == proposal.original_content.strip()
        except Exception as exc:
            LOGGER.debug("Could not read global skill candidate: %s", exc)

    if original_was_global:
        overlay_file.unlink(missing_ok=True)
        LOGGER.info("Reverted '%s': deleted overlay, global skill restored", proposal.skill_name)
    else:
        overlay_file.write_text(proposal.original_content, encoding="utf-8")
        LOGGER.info("Reverted '%s': restored previous overlay content", proposal.skill_name)

    # Update proposal status
    admin_email = getattr(request.state, "user_email", "admin")
    proposal.status = "reverted"
    proposal.reviewed_by = admin_email
    proposal.reviewed_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()

    LOGGER.info(
        "Admin reverted skill proposal %s for '%s' (context %s)",
        proposal_id,
        proposal.skill_name,
        context_id,
    )

    return {
        "success": True,
        "message": f"Reverted '{proposal.skill_name}'. Original skill restored.",
    }


@router.post(
    "/{context_id}/api/skill-proposals/{proposal_id}/promote",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def promote_skill_to_global(
    context_id: UUID,
    proposal_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Promote an accepted proposal to the global skills directory.

    Copies the accepted skill content to /skills/ on disk. This persists
    until the next container rebuild/deploy.

    Args:
        context_id: Context UUID.
        proposal_id: Proposal UUID.
        request: FastAPI request.
        session: Database session.

    Returns:
        Success message with global file path.
    """
    stmt = select(SkillImprovementProposal).where(
        SkillImprovementProposal.id == proposal_id,
        SkillImprovementProposal.context_id == context_id,
        SkillImprovementProposal.status == "applied",
    )
    result = await session.execute(stmt)
    proposal = result.scalar_one_or_none()

    if not proposal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Proposal not found or not in 'applied' status",
        )

    # Find the global skill to get the correct path
    factory = request.app.state.service_factory
    global_registry = factory.skill_registry
    if not global_registry:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Global skill registry not available",
        )

    global_skill = global_registry.get(proposal.skill_name)
    if not global_skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Global skill '{proposal.skill_name}' not found",
        )

    # Write to global skill path
    global_skill.path.write_text(proposal.proposed_content, encoding="utf-8")

    # Update status
    admin_email = getattr(request.state, "user_email", "admin")
    proposal.status = "promoted"
    proposal.reviewed_by = admin_email
    proposal.reviewed_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()

    LOGGER.info(
        "Admin promoted skill proposal %s to global: %s",
        proposal_id,
        global_skill.path,
    )

    return {
        "success": True,
        "message": (
            f"Promoted '{proposal.skill_name}' to global. "
            "NOTE: This writes to the container filesystem only -- "
            "changes will be lost on next deploy. "
            "To make permanent: commit the updated file to git."
        ),
        "global_path": str(global_skill.path),
    }


__all__ = ["router"]
