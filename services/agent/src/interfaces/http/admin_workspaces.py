"""Admin endpoints for workspace (git repository) management."""

# ruff: noqa: E501
from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.db.models import Context, Workspace
from interfaces.http.admin_auth import verify_admin_user
from interfaces.http.csrf import require_csrf
from shared.sanitize import sanitize_log

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platformadmin/workspaces",
    tags=["platform-admin", "workspaces"],
)


# Pydantic models
class WorkspaceInfo(BaseModel):
    """Workspace information for admin display."""

    id: UUID
    context_id: UUID
    context_name: str
    name: str
    repo_url: str
    branch: str
    local_path: str
    status: str
    last_synced_at: datetime | None
    sync_error: str | None
    created_at: datetime


class WorkspaceList(BaseModel):
    """List of workspaces."""

    workspaces: list[WorkspaceInfo]
    total: int


class CreateWorkspaceRequest(BaseModel):
    """Request to create (clone) a new workspace."""

    context_id: UUID
    repo_url: str
    name: str | None = None
    branch: str | None = None


class CreateWorkspaceResponse(BaseModel):
    """Response after creating a workspace."""

    success: bool
    message: str
    workspace_id: UUID


class SyncWorkspaceResponse(BaseModel):
    """Response after syncing a workspace."""

    success: bool
    message: str


class DeleteWorkspaceResponse(BaseModel):
    """Response after deleting a workspace."""

    success: bool
    message: str


@router.get("/list", response_model=WorkspaceList, dependencies=[Depends(verify_admin_user)])
async def list_workspaces(
    context_id: UUID | None = None,
    session: AsyncSession = Depends(get_db),
) -> WorkspaceList:
    """List all workspaces with optional context filter."""
    stmt = select(Workspace, Context.name).join(Context, Workspace.context_id == Context.id)

    if context_id:
        stmt = stmt.where(Workspace.context_id == context_id)

    stmt = stmt.order_by(Workspace.created_at.desc())

    result = await session.execute(stmt)
    rows = result.all()

    workspaces = [
        WorkspaceInfo(
            id=ws.id,
            context_id=ws.context_id,
            context_name=ctx_name,
            name=ws.name,
            repo_url=ws.repo_url,
            branch=ws.branch,
            local_path=ws.local_path,
            status=ws.status,
            last_synced_at=ws.last_synced_at,
            sync_error=ws.sync_error,
            created_at=ws.created_at,
        )
        for ws, ctx_name in rows
    ]

    return WorkspaceList(workspaces=workspaces, total=len(workspaces))


@router.post(
    "",
    response_model=CreateWorkspaceResponse,
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def create_workspace(
    request: CreateWorkspaceRequest,
    session: AsyncSession = Depends(get_db),
) -> CreateWorkspaceResponse:
    """Create a new workspace by cloning a git repository."""
    import os

    # Verify context exists
    ctx_stmt = select(Context).where(Context.id == request.context_id)
    ctx_result = await session.execute(ctx_stmt)
    context = ctx_result.scalar_one_or_none()

    if not context:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Context {request.context_id} not found",
        )

    # Derive workspace name if not provided
    name = request.name
    if not name:
        name = request.repo_url.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]

    # Sanitize name to prevent path traversal
    import re

    # Sanitize name: only allow safe characters (alphanumeric, dash, underscore, dot)
    name = re.sub(r"[^a-zA-Z0-9._-]", "-", name)
    if not name or name in (".", ".."):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid workspace name",
        )

    # Check if workspace already exists
    existing_stmt = select(Workspace).where(
        Workspace.context_id == request.context_id,
        Workspace.repo_url == request.repo_url,
    )
    existing_result = await session.execute(existing_stmt)
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace for this repository already exists in this context",
        )

    # Determine local path using a generated directory name (UUID) to avoid
    # any user-controlled data in filesystem paths
    workspace_base = Path(
        os.environ.get("AGENT_WORKSPACE_BASE", "/tmp/agent-workspaces")  # noqa: S108
    )
    import uuid as _uuid

    dir_name = str(_uuid.uuid4())
    # Use context.id from DB (trusted) instead of request.context_id (user input)
    workspace_dir = workspace_base / str(context.id) / dir_name
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # Create workspace record with pending status
    workspace = Workspace(
        context_id=request.context_id,
        name=name,
        repo_url=request.repo_url,
        branch=request.branch or "main",
        local_path=str(workspace_dir),
        status="pending",
    )
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)

    # Clone repository asynchronously
    try:
        workspace.status = "syncing"
        await session.commit()

        cmd = ["git", "clone", "--depth", "100"]
        if request.branch:
            cmd.extend(["--branch", request.branch])
        cmd.extend([request.repo_url, str(workspace_dir)])

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)

        if process.returncode != 0:
            error_msg = stderr.decode().strip()
            workspace.status = "error"
            workspace.sync_error = error_msg[:500]  # Limit error message length
            await session.commit()
            LOGGER.error("Git clone failed for workspace %s: %s", workspace.id, error_msg)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Git clone failed: {error_msg}",
            )

        workspace.status = "cloned"
        workspace.last_synced_at = datetime.now(UTC).replace(tzinfo=None)
        workspace.sync_error = None
        await session.commit()

        LOGGER.info(
            "Admin created workspace %s (name: %s, repo: %s)",
            workspace.id,
            sanitize_log(name),
            sanitize_log(request.repo_url),
        )

        return CreateWorkspaceResponse(
            success=True,
            message=f"Cloned repository to {workspace_dir}",
            workspace_id=workspace.id,
        )

    except TimeoutError as e:
        workspace.status = "error"
        workspace.sync_error = "Clone timed out after 5 minutes"
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Git clone timed out after 5 minutes",
        ) from e


