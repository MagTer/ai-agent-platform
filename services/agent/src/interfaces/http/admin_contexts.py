"""Admin endpoints for context management."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.db.models import Context, Conversation, ToolPermission
from core.db.oauth_models import OAuthToken

from .admin_auth import verify_admin_api_key

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/contexts",
    tags=["admin", "contexts"],
    dependencies=[Depends(verify_admin_api_key)],
)


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


@router.get("", response_model=ContextList)
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
        Requires admin API key via X-API-Key header
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


@router.get("/{context_id}", response_model=ContextDetailResponse)
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
        Requires admin API key via X-API-Key header
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

    now = datetime.utcnow()

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


@router.post("", response_model=CreateContextResponse)
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
        Requires admin API key via X-API-Key header
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


@router.delete("/{context_id}", response_model=DeleteContextResponse)
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
        Requires admin API key via X-API-Key header
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
