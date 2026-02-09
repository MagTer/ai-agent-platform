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
from shared.sanitize import sanitize_log
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.db.models import Context, Workspace
from interfaces.http.admin_auth import AdminUser, require_admin_or_redirect, verify_admin_user
from interfaces.http.admin_shared import UTF8HTMLResponse, render_admin_page
from interfaces.http.csrf import require_csrf

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platformadmin/workspaces",
    tags=["platform-admin", "workspaces"],
)


@router.get("/", response_class=UTF8HTMLResponse)
async def workspaces_dashboard(admin: AdminUser = Depends(require_admin_or_redirect)) -> str:
    """Workspace management dashboard."""
    content = """
        <h1 class="page-title">Workspaces</h1>

        <div class="card">
            <div class="card-header">
                <span>All Workspaces <span id="count" class="badge badge-info">0</span></span>
                <div style="display: flex; gap: 8px;">
                    <button class="btn btn-primary" onclick="showAddModal()">+ Add Workspace</button>
                    <button class="btn" onclick="loadWorkspaces()">Refresh</button>
                </div>
            </div>
            <div class="workspace-list" id="workspaces">
                <div class="loading">Loading...</div>
            </div>
        </div>

        <!-- Add Workspace Modal -->
        <div id="add-modal" class="modal" style="display: none;">
            <div class="modal-content">
                <h3>Add Workspace</h3>
                <form id="add-form" onsubmit="addWorkspace(event)">
                    <div class="form-group">
                        <label>Context</label>
                        <select id="context-select" required>
                            <option value="">Loading contexts...</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Repository URL</label>
                        <input type="url" id="repo-url" placeholder="https://github.com/org/repo.git" required>
                    </div>
                    <div class="form-group">
                        <label>Workspace Name (optional)</label>
                        <input type="text" id="workspace-name" placeholder="Derived from repo URL if empty">
                    </div>
                    <div class="form-group">
                        <label>Branch (optional)</label>
                        <input type="text" id="branch" placeholder="main">
                    </div>
                    <div class="modal-actions">
                        <button type="button" class="btn" onclick="hideAddModal()">Cancel</button>
                        <button type="submit" class="btn btn-primary">Clone Repository</button>
                    </div>
                </form>
            </div>
        </div>
    """

    extra_css = """
        .workspace { padding: 16px; border: 1px solid var(--border); border-radius: 6px; margin-bottom: 8px; }
        .workspace-header { display: flex; justify-content: space-between; align-items: flex-start; }
        .workspace-name { font-weight: 600; font-size: 15px; margin-bottom: 4px; }
        .workspace-repo { font-size: 13px; color: var(--text-muted); word-break: break-all; }
        .workspace-meta { font-size: 12px; color: var(--text-muted); display: flex; gap: 16px; margin-top: 8px; flex-wrap: wrap; }
        .workspace-path { font-family: monospace; font-size: 11px; color: var(--text-muted); margin-top: 4px; }
        .workspace-context { font-size: 12px; color: var(--primary); }
        .workspace-actions { display: flex; gap: 8px; }
        .status-cloned { color: var(--success); }
        .status-error { color: var(--error); }
        .status-syncing { color: var(--warning); }
        .status-pending { color: var(--text-muted); }
        .modal { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 1000; }
        .modal-content { background: var(--bg-card); padding: 24px; border-radius: 8px; width: 100%; max-width: 500px; }
        .modal-content h3 { margin: 0 0 16px 0; }
        .form-group { margin-bottom: 16px; }
        .form-group label { display: block; margin-bottom: 4px; font-size: 13px; font-weight: 500; }
        .form-group input, .form-group select { width: 100%; padding: 8px; border: 1px solid var(--border); border-radius: 4px; font-size: 14px; background: var(--bg); color: var(--text); }
        .modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
    """

    extra_js = """
        let contexts = [];

        async function loadWorkspaces() {
            try {
                const res = await fetch('/platformadmin/workspaces/list');
                const data = await res.json();
                renderWorkspaces(data);
            } catch (e) {
                document.getElementById('workspaces').innerHTML = '<div style="color: var(--error)">Failed to load workspaces</div>';
            }
        }

        async function loadContexts() {
            try {
                const res = await fetch('/platformadmin/contexts');
                const data = await res.json();
                contexts = data.contexts || [];
                const select = document.getElementById('context-select');
                select.innerHTML = '<option value="">Select a context...</option>' +
                    contexts.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
            } catch (e) {
                console.error('Failed to load contexts:', e);
            }
        }

        function renderWorkspaces(data) {
            document.getElementById('count').textContent = data.total || 0;
            const el = document.getElementById('workspaces');
            if (!data.workspaces || data.workspaces.length === 0) {
                el.innerHTML = '<div class="empty-state">No workspaces found. Click "Add Workspace" to clone a repository.</div>';
                return;
            }
            el.innerHTML = data.workspaces.map(w => `
                <div class="workspace">
                    <div class="workspace-header">
                        <div>
                            <div class="workspace-name">${escapeHtml(w.name)}</div>
                            <div class="workspace-repo">${escapeHtml(w.repo_url)}</div>
                            <div class="workspace-context">Context: ${escapeHtml(w.context_name)}</div>
                        </div>
                        <div class="workspace-actions">
                            <button class="btn btn-sm" onclick="syncWorkspace('${w.id}')">Sync</button>
                            <button class="btn btn-sm" style="color: var(--error)" onclick="deleteWorkspace('${w.id}', '${escapeHtml(w.name)}')">Delete</button>
                        </div>
                    </div>
                    <div class="workspace-path">${escapeHtml(w.local_path)}</div>
                    <div class="workspace-meta">
                        <span>Branch: <strong>${escapeHtml(w.branch)}</strong></span>
                        <span>Status: <span class="status-${w.status}">${w.status}</span></span>
                        ${w.last_synced_at ? `<span>Last synced: ${new Date(w.last_synced_at).toLocaleString()}</span>` : ''}
                        ${w.sync_error ? `<span style="color: var(--error)">Error: ${escapeHtml(w.sync_error)}</span>` : ''}
                    </div>
                </div>
            `).join('');
        }

        function showAddModal() {
            document.getElementById('add-modal').style.display = 'flex';
        }

        function hideAddModal() {
            document.getElementById('add-modal').style.display = 'none';
            document.getElementById('add-form').reset();
        }

        async function addWorkspace(event) {
            event.preventDefault();
            const contextId = document.getElementById('context-select').value;
            const repoUrl = document.getElementById('repo-url').value;
            const name = document.getElementById('workspace-name').value || null;
            const branch = document.getElementById('branch').value || null;

            if (!contextId || !repoUrl) {
                alert('Please select a context and enter a repository URL');
                return;
            }

            try {
                const res = await fetch('/platformadmin/workspaces', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ context_id: contextId, repo_url: repoUrl, name, branch })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Failed to create workspace');
                hideAddModal();
                loadWorkspaces();
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }

        async function syncWorkspace(id) {
            if (!confirm('Sync this workspace? This will pull the latest changes.')) return;
            try {
                const res = await fetch(`/platformadmin/workspaces/${id}/sync`, { method: 'POST' });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Sync failed');
                loadWorkspaces();
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }

        async function deleteWorkspace(id, name) {
            if (!confirm(`Delete workspace "${name}"? This will remove the local files.`)) return;
            try {
                const res = await fetch(`/platformadmin/workspaces/${id}`, { method: 'DELETE' });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Delete failed');
                loadWorkspaces();
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }

        function escapeHtml(str) {
            if (!str) return '';
            const div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        }

        loadWorkspaces();
        loadContexts();
    """

    return render_admin_page(
        title="Workspaces",
        active_page="/platformadmin/workspaces/",
        content=content,
        user_name=admin.display_name or admin.email.split("@")[0],
        user_email=admin.email,
        breadcrumbs=[("Workspaces", "#")],
        extra_css=extra_css,
        extra_js=extra_js,
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
    workspace_dir = workspace_base / str(request.context_id) / dir_name
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
