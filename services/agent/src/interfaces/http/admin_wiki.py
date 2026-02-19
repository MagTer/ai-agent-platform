"""Admin endpoints for Azure DevOps wiki import management."""

# ruff: noqa: E501
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import AsyncSessionLocal, get_db
from core.db.models import Context, WikiImport
from core.wiki.service import WikiImportError, full_import
from interfaces.http.admin_auth import AdminUser, verify_admin_user
from interfaces.http.admin_shared import UTF8HTMLResponse, render_admin_page
from interfaces.http.csrf import require_csrf

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platformadmin/wiki",
    tags=["platform-admin", "wiki"],
)


class WikiImportStatus(BaseModel):
    """Wiki import status for the admin portal."""

    id: UUID
    context_id: UUID
    wiki_identifier: str
    status: str
    total_pages: int
    pages_imported: int
    total_chunks: int
    last_error: str | None
    last_import_started_at: datetime | None
    last_import_completed_at: datetime | None


class TriggerImportRequest(BaseModel):
    """Request to trigger a wiki import."""

    context_id: UUID
    force: bool = False


class TriggerImportResponse(BaseModel):
    """Response after triggering a wiki import."""

    success: bool
    message: str


@router.get("/", dependencies=[Depends(verify_admin_user)])
async def wiki_import_dashboard(
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
) -> UTF8HTMLResponse:
    """Wiki import management dashboard."""
    # Fetch all contexts for the selector
    ctx_stmt = select(Context).order_by(Context.name)
    ctx_result = await session.execute(ctx_stmt)
    contexts = ctx_result.scalars().all()

    context_options = "".join(
        f'<option value="{ctx.id}">{ctx.display_name or ctx.name}</option>' for ctx in contexts
    )

    content = f"""
<div class="page-title">Wiki Import</div>

<div class="card">
    <div class="card-header">
        <h3 class="card-title">Azure DevOps Wiki Import</h3>
    </div>
    <p style="color: var(--text-muted); margin-bottom: 16px; font-size: 13px;">
        Import wiki pages from Azure DevOps into the TIBP wiki search index.
        Requires Azure DevOps credentials configured in the context.
    </p>

    <div style="display: flex; gap: 12px; align-items: flex-end; margin-bottom: 20px;">
        <div style="flex: 1;">
            <label style="display: block; font-size: 12px; font-weight: 600; margin-bottom: 6px; color: var(--text-muted);">Context</label>
            <select id="contextSelect" style="width: 100%; padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; background: var(--bg-card);">
                <option value="">-- Select a context --</option>
                {context_options}
            </select>
        </div>
        <button class="btn btn-primary" onclick="triggerImport(false)" id="importBtn">Import</button>
        <button class="btn" onclick="triggerImport(true)" id="forceBtn" style="border-color: var(--warning); color: var(--warning);">Force Re-import</button>
    </div>
</div>

<div id="statusCard" style="display: none;">
    <div class="card">
        <div class="card-header">
            <h3 class="card-title">Import Status</h3>
            <span id="statusBadge" class="badge badge-muted">idle</span>
        </div>
        <div class="stats-grid">
            <div class="stat-box">
                <div class="stat-value" id="statPages">-</div>
                <div class="stat-label">Pages Imported</div>
            </div>
            <div class="stat-box">
                <div class="stat-value" id="statTotal">-</div>
                <div class="stat-label">Total Pages</div>
            </div>
            <div class="stat-box">
                <div class="stat-value" id="statChunks">-</div>
                <div class="stat-label">Total Chunks</div>
            </div>
        </div>
        <div id="lastImport" style="font-size: 12px; color: var(--text-muted); margin-bottom: 8px;"></div>
        <div id="errorMsg" style="display: none; background: #fee2e2; border: 1px solid #fca5a5; border-radius: 6px; padding: 12px; font-size: 12px; color: #991b1b; margin-top: 8px;"></div>
    </div>
</div>
"""

    extra_js = """
        let pollInterval = null;
        let currentContextId = null;

        document.getElementById('contextSelect').addEventListener('change', function() {
            currentContextId = this.value;
            if (currentContextId) {
                loadStatus(currentContextId);
            } else {
                document.getElementById('statusCard').style.display = 'none';
                stopPolling();
            }
        });

        async function loadStatus(contextId) {
            const resp = await fetch('/platformadmin/wiki/status/' + contextId);
            if (!resp || !resp.ok) return;
            const data = await resp.json();
            renderStatus(data);
        }

        function renderStatus(data) {
            const card = document.getElementById('statusCard');
            if (!data || data.length === 0) {
                card.style.display = 'none';
                return;
            }
            card.style.display = 'block';
            const item = data[0];
            const badge = document.getElementById('statusBadge');
            const statusMap = {
                'idle': ['badge-muted', 'Idle'],
                'fetching': ['badge-info', 'Fetching...'],
                'embedding': ['badge-warning', 'Embedding...'],
                'completed': ['badge-success', 'Completed'],
                'error': ['badge-error', 'Error'],
            };
            const [cls, label] = statusMap[item.status] || ['badge-muted', item.status];
            badge.className = 'badge ' + cls;
            badge.textContent = label;

            document.getElementById('statPages').textContent = item.pages_imported;
            document.getElementById('statTotal').textContent = item.total_pages;
            document.getElementById('statChunks').textContent = item.total_chunks;

            const lastImportEl = document.getElementById('lastImport');
            if (item.last_import_completed_at) {
                lastImportEl.textContent = 'Last completed: ' + new Date(item.last_import_completed_at + 'Z').toLocaleString();
            } else if (item.last_import_started_at) {
                lastImportEl.textContent = 'Started: ' + new Date(item.last_import_started_at + 'Z').toLocaleString();
            } else {
                lastImportEl.textContent = '';
            }

            const errEl = document.getElementById('errorMsg');
            if (item.last_error) {
                errEl.style.display = 'block';
                errEl.textContent = 'Error: ' + escapeHtml(item.last_error);
            } else {
                errEl.style.display = 'none';
            }

            // Auto-poll while running
            if (item.status === 'fetching' || item.status === 'embedding') {
                startPolling(currentContextId);
            } else {
                stopPolling();
            }
        }

        function startPolling(contextId) {
            if (pollInterval) return;
            pollInterval = setInterval(() => loadStatus(contextId), 5000);
        }

        function stopPolling() {
            if (pollInterval) {
                clearInterval(pollInterval);
                pollInterval = null;
            }
        }

        async function triggerImport(force) {
            const contextId = document.getElementById('contextSelect').value;
            if (!contextId) {
                showToast('Please select a context first.', 'warning');
                return;
            }

            const importBtn = document.getElementById('importBtn');
            const forceBtn = document.getElementById('forceBtn');
            importBtn.disabled = true;
            forceBtn.disabled = true;

            try {
                const resp = await fetchWithErrorHandling('/platformadmin/wiki/import', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({context_id: contextId, force: force}),
                });
                if (resp) {
                    showToast(force ? 'Force re-import started.' : 'Import started.', 'success');
                    loadStatus(contextId);
                }
            } finally {
                importBtn.disabled = false;
                forceBtn.disabled = false;
            }
        }
    """

    return UTF8HTMLResponse(
        render_admin_page(
            title="Wiki Import",
            active_page="/platformadmin/wiki/",
            content=content,
            user_name=admin.display_name or admin.email,
            user_email=admin.email,
            breadcrumbs=[("Wiki Import", "/platformadmin/wiki/")],
            extra_js=extra_js,
        )
    )


