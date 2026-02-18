"""Admin endpoints for scheduled job management."""

# ruff: noqa: E501
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.db.models import Context, ScheduledJob
from interfaces.http.admin_auth import AdminUser, require_admin_or_redirect, verify_admin_user
from interfaces.http.admin_shared import UTF8HTMLResponse, render_admin_page
from interfaces.http.csrf import require_csrf

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platformadmin/scheduler",
    tags=["platform-admin", "scheduler"],
)


# --- Pydantic Models ---


class CreateJobRequest(BaseModel):
    """Request to create a scheduled job."""

    name: str = Field(..., min_length=1, max_length=255, description="Job name")
    description: str | None = Field(default=None, max_length=1000, description="Job description")
    cron_expression: str = Field(..., description="5-field cron expression")
    skill_prompt: str = Field(..., min_length=1, max_length=5000, description="Prompt to send")
    notification_channel: str | None = Field(default=None, description="Notification channel")
    notification_target: str | None = Field(default=None, description="Notification target")
    timeout_seconds: int = Field(default=300, ge=30, le=3600, description="Timeout in seconds")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate job name format."""
        import re

        v = v.strip()
        if not v:
            raise ValueError("Job name cannot be empty")
        if not re.match(r"^[a-zA-Z0-9_\- ]+$", v):
            raise ValueError(
                "Job name can only contain letters, numbers, spaces, hyphens, and underscores"
            )
        return v

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v: str) -> str:
        """Validate cron expression is parseable."""
        v = v.strip()
        if not croniter.is_valid(v):
            raise ValueError(f"Invalid cron expression: {v}")
        return v

    @field_validator("notification_channel")
    @classmethod
    def validate_notification_channel(cls, v: str | None) -> str | None:
        """Validate notification channel."""
        if v is None or v == "":
            return None
        valid_channels = {"telegram", "email"}
        if v not in valid_channels:
            raise ValueError(
                f"Invalid notification channel. Must be one of: {', '.join(valid_channels)}"
            )
        return v


class UpdateJobRequest(BaseModel):
    """Request to update a scheduled job."""

    name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    cron_expression: str | None = Field(default=None)
    skill_prompt: str | None = Field(default=None, max_length=5000)
    is_enabled: bool | None = Field(default=None)
    notification_channel: str | None = Field(default=None)
    notification_target: str | None = Field(default=None)
    timeout_seconds: int | None = Field(default=None, ge=30, le=3600)

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v: str | None) -> str | None:
        """Validate cron expression if provided."""
        if v is None:
            return None
        v = v.strip()
        if not croniter.is_valid(v):
            raise ValueError(f"Invalid cron expression: {v}")
        return v


class JobResponse(BaseModel):
    """Scheduled job response."""

    id: UUID
    context_id: UUID
    name: str
    description: str | None
    cron_expression: str
    skill_prompt: str
    is_enabled: bool
    status: str
    notification_channel: str | None
    notification_target: str | None
    last_run_at: str | None
    last_run_status: str | None
    last_run_result: str | None
    last_run_duration_ms: int | None
    next_run_at: str | None
    run_count: int
    error_count: int
    timeout_seconds: int
    created_at: str
    updated_at: str


class JobListResponse(BaseModel):
    """List of scheduled jobs."""

    jobs: list[JobResponse]
    total: int


def _job_to_response(job: ScheduledJob) -> JobResponse:
    """Convert a ScheduledJob model to a response dict."""
    return JobResponse(
        id=job.id,
        context_id=job.context_id,
        name=job.name,
        description=job.description,
        cron_expression=job.cron_expression,
        skill_prompt=job.skill_prompt,
        is_enabled=job.is_enabled,
        status=job.status,
        notification_channel=job.notification_channel,
        notification_target=job.notification_target,
        last_run_at=job.last_run_at.isoformat() if job.last_run_at else None,
        last_run_status=job.last_run_status,
        last_run_result=job.last_run_result,
        last_run_duration_ms=job.last_run_duration_ms,
        next_run_at=job.next_run_at.isoformat() if job.next_run_at else None,
        run_count=job.run_count,
        error_count=job.error_count,
        timeout_seconds=job.timeout_seconds,
        created_at=job.created_at.isoformat(),
        updated_at=job.updated_at.isoformat(),
    )


# --- Context-scoped endpoints (used by context detail tab) ---


@router.get(
    "/context/{context_id}/jobs",
    response_model=JobListResponse,
    dependencies=[Depends(verify_admin_user)],
)
async def list_context_jobs(
    context_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> JobListResponse:
    """List all scheduled jobs for a context."""
    stmt = (
        select(ScheduledJob)
        .where(ScheduledJob.context_id == context_id)
        .order_by(ScheduledJob.name)
    )
    result = await session.execute(stmt)
    jobs = result.scalars().all()

    return JobListResponse(
        jobs=[_job_to_response(j) for j in jobs],
        total=len(jobs),
    )


@router.post(
    "/context/{context_id}/jobs",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def create_job(
    context_id: UUID,
    request: CreateJobRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a new scheduled job for a context."""
    # Verify context exists
    ctx = await session.get(Context, context_id)
    if not ctx:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Context not found")

    # Check for duplicate name
    existing_stmt = select(ScheduledJob).where(
        ScheduledJob.context_id == context_id,
        ScheduledJob.name == request.name,
    )
    existing = await session.execute(existing_stmt)
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job with name '{request.name}' already exists in this context",
        )

    # Compute initial next_run_at
    from interfaces.scheduler.adapter import SchedulerAdapter

    next_run = SchedulerAdapter._compute_next_run(request.cron_expression)

    job = ScheduledJob(
        context_id=context_id,
        name=request.name,
        description=request.description,
        cron_expression=request.cron_expression,
        skill_prompt=request.skill_prompt,
        notification_channel=request.notification_channel,
        notification_target=request.notification_target,
        timeout_seconds=request.timeout_seconds,
        next_run_at=next_run,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    LOGGER.info(
        "Created scheduled job %s (context: %s, cron: %s)",
        job.name,
        context_id,
        job.cron_expression,
    )

    return {"success": True, "job_id": str(job.id), "next_run_at": next_run.isoformat()}


@router.put(
    "/jobs/{job_id}",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def update_job(
    job_id: UUID,
    request: UpdateJobRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Update a scheduled job."""
    job = await session.get(ScheduledJob, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    update_fields = request.model_dump(exclude_none=True)
    for field_name, value in update_fields.items():
        setattr(job, field_name, value)

    # Recompute next_run_at if cron changed
    if request.cron_expression:
        from interfaces.scheduler.adapter import SchedulerAdapter

        job.next_run_at = SchedulerAdapter._compute_next_run(request.cron_expression)

    # If re-enabled, recompute next_run_at
    if request.is_enabled is True and job.status in ("paused", "error"):
        from interfaces.scheduler.adapter import SchedulerAdapter

        job.status = "active"
        job.next_run_at = SchedulerAdapter._compute_next_run(job.cron_expression)

    await session.commit()
    await session.refresh(job)

    return {"success": True, "job": _job_to_response(job).model_dump()}


@router.delete(
    "/jobs/{job_id}",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def delete_job(
    job_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Delete a scheduled job."""
    job = await session.get(ScheduledJob, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    job_name = job.name
    await session.delete(job)
    await session.commit()

    LOGGER.info("Deleted scheduled job %s", job_name)
    return {"success": True, "message": f"Deleted job '{job_name}'"}


@router.post(
    "/jobs/{job_id}/toggle",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def toggle_job(
    job_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Toggle a job's enabled state."""
    job = await session.get(ScheduledJob, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    job.is_enabled = not job.is_enabled
    if job.is_enabled:
        from interfaces.scheduler.adapter import SchedulerAdapter

        job.status = "active"
        job.next_run_at = SchedulerAdapter._compute_next_run(job.cron_expression)
    else:
        job.status = "paused"
        job.next_run_at = None

    await session.commit()

    return {
        "success": True,
        "is_enabled": job.is_enabled,
        "status": job.status,
        "next_run_at": job.next_run_at.isoformat() if job.next_run_at else None,
    }


@router.post(
    "/jobs/{job_id}/run-now",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def run_job_now(
    job_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Trigger immediate execution of a job (sets next_run_at to now)."""
    job = await session.get(ScheduledJob, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    if not job.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot run a disabled job. Enable it first.",
        )

    # Set next_run_at to now so the scheduler picks it up
    now = datetime.now(UTC).replace(tzinfo=None)
    job.next_run_at = now
    job.status = "active"
    await session.commit()

    return {"success": True, "message": f"Job '{job.name}' scheduled for immediate execution"}


# --- Global overview page ---


@router.get("/", response_class=UTF8HTMLResponse)
async def scheduler_dashboard(admin: AdminUser = Depends(require_admin_or_redirect)) -> str:
    """Scheduler overview dashboard showing all jobs across all contexts."""
    content = """
    <h1 class="page-title">Scheduled Jobs</h1>

    <div class="stats-grid">
        <div class="stat-box">
            <div class="stat-value" id="totalJobs">0</div>
            <div class="stat-label">Total Jobs</div>
        </div>
        <div class="stat-box">
            <div class="stat-value" id="activeJobs">0</div>
            <div class="stat-label">Active</div>
        </div>
        <div class="stat-box">
            <div class="stat-value" id="errorJobs">0</div>
            <div class="stat-label">Errors</div>
        </div>
    </div>

    <div class="card">
        <div class="card-header">
            <span>All Scheduled Jobs <span id="count" class="badge badge-info">0</span></span>
            <div style="display: flex; gap: 8px;">
                <button class="btn btn-primary" onclick="showCreateJobModal()">+ Create Job</button>
                <button class="btn" onclick="loadAllJobs()">Refresh</button>
            </div>
        </div>
        <div id="jobsList">
            <div class="loading">Loading...</div>
        </div>
    </div>

    <!-- Create Job Modal -->
    <div id="createJobModal" class="modal" style="display: none;">
        <div class="modal-content">
            <h3>Create Scheduled Job</h3>
            <form id="createJobForm" onsubmit="submitNewJob(event)">
                <div class="form-group">
                    <label>Context *</label>
                    <select id="jobContext" required>
                        <option value="">Loading contexts...</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Name *</label>
                    <input type="text" id="jobName" required placeholder="e.g., daily-report" pattern="^[a-zA-Z0-9_\\- ]+$">
                </div>
                <div class="form-group">
                    <label>Prompt *</label>
                    <textarea id="jobPrompt" required placeholder="What should the agent do?" style="min-height: 80px;"></textarea>
                </div>
                <div class="form-group">
                    <label>Cron Expression *</label>
                    <select id="cronPresets" onchange="applyCronPreset()" style="margin-bottom: 4px;">
                        <option value="">-- Pick a common schedule --</option>
                        <option value="0 */4 * * *">Every 4 hours</option>
                        <option value="0 */6 * * *">Every 6 hours</option>
                        <option value="0 9 * * *">Daily at 9am UTC</option>
                        <option value="0 9,17 * * *">Twice daily (9am, 5pm UTC)</option>
                        <option value="0 8 * * 1-5">Weekdays at 8am UTC</option>
                    </select>
                    <input type="text" id="jobCron" required placeholder="0 8 * * 1-5 (weekdays at 08:00)">
                    <small style="color: var(--text-muted); font-size: 12px;">Format: minute hour day month weekday. Example: 0 9 * * * = daily at 09:00 UTC</small>
                </div>
                <div class="form-group">
                    <label>Description</label>
                    <input type="text" id="jobDescription" placeholder="Optional description">
                </div>
                <div class="form-group">
                    <label>Notification Channel</label>
                    <select id="jobNotifChannel" onchange="toggleNotifTarget()">
                        <option value="">None</option>
                        <option value="email">Email</option>
                        <option value="telegram">Telegram</option>
                    </select>
                </div>
                <div class="form-group" id="notifTargetGroup" style="display:none;">
                    <label>Notification Target</label>
                    <input type="text" id="jobNotifTarget" placeholder="Email address or Telegram chat ID">
                </div>
                <div class="form-group">
                    <label>Timeout (seconds)</label>
                    <input type="number" id="jobTimeout" value="300" min="30" max="3600">
                </div>
                <div class="modal-actions">
                    <button type="button" class="btn" onclick="hideCreateJobModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Create Job</button>
                </div>
            </form>
        </div>
    </div>
"""

    extra_css = """
    .job-row { padding: 12px 0; border-bottom: 1px solid var(--border); }
    .job-row:last-child { border-bottom: none; }
    .job-name { font-weight: 600; font-size: 14px; }
    .job-meta { font-size: 12px; color: var(--text-muted); display: flex; gap: 12px; margin-top: 4px; flex-wrap: wrap; }
    .job-actions { display: flex; gap: 4px; }
    .status-active { color: var(--success); }
    .status-error { color: var(--error); }
    .status-paused { color: var(--text-muted); }
    .status-running { color: var(--warning); }
    .modal { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 1000; }
    .modal-content { background: var(--card-bg, #fff); border-radius: 8px; padding: 24px; width: 90%; max-width: 500px; max-height: 90vh; overflow-y: auto; }
    .form-group { margin-bottom: 12px; }
    .form-group label { display: block; font-weight: 600; margin-bottom: 4px; font-size: 13px; }
    .form-group input, .form-group select, .form-group textarea { width: 100%; padding: 8px; border: 1px solid var(--border); border-radius: 4px; font-size: 14px; background: var(--bg, #fff); color: var(--text, #333); box-sizing: border-box; }
    .modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
"""

    extra_js = """
    async function loadAllJobs() {
        const res = await fetchWithErrorHandling('/platformadmin/scheduler/all-jobs');
        if (!res) { document.getElementById('jobsList').innerHTML = '<div style="color: var(--error)">Failed to load</div>'; return; }
        const data = await res.json();
        const jobs = data.jobs || [];

        document.getElementById('count').textContent = data.total;
        document.getElementById('totalJobs').textContent = data.total;
        document.getElementById('activeJobs').textContent = jobs.filter(j => j.status === 'active').length;
        document.getElementById('errorJobs').textContent = jobs.filter(j => j.status === 'error').length;

        const el = document.getElementById('jobsList');
        if (jobs.length === 0) {
            el.innerHTML = '<div class="empty-state">No scheduled jobs yet. Click "+ Create Job" to add one.</div>';
            return;
        }
        el.innerHTML = jobs.map(j => {
            const statusClass = 'status-' + j.status;
            const nextRun = j.next_run_at ? new Date(j.next_run_at).toLocaleString() : 'N/A';
            const lastRun = j.last_run_at ? new Date(j.last_run_at).toLocaleString() : 'Never';
            const toggleLabel = j.is_enabled ? 'Pause' : 'Enable';
            return '<div class="job-row">' +
                '<div style="display:flex;justify-content:space-between;align-items:center;">' +
                '<span class="job-name">' + escapeHtml(j.name) + '</span>' +
                '<div class="job-actions">' +
                '<button class="btn btn-sm" onclick="toggleJob(\\'' + j.id + '\\')">' + toggleLabel + '</button>' +
                '<button class="btn btn-sm btn-primary" onclick="runJobNow(\\'' + j.id + '\\')" ' + (j.is_enabled ? '' : 'disabled') + '>Run Now</button>' +
                '<button class="btn btn-sm btn-danger" onclick="deleteJob(\\'' + j.id + '\\', \\'' + escapeHtml(j.name).replace(/'/g, "\\\\'") + '\\')">Delete</button>' +
                '<span class="badge ' + statusClass + '" style="margin-left:4px;">' + j.status + '</span>' +
                '</div></div>' +
                '<div class="job-meta">' +
                '<span>Cron: <code>' + escapeHtml(j.cron_expression) + '</code></span>' +
                '<span>Next: ' + nextRun + '</span>' +
                '<span>Last: ' + lastRun + '</span>' +
                '<span>Runs: ' + j.run_count + '</span>' +
                (j.error_count > 0 ? '<span style="color:var(--error);">Errors: ' + j.error_count + '</span>' : '') +
                '<span>Context: <a href="/platformadmin/contexts/' + j.context_id + '/#scheduler">' + j.context_id.substring(0, 8) + '...</a></span>' +
                '</div></div>';
        }).join('');
    }

    async function toggleJob(jobId) {
        const res = await fetchWithErrorHandling('/platformadmin/scheduler/jobs/' + jobId + '/toggle', { method: 'POST' });
        if (res) { showToast('Job toggled', 'success'); loadAllJobs(); }
    }

    async function runJobNow(jobId) {
        const res = await fetchWithErrorHandling('/platformadmin/scheduler/jobs/' + jobId + '/run-now', { method: 'POST' });
        if (res) { showToast('Job triggered for immediate execution', 'success'); loadAllJobs(); }
    }

    async function deleteJob(jobId, jobName) {
        if (!confirm('Delete job "' + jobName + '"? This cannot be undone.')) return;
        const res = await fetchWithErrorHandling('/platformadmin/scheduler/jobs/' + jobId, { method: 'DELETE' });
        if (res) { showToast('Job deleted', 'success'); loadAllJobs(); }
    }

    // --- Create Job Modal ---
    let contextsLoaded = false;
    async function loadContextsForSelect() {
        if (contextsLoaded) return;
        const res = await fetchWithErrorHandling('/platformadmin/contexts');
        if (!res) return;
        const data = await res.json();
        const sel = document.getElementById('jobContext');
        sel.innerHTML = '<option value="">-- Select context --</option>';
        (data.contexts || []).forEach(c => {
            const label = c.name ? escapeHtml(c.name) + ' (' + c.id.substring(0, 8) + '...)' : c.id;
            sel.innerHTML += '<option value="' + c.id + '">' + label + '</option>';
        });
        contextsLoaded = true;
    }

    function showCreateJobModal() {
        loadContextsForSelect();
        document.getElementById('createJobModal').style.display = 'flex';
    }

    function hideCreateJobModal() {
        document.getElementById('createJobModal').style.display = 'none';
        document.getElementById('createJobForm').reset();
        document.getElementById('cronPresets').value = '';
        document.getElementById('notifTargetGroup').style.display = 'none';
    }

    function applyCronPreset() {
        const preset = document.getElementById('cronPresets').value;
        if (preset) document.getElementById('jobCron').value = preset;
    }

    function toggleNotifTarget() {
        const ch = document.getElementById('jobNotifChannel').value;
        document.getElementById('notifTargetGroup').style.display = ch ? 'block' : 'none';
    }

    async function submitNewJob(e) {
        e.preventDefault();
        const contextId = document.getElementById('jobContext').value;
        if (!contextId) { showToast('Please select a context', 'error'); return; }
        const body = {
            name: document.getElementById('jobName').value,
            skill_prompt: document.getElementById('jobPrompt').value,
            cron_expression: document.getElementById('jobCron').value,
            description: document.getElementById('jobDescription').value || null,
            notification_channel: document.getElementById('jobNotifChannel').value || null,
            notification_target: document.getElementById('jobNotifTarget').value || null,
            timeout_seconds: parseInt(document.getElementById('jobTimeout').value) || 300
        };
        const res = await fetchWithErrorHandling('/platformadmin/scheduler/context/' + contextId + '/jobs', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
        });
        if (res) {
            showToast('Job created', 'success');
            hideCreateJobModal();
            loadAllJobs();
        }
    }

    loadAllJobs();
"""

    return render_admin_page(
        title="Scheduled Jobs",
        active_page="/platformadmin/scheduler/",
        content=content,
        user_name=admin.display_name or admin.email.split("@")[0],
        user_email=admin.email,
        breadcrumbs=[("Scheduler", "#")],
        extra_css=extra_css,
        extra_js=extra_js,
    )


@router.get(
    "/all-jobs",
    response_model=JobListResponse,
    dependencies=[Depends(verify_admin_user)],
)
async def list_all_jobs(
    offset: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(50, ge=1, le=500, description="Max items to return"),
    session: AsyncSession = Depends(get_db),
) -> JobListResponse:
    """List all scheduled jobs across all contexts."""
    stmt = select(ScheduledJob).order_by(ScheduledJob.next_run_at.asc())

    # Apply pagination
    stmt = stmt.offset(offset).limit(limit)

    result = await session.execute(stmt)
    jobs = result.scalars().all()

    return JobListResponse(
        jobs=[_job_to_response(j) for j in jobs],
        total=len(jobs),
    )


__all__ = ["router"]
