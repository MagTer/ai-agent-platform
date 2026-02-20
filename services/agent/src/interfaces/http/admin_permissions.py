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
from interfaces.http.admin_auth import AdminUser, verify_admin_user
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