@router.get(
    "/status/{context_id}",
    response_model=list[WikiImportStatus],
    dependencies=[Depends(verify_admin_user)],
)
async def get_wiki_status(
    context_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> list[WikiImportStatus]:
    """Get current wiki import status for a context."""
    stmt = (
        select(WikiImport)
        .where(WikiImport.context_id == context_id)
        .order_by(WikiImport.wiki_identifier)
    )
    result = await session.execute(stmt)
    records = result.scalars().all()

    return [
        WikiImportStatus(
            id=r.id,
            context_id=r.context_id,
            wiki_identifier=r.wiki_identifier,
            status=r.status,
            total_pages=r.total_pages,
            pages_imported=r.pages_imported,
            total_chunks=r.total_chunks,
            last_error=r.last_error,
            last_import_started_at=r.last_import_started_at,
            last_import_completed_at=r.last_import_completed_at,
        )
        for r in records
    ]


@router.post(
    "/import",
    response_model=TriggerImportResponse,
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def trigger_wiki_import(
    request: TriggerImportRequest,
    session: AsyncSession = Depends(get_db),
) -> TriggerImportResponse:
    """Start a wiki import as a background task.

    Returns immediately. Poll /status/{context_id} for progress.
    """
    # Verify context exists
    ctx_stmt = select(Context).where(Context.id == request.context_id)
    ctx_result = await session.execute(ctx_stmt)
    context = ctx_result.scalar_one_or_none()
    if not context:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Context {request.context_id} not found",
        )

    context_id = request.context_id
    force = request.force

    async def _run_import_bg() -> None:
        """Run import in background with a fresh DB session."""
        try:
            async with AsyncSessionLocal() as bg_session:
                await full_import(context_id, bg_session, force=force)
        except WikiImportError as e:
            LOGGER.warning("Background wiki import failed for context %s: %s", context_id, e)
        except Exception:
            LOGGER.exception(
                "Unexpected error in background wiki import for context %s", context_id
            )

    asyncio.create_task(_run_import_bg())

    action = "Force re-import" if force else "Import"
    return TriggerImportResponse(
        success=True,
        message=f"{action} started for context {context_id}. Poll /status/{context_id} for progress.",
    )
