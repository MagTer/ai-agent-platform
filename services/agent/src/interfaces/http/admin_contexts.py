"""Admin endpoints for context management."""

# ruff: noqa: E501
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.db.models import Context, Conversation, ToolPermission
from core.db.oauth_models import OAuthToken
from interfaces.http.admin_auth import verify_admin_user

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/contexts",
    tags=["admin", "contexts"],
)


@router.get("/", response_class=HTMLResponse, dependencies=[Depends(verify_admin_user)])
async def contexts_dashboard() -> str:
    """Context management dashboard.

    Security:
        Requires admin role via Entra ID authentication.
    """
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Contexts - Admin</title>
    <style>
        :root { --primary: #f59e0b; --bg: #f8fafc; --card: #fff; --border: #e2e8f0; --text: #1e293b; --muted: #64748b; --success: #10b981; --error: #ef4444; }
        body { font-family: system-ui, sans-serif; margin: 0; background: var(--bg); color: var(--text); }
        .header { background: linear-gradient(135deg, #1e293b, #334155); color: white; padding: 24px; }
        .header h1 { margin: 0 0 4px 0; font-size: 20px; }
        .header p { margin: 0; opacity: 0.8; font-size: 13px; }
        .nav { padding: 8px 24px; background: var(--card); border-bottom: 1px solid var(--border); }
        .nav a { color: var(--primary); text-decoration: none; font-size: 13px; }
        .container { max-width: 900px; margin: 24px auto; padding: 0 24px; }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 16px; }
        .card h2 { margin: 0 0 16px 0; font-size: 16px; display: flex; justify-content: space-between; align-items: center; }
        .context-list { margin-top: 16px; }
        .context { padding: 16px; border: 1px solid var(--border); border-radius: 6px; margin-bottom: 8px; }
        .context-name { font-weight: 600; font-size: 15px; margin-bottom: 4px; }
        .context-meta { font-size: 12px; color: var(--muted); display: flex; gap: 16px; margin-top: 8px; }
        .context-id { font-family: monospace; font-size: 11px; color: var(--muted); }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; background: #e0e7ff; color: #3730a3; }
        .loading { color: var(--muted); font-style: italic; }
        .btn { padding: 6px 12px; border-radius: 4px; font-size: 12px; cursor: pointer; border: 1px solid var(--border); background: var(--card); }
        .btn:hover { background: var(--bg); }
        .btn-sm { padding: 4px 8px; font-size: 11px; }
        .btn-danger { color: var(--error); border-color: var(--error); }
        .btn-danger:hover { background: #fee2e2; }
        .stat { font-size: 24px; font-weight: 600; color: var(--primary); }
    </style>
</head>
<body>
    <div class="header">
        <h1>Contexts</h1>
        <p>Manage conversation contexts and resources</p>
    </div>
    <div class="nav"><a href="/admin/">&larr; Back to Admin Portal</a></div>
    <div class="container">
        <div class="card">
            <h2>
                <span>All Contexts <span id="count" class="badge">0</span></span>
                <button class="btn" onclick="loadContexts()">Refresh</button>
            </h2>
            <div class="context-list" id="contexts">
                <div class="loading">Loading...</div>
            </div>
        </div>
    </div>
    <script>
        async function loadContexts() {
            try {
                const res = await fetch('/admin/contexts');
                const data = await res.json();
                renderContexts(data);
            } catch (e) {
                document.getElementById('contexts').innerHTML = '<div style="color: var(--error)">Failed to load contexts</div>';
            }
        }
        function renderContexts(data) {
            document.getElementById('count').textContent = data.total || 0;
            const el = document.getElementById('contexts');
            if (!data.contexts || data.contexts.length === 0) {
                el.innerHTML = '<div class="loading">No contexts found</div>';
                return;
            }
            el.innerHTML = data.contexts.map(c => `
                <div class="context">
                    <div class="context-name">${escapeHtml(c.name)}</div>
                    <div class="context-id">${c.id}</div>
                    <div class="context-meta">
                        <span>Type: <span class="badge">${c.type}</span></span>
                        <span>Conversations: ${c.conversation_count}</span>
                        <span>OAuth tokens: ${c.oauth_token_count}</span>
                        <span>Tool permissions: ${c.tool_permission_count}</span>
                    </div>
                </div>
            `).join('');
        }
        function escapeHtml(str) {
            if (!str) return '';
            const div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        }
        loadContexts();
    </script>
</body>
</html>"""


class ContextInfo(BaseModel):
    """Context information for admin display."""

    id: UUID
    name: str
    type: str
    config: dict[str, Any]
    pinned_files: list[str]
    default_cwd: str
    conversation_count: int
    oauth_token_count: int
    tool_permission_count: int


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


class CreateContextRequest(BaseModel):
    """Request to create a new context."""

    name: str
    type: str = "virtual"
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
    stmt = select(Context)

    if type_filter:
        stmt = stmt.where(Context.type == type_filter)

    result = await session.execute(stmt)
    contexts = result.scalars().all()

    context_infos = []
    for ctx in contexts:
        # Count related entities
        conv_count_stmt = (
            select(func.count()).select_from(Conversation).where(Conversation.context_id == ctx.id)
        )
        conv_count = await session.scalar(conv_count_stmt) or 0

        oauth_count_stmt = (
            select(func.count()).select_from(OAuthToken).where(OAuthToken.context_id == ctx.id)
        )
        oauth_count = await session.scalar(oauth_count_stmt) or 0

        perm_count_stmt = (
            select(func.count())
            .select_from(ToolPermission)
            .where(ToolPermission.context_id == ctx.id)
        )
        perm_count = await session.scalar(perm_count_stmt) or 0

        context_infos.append(
            ContextInfo(
                id=ctx.id,
                name=ctx.name,
                type=ctx.type,
                config=ctx.config,
                pinned_files=ctx.pinned_files,
                default_cwd=ctx.default_cwd,
                conversation_count=conv_count,
                oauth_token_count=oauth_count,
                tool_permission_count=perm_count,
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
                "has_refresh_token": token.refresh_token is not None,
                "scope": token.scope,
                "created_at": token.created_at.isoformat(),
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
    )


@router.post("", response_model=CreateContextResponse, dependencies=[Depends(verify_admin_user)])
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
    "/{context_id}", response_model=DeleteContextResponse, dependencies=[Depends(verify_admin_user)]
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

    LOGGER.info(f"Admin deleted context {context_id} (name: {context_name})")

    # Note: MCP clients will be automatically cleaned up on next access
    # since the context no longer exists in the database

    return DeleteContextResponse(
        success=True,
        message=f"Deleted context '{context_name}' and all related data",
        deleted_context_id=context_id,
    )


__all__ = ["router"]