@router.post(
    "/{workspace_id}/sync",
    response_model=SyncWorkspaceResponse,
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def sync_workspace(
    workspace_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> SyncWorkspaceResponse:
    """Sync (pull latest) a workspace."""
    stmt = select(Workspace).where(Workspace.id == workspace_id)
    result = await session.execute(stmt)
    workspace = result.scalar_one_or_none()

    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found",
        )

    local_path = Path(workspace.local_path)
    if not local_path.exists() or not (local_path / ".git").exists():
        workspace.status = "error"
        workspace.sync_error = "Local directory not found or not a git repository"
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace directory not found. Please delete and recreate.",
        )

    try:
        workspace.status = "syncing"
        await session.commit()

        # Fetch and reset to handle any diverged history
        fetch_cmd = ["git", "fetch", "origin"]
        process = await asyncio.create_subprocess_exec(
            *fetch_cmd,
            cwd=local_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(process.communicate(), timeout=120)

        # Reset to origin branch
        reset_cmd = ["git", "reset", "--hard", f"origin/{workspace.branch}"]
        process = await asyncio.create_subprocess_exec(
            *reset_cmd,
            cwd=local_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)

        if process.returncode != 0:
            error_msg = stderr.decode().strip()
            workspace.status = "error"
            workspace.sync_error = error_msg[:500]
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Sync failed: {error_msg}",
            )

        workspace.status = "cloned"
        workspace.last_synced_at = datetime.now(UTC).replace(tzinfo=None)
        workspace.sync_error = None
        await session.commit()

        LOGGER.info("Admin synced workspace %s", sanitize_log(workspace_id))

        return SyncWorkspaceResponse(success=True, message="Workspace synced successfully")

    except TimeoutError as e:
        workspace.status = "error"
        workspace.sync_error = "Sync timed out"
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Sync timed out",
        ) from e


@router.delete(
    "/{workspace_id}",
    response_model=DeleteWorkspaceResponse,
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def delete_workspace(
    workspace_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> DeleteWorkspaceResponse:
    """Delete a workspace and its local files."""
    stmt = select(Workspace).where(Workspace.id == workspace_id)
    result = await session.execute(stmt)
    workspace = result.scalar_one_or_none()

    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found",
        )

    workspace_name = workspace.name
    local_path = Path(workspace.local_path)

    # Delete local files if they exist
    if local_path.exists():
        try:
            shutil.rmtree(local_path)
            LOGGER.info("Deleted workspace files: %s", local_path)
        except Exception as e:
            LOGGER.warning("Failed to delete workspace files %s: %s", local_path, e)

    # Delete database record
    await session.delete(workspace)
    await session.commit()

    LOGGER.info(
        "Admin deleted workspace %s (name: %s)",
        sanitize_log(workspace_id),
        sanitize_log(workspace_name),
    )

    return DeleteWorkspaceResponse(
        success=True,
        message=f"Deleted workspace '{workspace_name}'",
    )


__all__ = ["router"]
