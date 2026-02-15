# Scheduler Service - Implementation Plan

**Created:** 2026-02-13
**Author:** Architect (Opus)
**Status:** Ready for implementation

---

## 1. Feature Overview

Add a general-purpose Scheduler Service to the AI Agent Platform that enables cron-based scheduling of any skill. Jobs are defined per-context, stored in the database, and executed as standard AgentService requests. Results go into conversation history and optionally trigger notifications via Telegram or Email.

**Key design decisions:**
- The scheduler is a new **interface adapter** (peer to Telegram/OpenWebUI), living in `interfaces/scheduler/`
- It follows the `PlatformAdapter` pattern: timer fires -> resolve context -> create `AgentRequest` -> call `AgentService.handle_request()`
- Uses `croniter` library for cron expression parsing (lightweight, stdlib-compatible, no APScheduler needed)
- Job definitions stored in `scheduled_jobs` DB table, scoped to `context_id`
- Admin UI: new "Scheduler" tab on the context detail page + optional dedicated overview page in navigation
- In-process asyncio loop (no external scheduler daemon)

**Why not APScheduler?**
The existing schedulers (PriceCheckScheduler, HomeyDeviceSyncScheduler) already use plain `asyncio.create_task()` with sleep loops. APScheduler adds a heavyweight dependency when `croniter` (cron parsing) + asyncio (scheduling) covers the requirement. This keeps the pattern consistent.

---

## 2. Architecture Decisions

### Layer Placement

```
interfaces/scheduler/adapter.py    -- SchedulerAdapter (PlatformAdapter subclass)
core/db/models.py                  -- ScheduledJob model
interfaces/http/admin_scheduler.py -- Admin API endpoints for CRUD
interfaces/http/templates/admin_scheduler_tab.html -- Context detail tab HTML
```

**Why `interfaces/`?**
The scheduler adapter _receives triggers_ (timer events) and _routes them_ to AgentService, just like the Telegram adapter receives messages and routes them. It needs `ServiceFactory`, `ContextService`, and `AsyncSessionLocal` -- all interface-layer concerns.

### Dependency Flow

```
interfaces/scheduler/adapter.py
    -> core/runtime/service_factory.py (creates context-scoped AgentService)
    -> core/runtime/service.py (AgentService.handle_request)
    -> core/db/models.py (ScheduledJob model)
    -> core/providers.py (get_email_service_optional for notifications)
```

No cross-module imports. No modules/ imports. Architecture-compliant.

### Notification Strategy

Post-execution hook inside the adapter, not a separate tool call:
1. Job executes via AgentService.handle_request()
2. Adapter inspects response
3. If `notification_channel` is configured on the job, send notification
4. Telegram: via `aiogram.Bot.send_message(chat_id, summary)`
5. Email: via `IEmailService.send(EmailMessage(...))`

---

## 3. Implementation Roadmap

### Phase 1: Database Model + Migration (Step 1)

**Engineer tasks:**

1. Add `croniter` dependency to `pyproject.toml`
2. Add `ScheduledJob` model to `core/db/models.py`
3. Create Alembic migration

**File: `services/agent/pyproject.toml`** -- Add dependency:

```toml
# Add to [tool.poetry.dependencies] section, after "orjson":
croniter = "^3.0"
```

**File: `services/agent/src/core/db/models.py`** -- Add after the `McpServer` class (before `SystemConfig`):

```python
class ScheduledJob(Base):
    """Cron-scheduled job definition scoped to a context.

    Each job triggers a skill execution via AgentService at the
    configured cron schedule. Results are stored in conversation
    history and optionally sent via notification channel.

    Attributes:
        id: Unique job identifier.
        context_id: Parent context for multi-tenant isolation.
        name: Human-readable job name (unique per context).
        description: Optional description of what the job does.
        cron_expression: Standard 5-field cron expression (minute hour day month weekday).
        skill_prompt: The prompt to send to AgentService (e.g., "Check server status").
        is_enabled: Whether the job is active.
        status: Current status (active, paused, error).
        notification_channel: Optional notification channel (telegram, email, none).
        notification_target: Channel-specific target (chat_id for telegram, email address for email).
        last_run_at: Timestamp of last execution.
        last_run_status: Status of last execution (success, error).
        last_run_result: Summary of last execution result.
        last_run_duration_ms: Duration of last execution in milliseconds.
        next_run_at: Computed next execution time.
        run_count: Total number of executions.
        error_count: Total number of failed executions.
        max_retries: Number of retries on failure (default 0).
        timeout_seconds: Maximum execution time before timeout (default 300).
        created_at: Job creation timestamp.
        updated_at: Last update timestamp.
    """

    __tablename__ = "scheduled_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    context_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contexts.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String, index=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    cron_expression: Mapped[str] = mapped_column(String)
    skill_prompt: Mapped[str] = mapped_column(String)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # active, paused, error, running
    status: Mapped[str] = mapped_column(String, default="active")
    notification_channel: Mapped[str | None] = mapped_column(String, nullable=True)  # telegram, email, none
    notification_target: Mapped[str | None] = mapped_column(String, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_status: Mapped[str | None] = mapped_column(String, nullable=True)  # success, error
    last_run_result: Mapped[str | None] = mapped_column(String, nullable=True)
    last_run_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=0)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    # Relationships
    context = relationship("Context")

    __table_args__ = (
        UniqueConstraint("context_id", "name", name="uq_context_scheduled_job_name"),
    )
```

Also add to the `Context` model's relationships (optional, for cascade delete):
```python
# Add after existing relationships in Context class:
scheduled_jobs = relationship("ScheduledJob", cascade="all, delete-orphan")
```

**File: `services/agent/alembic/versions/20260213_add_scheduled_jobs.py`** -- New migration:

```python
"""Add scheduled_jobs table.

Revision ID: 20260213_sched_jobs
Revises: 20260212_ctx_shared
Create Date: 2026-02-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260213_sched_jobs"
down_revision: str | Sequence[str] | None = "20260212_ctx_shared"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create scheduled_jobs table."""
    op.create_table(
        "scheduled_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("context_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contexts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(), nullable=False, index=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("cron_expression", sa.String(), nullable=False),
        sa.Column("skill_prompt", sa.String(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("notification_channel", sa.String(), nullable=True),
        sa.Column("notification_target", sa.String(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_status", sa.String(), nullable=True),
        sa.Column("last_run_result", sa.String(), nullable=True),
        sa.Column("last_run_duration_ms", sa.Integer(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(), nullable=True, index=True),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default=sa.text("300")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("context_id", "name", name="uq_context_scheduled_job_name"),
    )


def downgrade() -> None:
    """Drop scheduled_jobs table."""
    op.drop_table("scheduled_jobs")
```

**Ops tasks:**
- Run `poetry add croniter@^3.0` from `services/agent/`
- Run `./stack check` to verify model compiles
- Do NOT run migration yet (will be done during deploy)

**Files affected:**
- `services/agent/pyproject.toml` (modify)
- `services/agent/src/core/db/models.py` (modify)
- `services/agent/alembic/versions/20260213_add_scheduled_jobs.py` (create)

---

### Phase 2: Scheduler Adapter (Step 2)

**Engineer tasks:**

Create the core scheduler adapter that runs as an asyncio background loop, checking for due jobs and executing them.

**File: `services/agent/src/interfaces/scheduler/__init__.py`** (create):

```python
"""Scheduler interface adapter for cron-based job execution."""

from interfaces.scheduler.adapter import SchedulerAdapter

__all__ = ["SchedulerAdapter"]
```

**File: `services/agent/src/interfaces/scheduler/adapter.py`** (create):

```python
"""Scheduler adapter for cron-based job execution.

This adapter runs as an in-process asyncio background loop that:
1. Periodically checks for due scheduled jobs
2. Creates AgentRequests for each due job
3. Executes them via AgentService
4. Stores results and sends optional notifications
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from croniter import croniter
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.db.models import ScheduledJob
from core.protocols.email import EmailMessage
from core.providers import get_email_service_optional
from core.runtime.service_factory import ServiceFactory
from shared.models import AgentRequest

LOGGER = logging.getLogger(__name__)


class SchedulerAdapter:
    """In-process cron scheduler that executes jobs via AgentService.

    This is a PlatformAdapter-style class (but does not subclass PlatformAdapter
    because it has no send_message semantics -- it is fire-and-forget).

    Architecture:
        - Lives in interfaces/ layer (can import core/ and orchestrator/)
        - Uses ServiceFactory to create context-scoped AgentService per job
        - Stores results in conversation history via AgentService.handle_request()
        - Sends notifications post-execution via email or telegram
    """

    platform_name = "scheduler"

    # How often to check for due jobs (seconds)
    CHECK_INTERVAL_SECONDS = 60

    # Maximum concurrent job executions
    MAX_CONCURRENT_JOBS = 3

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        service_factory: ServiceFactory,
        telegram_bot_token: str | None = None,
    ) -> None:
        """Initialize the scheduler adapter.

        Args:
            session_factory: Factory for creating database sessions.
            service_factory: Factory for creating context-scoped AgentService instances.
            telegram_bot_token: Optional Telegram bot token for notifications.
        """
        self._session_factory = session_factory
        self._service_factory = service_factory
        self._telegram_bot_token = telegram_bot_token
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_JOBS)
        self._running_jobs: set[uuid.UUID] = set()

    async def start(self) -> None:
        """Start the scheduler background loop."""
        if self._running:
            LOGGER.warning("Scheduler already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        LOGGER.info("Scheduler adapter started (check interval: %ds)", self.CHECK_INTERVAL_SECONDS)

    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        LOGGER.info("Scheduler adapter stopped")

    async def _run_loop(self) -> None:
        """Main scheduler loop -- check for due jobs every CHECK_INTERVAL_SECONDS."""
        # Initial delay to let the application fully start
        await asyncio.sleep(10)

        while self._running:
            try:
                await self._check_due_jobs()
            except asyncio.CancelledError:
                break
            except Exception as e:
                LOGGER.error("Scheduler loop error: %s", e, exc_info=True)

            await asyncio.sleep(self.CHECK_INTERVAL_SECONDS)

    async def _check_due_jobs(self) -> None:
        """Find and execute all due jobs."""
        now = datetime.now(UTC).replace(tzinfo=None)

        async with self._session_factory() as session:
            stmt = (
                select(ScheduledJob)
                .where(
                    ScheduledJob.is_enabled.is_(True),
                    ScheduledJob.status.in_(["active", "error"]),
                    ScheduledJob.next_run_at <= now,
                )
                .order_by(ScheduledJob.next_run_at.asc())
                .limit(10)
            )
            result = await session.execute(stmt)
            due_jobs = result.scalars().all()

            if not due_jobs:
                return

            LOGGER.info("Found %d due scheduled jobs", len(due_jobs))

            for job in due_jobs:
                # Skip if this job is already running
                if job.id in self._running_jobs:
                    LOGGER.debug("Job %s already running, skipping", job.name)
                    continue

                # Fire-and-forget with concurrency limit
                asyncio.create_task(self._execute_job_with_semaphore(job.id))

    async def _execute_job_with_semaphore(self, job_id: uuid.UUID) -> None:
        """Execute a job with semaphore-based concurrency control."""
        async with self._semaphore:
            await self._execute_job(job_id)

    async def _execute_job(self, job_id: uuid.UUID) -> None:
        """Execute a single scheduled job.

        Flow:
        1. Mark job as running
        2. Create context-scoped AgentService
        3. Build AgentRequest with skill_prompt
        4. Call handle_request()
        5. Record results
        6. Send notification if configured
        7. Compute next_run_at
        """
        self._running_jobs.add(job_id)
        start_time = time.monotonic()

        try:
            async with self._session_factory() as session:
                # Re-fetch job within this session
                job = await session.get(ScheduledJob, job_id)
                if not job or not job.is_enabled:
                    return

                # Mark as running
                job.status = "running"
                await session.commit()

                LOGGER.info(
                    "Executing scheduled job: %s (context: %s, cron: %s)",
                    job.name,
                    job.context_id,
                    job.cron_expression,
                )

                try:
                    # Create context-scoped AgentService
                    agent_service = await self._service_factory.create_service(
                        job.context_id, session
                    )

                    # Build a dedicated conversation for this job execution
                    conversation_id = str(uuid.uuid4())

                    request = AgentRequest(
                        prompt=job.skill_prompt,
                        conversation_id=conversation_id,
                        metadata={
                            "context_id": str(job.context_id),
                            "platform": "scheduler",
                            "platform_id": f"job-{job.id}",
                            "scheduled_job_id": str(job.id),
                            "scheduled_job_name": job.name,
                        },
                    )

                    # Execute with timeout
                    response = await asyncio.wait_for(
                        agent_service.handle_request(request, session=session),
                        timeout=float(job.timeout_seconds),
                    )

                    # Record success
                    duration_ms = int((time.monotonic() - start_time) * 1000)
                    result_summary = (response.response or "")[:2000]

                    job.last_run_at = datetime.now(UTC).replace(tzinfo=None)
                    job.last_run_status = "success"
                    job.last_run_result = result_summary
                    job.last_run_duration_ms = duration_ms
                    job.run_count += 1
                    job.status = "active"

                    # Compute next run time
                    job.next_run_at = self._compute_next_run(job.cron_expression)

                    await session.commit()

                    LOGGER.info(
                        "Job %s completed successfully in %dms (next: %s)",
                        job.name,
                        duration_ms,
                        job.next_run_at,
                    )

                    # Send notification if configured
                    await self._send_notification(
                        job=job,
                        status="success",
                        result=result_summary,
                    )

                except TimeoutError:
                    duration_ms = int((time.monotonic() - start_time) * 1000)
                    LOGGER.error("Job %s timed out after %ds", job.name, job.timeout_seconds)

                    job.last_run_at = datetime.now(UTC).replace(tzinfo=None)
                    job.last_run_status = "error"
                    job.last_run_result = f"Timed out after {job.timeout_seconds}s"
                    job.last_run_duration_ms = duration_ms
                    job.run_count += 1
                    job.error_count += 1
                    job.status = "error"
                    job.next_run_at = self._compute_next_run(job.cron_expression)
                    await session.commit()

                    await self._send_notification(
                        job=job,
                        status="error",
                        result=f"Job timed out after {job.timeout_seconds} seconds",
                    )

                except Exception as e:
                    duration_ms = int((time.monotonic() - start_time) * 1000)
                    error_msg = str(e)[:500]
                    LOGGER.error("Job %s failed: %s", job.name, error_msg, exc_info=True)

                    job.last_run_at = datetime.now(UTC).replace(tzinfo=None)
                    job.last_run_status = "error"
                    job.last_run_result = f"Error: {error_msg}"
                    job.last_run_duration_ms = duration_ms
                    job.run_count += 1
                    job.error_count += 1
                    job.status = "error"
                    job.next_run_at = self._compute_next_run(job.cron_expression)
                    await session.commit()

                    await self._send_notification(
                        job=job,
                        status="error",
                        result=f"Error: {error_msg}",
                    )

        except Exception as e:
            LOGGER.error("Fatal error executing job %s: %s", job_id, e, exc_info=True)
        finally:
            self._running_jobs.discard(job_id)

    @staticmethod
    def _compute_next_run(cron_expression: str) -> datetime:
        """Compute the next run time from a cron expression.

        Args:
            cron_expression: Standard 5-field cron expression.

        Returns:
            Next execution datetime (naive UTC).
        """
        now = datetime.now(UTC).replace(tzinfo=None)
        cron = croniter(cron_expression, now)
        next_time: datetime = cron.get_next(datetime)
        return next_time

    async def _send_notification(
        self,
        job: ScheduledJob,
        status: str,
        result: str,
    ) -> None:
        """Send notification if configured on the job.

        Args:
            job: The scheduled job.
            status: Execution status (success, error).
            result: Result summary text.
        """
        if not job.notification_channel or not job.notification_target:
            return

        try:
            if job.notification_channel == "email":
                await self._send_email_notification(job, status, result)
            elif job.notification_channel == "telegram":
                await self._send_telegram_notification(job, status, result)
            else:
                LOGGER.warning(
                    "Unknown notification channel: %s for job %s",
                    job.notification_channel,
                    job.name,
                )
        except Exception as e:
            LOGGER.error(
                "Failed to send %s notification for job %s: %s",
                job.notification_channel,
                job.name,
                e,
            )

    async def _send_email_notification(
        self,
        job: ScheduledJob,
        status: str,
        result: str,
    ) -> None:
        """Send email notification for job completion."""
        email_service = get_email_service_optional()
        if not email_service or not email_service.is_configured():
            LOGGER.warning("Email service not configured, skipping notification for job %s", job.name)
            return

        from core.utils.email import wrap_html_email

        status_label = "completed successfully" if status == "success" else "failed"
        subject = f"[Scheduler] {job.name} {status_label}"

        html_body = wrap_html_email(
            title=subject,
            body_content=(
                f"<h3>Job: {job.name}</h3>"
                f"<p><strong>Status:</strong> {status}</p>"
                f"<p><strong>Cron:</strong> <code>{job.cron_expression}</code></p>"
                f"<p><strong>Duration:</strong> {job.last_run_duration_ms or 0}ms</p>"
                f"<hr>"
                f"<p><strong>Result:</strong></p>"
                f"<pre>{result[:1000]}</pre>"
            ),
            footer_text="Sent by AI Agent Platform Scheduler",
        )

        message = EmailMessage(
            to=[job.notification_target],
            subject=subject,
            html_body=html_body,
            text_body=f"Job: {job.name}\nStatus: {status}\n\nResult:\n{result[:1000]}",
        )

        email_result = await email_service.send(message)
        if email_result.success:
            LOGGER.info("Email notification sent for job %s to %s", job.name, job.notification_target)
        else:
            LOGGER.error(
                "Email notification failed for job %s: %s", job.name, email_result.error
            )

    async def _send_telegram_notification(
        self,
        job: ScheduledJob,
        status: str,
        result: str,
    ) -> None:
        """Send Telegram notification for job completion."""
        if not self._telegram_bot_token:
            LOGGER.warning(
                "Telegram bot token not configured, skipping notification for job %s",
                job.name,
            )
            return

        from aiogram import Bot

        status_emoji = "OK" if status == "success" else "ERROR"
        message_text = (
            f"[{status_emoji}] Scheduled Job: {job.name}\n"
            f"Status: {status}\n"
            f"Duration: {job.last_run_duration_ms or 0}ms\n\n"
            f"Result:\n{result[:500]}"
        )

        bot = Bot(token=self._telegram_bot_token)
        try:
            await bot.send_message(
                chat_id=job.notification_target,
                text=message_text,
            )
            LOGGER.info(
                "Telegram notification sent for job %s to %s",
                job.name,
                job.notification_target,
            )
        finally:
            await bot.session.close()

    async def initialize_next_run_times(self) -> None:
        """Compute next_run_at for all enabled jobs that have NULL next_run_at.

        Called once at startup to seed the schedule for jobs that were
        created while the scheduler was not running.
        """
        async with self._session_factory() as session:
            stmt = select(ScheduledJob).where(
                ScheduledJob.is_enabled.is_(True),
                ScheduledJob.next_run_at.is_(None),
            )
            result = await session.execute(stmt)
            jobs = result.scalars().all()

            for job in jobs:
                try:
                    job.next_run_at = self._compute_next_run(job.cron_expression)
                    LOGGER.info(
                        "Initialized next_run_at for job %s: %s",
                        job.name,
                        job.next_run_at,
                    )
                except Exception as e:
                    LOGGER.error("Invalid cron expression for job %s: %s", job.name, e)
                    job.status = "error"
                    job.last_run_result = f"Invalid cron expression: {e}"

            # Also reset any jobs stuck in "running" status (from unclean shutdown)
            reset_stmt = (
                update(ScheduledJob)
                .where(ScheduledJob.status == "running")
                .values(status="active")
            )
            await session.execute(reset_stmt)

            await session.commit()
            LOGGER.info("Initialized schedule for %d jobs", len(jobs))
```

**Ops tasks:**
- Run `./stack check`
- Fix any import or type issues

**Files affected:**
- `services/agent/src/interfaces/scheduler/__init__.py` (create)
- `services/agent/src/interfaces/scheduler/adapter.py` (create)

---

### Phase 3: App Startup Integration (Step 3)

**Engineer tasks:**

Wire the SchedulerAdapter into the application lifecycle in `app.py`.

**File: `services/agent/src/interfaces/http/app.py`** -- Modify the `lifespan` function.

Find this block (around line 477-481):

```python
        # Email service + background schedulers (via orchestrator)
        email_service = create_email_service(settings)
        scheduler, homey_scheduler = await start_schedulers(email_service)

        yield  # Application runs here
```

Replace with:

```python
        # Email service + background schedulers (via orchestrator)
        email_service = create_email_service(settings)
        scheduler, homey_scheduler = await start_schedulers(email_service)

        # Job Scheduler (cron-based skill execution)
        from interfaces.scheduler.adapter import SchedulerAdapter

        job_scheduler = SchedulerAdapter(
            session_factory=AsyncSessionLocal,
            service_factory=service_factory,
            telegram_bot_token=settings.telegram_bot_token if hasattr(settings, "telegram_bot_token") else None,
        )
        await job_scheduler.initialize_next_run_times()
        await job_scheduler.start()
        LOGGER.info("Job scheduler started")

        yield  # Application runs here
```

Find the shutdown block (around line 483-496):

```python
        # --- SHUTDOWN ---
        await homey_scheduler.stop()
        await scheduler.stop()
```

Add after `await scheduler.stop()`:

```python
        await job_scheduler.stop()
```

Also, we need the Telegram bot token available. Check if `Settings` already has it.

**File: `services/agent/src/core/runtime/config.py`** -- Add Telegram bot token setting if not present.

Add after `cors_allowed_origins` field (around line 195):

```python
    # Telegram Bot
    telegram_bot_token: str | None = Field(  # noqa: S105
        default=None,
        description="Telegram bot token for notifications and adapter.",
    )
```

Note: The Telegram adapter currently reads the token from env directly. Adding it to Settings centralizes configuration. Check existing code to see how it's loaded -- if there's already a `AGENT_TELEGRAM_BOT_TOKEN` env var, this field will automatically pick it up via the `AGENT_` prefix.

**Ops tasks:**
- Run `./stack check`

**Files affected:**
- `services/agent/src/interfaces/http/app.py` (modify)
- `services/agent/src/core/runtime/config.py` (modify)

---

### Phase 4: Admin API Endpoints (Step 4)

**Engineer tasks:**

Create the admin API for managing scheduled jobs.

**File: `services/agent/src/interfaces/http/admin_scheduler.py`** (create):

```python
"""Admin endpoints for scheduled job management."""

# ruff: noqa: E501
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
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
            raise ValueError("Job name can only contain letters, numbers, spaces, hyphens, and underscores")
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
            raise ValueError(f"Invalid notification channel. Must be one of: {', '.join(valid_channels)}")
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

    LOGGER.info("Created scheduled job %s (context: %s, cron: %s)", job.name, context_id, job.cron_expression)

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
            <button class="btn" onclick="loadAllJobs()">Refresh</button>
        </div>
        <div id="jobsList">
            <div class="loading">Loading...</div>
        </div>
    </div>
"""

    extra_css = """
    .job-row { padding: 12px 0; border-bottom: 1px solid var(--border); }
    .job-row:last-child { border-bottom: none; }
    .job-name { font-weight: 600; font-size: 14px; }
    .job-meta { font-size: 12px; color: var(--text-muted); display: flex; gap: 12px; margin-top: 4px; flex-wrap: wrap; }
    .status-active { color: var(--success); }
    .status-error { color: var(--error); }
    .status-paused { color: var(--text-muted); }
    .status-running { color: var(--warning); }
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
            el.innerHTML = '<div class="empty-state">No scheduled jobs. Create one from a context detail page.</div>';
            return;
        }
        el.innerHTML = jobs.map(j => {
            const statusClass = 'status-' + j.status;
            const nextRun = j.next_run_at ? new Date(j.next_run_at).toLocaleString() : 'N/A';
            const lastRun = j.last_run_at ? new Date(j.last_run_at).toLocaleString() : 'Never';
            return '<div class="job-row">' +
                '<div style="display:flex;justify-content:space-between;align-items:center;">' +
                '<span class="job-name">' + escapeHtml(j.name) + '</span>' +
                '<span class="badge ' + statusClass + '">' + j.status + '</span>' +
                '</div>' +
                '<div class="job-meta">' +
                '<span>Cron: <code>' + escapeHtml(j.cron_expression) + '</code></span>' +
                '<span>Next: ' + nextRun + '</span>' +
                '<span>Last: ' + lastRun + '</span>' +
                '<span>Runs: ' + j.run_count + '</span>' +
                '<span>Errors: ' + j.error_count + '</span>' +
                '<span>Context: <a href="/platformadmin/contexts/' + j.context_id + '/#scheduler">' + j.context_id.substring(0, 8) + '...</a></span>' +
                '</div></div>';
        }).join('');
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
    session: AsyncSession = Depends(get_db),
) -> JobListResponse:
    """List all scheduled jobs across all contexts."""
    stmt = select(ScheduledJob).order_by(ScheduledJob.next_run_at.asc())
    result = await session.execute(stmt)
    jobs = result.scalars().all()

    return JobListResponse(
        jobs=[_job_to_response(j) for j in jobs],
        total=len(jobs),
    )


__all__ = ["router"]
```

**Register the router in `app.py`.**

Add import near line 61 (after the last admin router import):
```python
from interfaces.http.admin_scheduler import router as admin_scheduler_router
```

Add router registration near line 860 (after `admin_api_router`):
```python
    app.include_router(admin_scheduler_router)
```

**Add navigation item in `admin_shared.py`.**

Add to `ADMIN_NAV_ITEMS` list (after the "Credentials" item):
```python
    NavItem("Scheduler", "/platformadmin/scheduler/", "&#128339;", "features"),
```

**Ops tasks:**
- Run `./stack check`

**Files affected:**
- `services/agent/src/interfaces/http/admin_scheduler.py` (create)
- `services/agent/src/interfaces/http/app.py` (modify -- add import + include_router)
- `services/agent/src/interfaces/http/admin_shared.py` (modify -- add NavItem)

---

### Phase 5: Context Detail Scheduler Tab (Step 5)

**Engineer tasks:**

Add a "Scheduler" tab to the context detail page. This requires modifying both the HTML template and the Python route.

**File: `services/agent/src/interfaces/http/templates/admin_context_detail.html`**

1. **Add the tab button** -- Find the tab bar (line 7-15). Add a new button after the "Conversations" tab:

```html
    <button class="tab" data-tab="scheduler" onclick="switchTab('scheduler')">Scheduler</button>
```

2. **Add the tab content div** -- After the `tab-conversations` div (line 53-56), add:

```html
<div id="tab-scheduler" class="tab-content" style="display:none;">
    <div class="card" id="schedulerCard">
        <div class="loading">Loading...</div>
    </div>
</div>
```

3. **Add the Create Job modal** -- After the existing "Add Credential Modal" div (line 59-80, before `SECTION_SEPARATOR`):

```html
<!-- Create Scheduled Job Modal -->
<div id="createJobModal" class="modal" style="display: none;">
    <div class="modal-content">
        <h3>Create Scheduled Job</h3>
        <form id="createJobForm" onsubmit="submitJob(event)">
            <div class="form-group">
                <label>Name *</label>
                <input type="text" id="jobName" required placeholder="e.g., daily-report" pattern="^[a-zA-Z0-9_\- ]+$">
            </div>
            <div class="form-group">
                <label>Prompt *</label>
                <textarea id="jobPrompt" required placeholder="What should the agent do?" style="min-height: 80px;"></textarea>
            </div>
            <div class="form-group">
                <label>Cron Expression *</label>
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
                <button type="button" class="btn" onclick="hideJobModal()">Cancel</button>
                <button type="submit" class="btn btn-primary">Create Job</button>
            </div>
        </form>
    </div>
</div>
```

4. **Add scheduler tab handler** in JS section -- In the `switchTab` function's if/else chain (around line 138-147), add:

```javascript
        else if (tab === 'scheduler') loadScheduler();
```

5. **Add scheduler JS functions** -- Add before the final `// Initialize` block (before line 451):

```javascript
// --- Scheduler Tab ---
async function loadScheduler() {
    const card = document.getElementById('schedulerCard');
    const res = await fetchWithErrorHandling('/platformadmin/scheduler/context/' + CONTEXT_ID + '/jobs');
    if (!res) { card.innerHTML = '<div class="empty-state">Failed to load scheduler data</div>'; return; }
    const data = await res.json();
    const jobs = data.jobs || [];

    let html = '<div class="card-header"><span class="card-title">Scheduled Jobs (' + jobs.length + ')</span>';
    html += '<button class="btn btn-primary btn-sm" onclick="showJobModal()">+ Create Job</button></div>';

    if (jobs.length === 0) {
        html += '<div class="empty-state">No scheduled jobs for this context</div>';
    } else {
        jobs.forEach(function(j) {
            const statusClass = j.status === 'active' ? 'badge-success' :
                               j.status === 'error' ? 'badge-danger' :
                               j.status === 'running' ? 'badge-warning' : 'badge-info';
            const nextRun = j.next_run_at ? new Date(j.next_run_at).toLocaleString() : 'N/A';
            const lastRun = j.last_run_at ? new Date(j.last_run_at).toLocaleString() : 'Never';
            const lastStatus = j.last_run_status || 'N/A';

            html += '<div class="cred-row" style="flex-direction: column; align-items: flex-start;">';
            html += '<div style="display:flex;justify-content:space-between;width:100%;align-items:center;">';
            html += '<div><strong>' + escapeHtml(j.name) + '</strong> <span class="badge ' + statusClass + '">' + j.status + '</span></div>';
            html += '<div style="display:flex;gap:4px;">';
            html += '<button class="btn btn-sm" onclick="toggleJob(\'' + j.id + '\')" title="' + (j.is_enabled ? 'Pause' : 'Enable') + '">' + (j.is_enabled ? 'Pause' : 'Enable') + '</button>';
            html += '<button class="btn btn-sm btn-primary" onclick="runJobNow(\'' + j.id + '\')" ' + (j.is_enabled ? '' : 'disabled') + '>Run Now</button>';
            html += '<button class="btn btn-sm btn-danger" onclick="deleteJob(\'' + j.id + '\', \'' + escapeHtml(j.name) + '\')">Delete</button>';
            html += '</div></div>';
            html += '<div class="job-meta" style="margin-top: 8px; font-size: 12px; color: var(--text-muted); display: flex; gap: 12px; flex-wrap: wrap;">';
            html += '<span>Cron: <code>' + escapeHtml(j.cron_expression) + '</code></span>';
            html += '<span>Next: ' + nextRun + '</span>';
            html += '<span>Last: ' + lastRun + ' (' + lastStatus + ')</span>';
            html += '<span>Runs: ' + j.run_count + '</span>';
            if (j.error_count > 0) html += '<span style="color: var(--error);">Errors: ' + j.error_count + '</span>';
            if (j.notification_channel) html += '<span>Notify: ' + j.notification_channel + '</span>';
            html += '</div>';
            if (j.description) {
                html += '<div style="margin-top: 4px; font-size: 12px; color: var(--text-muted);">' + escapeHtml(j.description) + '</div>';
            }
            if (j.last_run_result) {
                html += '<details style="margin-top: 4px; font-size: 12px;"><summary>Last Result</summary><pre style="white-space: pre-wrap; max-height: 200px; overflow: auto; background: var(--bg); padding: 8px; border-radius: 4px; margin-top: 4px;">' + escapeHtml(j.last_run_result) + '</pre></details>';
            }
            html += '</div>';
        });
    }
    card.innerHTML = html;
}

function showJobModal() { document.getElementById('createJobModal').style.display = 'flex'; }
function hideJobModal() { document.getElementById('createJobModal').style.display = 'none'; document.getElementById('createJobForm').reset(); document.getElementById('notifTargetGroup').style.display = 'none'; }
function toggleNotifTarget() {
    const ch = document.getElementById('jobNotifChannel').value;
    document.getElementById('notifTargetGroup').style.display = ch ? 'block' : 'none';
}

async function submitJob(e) {
    e.preventDefault();
    const body = {
        name: document.getElementById('jobName').value,
        skill_prompt: document.getElementById('jobPrompt').value,
        cron_expression: document.getElementById('jobCron').value,
        description: document.getElementById('jobDescription').value || null,
        notification_channel: document.getElementById('jobNotifChannel').value || null,
        notification_target: document.getElementById('jobNotifTarget').value || null,
        timeout_seconds: parseInt(document.getElementById('jobTimeout').value) || 300
    };
    const res = await fetchWithErrorHandling('/platformadmin/scheduler/context/' + CONTEXT_ID + '/jobs', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    });
    if (res) {
        showToast('Job created', 'success');
        hideJobModal();
        loadedTabs['scheduler'] = false;
        loadScheduler();
    }
}

async function toggleJob(jobId) {
    const res = await fetchWithErrorHandling('/platformadmin/scheduler/jobs/' + jobId + '/toggle', { method: 'POST' });
    if (res) {
        showToast('Job toggled', 'success');
        loadedTabs['scheduler'] = false;
        loadScheduler();
    }
}

async function runJobNow(jobId) {
    const res = await fetchWithErrorHandling('/platformadmin/scheduler/jobs/' + jobId + '/run-now', { method: 'POST' });
    if (res) {
        showToast('Job triggered for immediate execution', 'success');
        loadedTabs['scheduler'] = false;
        loadScheduler();
    }
}

async function deleteJob(jobId, name) {
    if (!confirm('Delete job "' + name + '"? This cannot be undone.')) return;
    const res = await fetchWithErrorHandling('/platformadmin/scheduler/jobs/' + jobId, { method: 'DELETE' });
    if (res) {
        showToast('Job deleted', 'success');
        loadedTabs['scheduler'] = false;
        loadScheduler();
    }
}
```

**Ops tasks:**
- Run `./stack check`

**Files affected:**
- `services/agent/src/interfaces/http/templates/admin_context_detail.html` (modify)

---

### Phase 6: Tests (Step 6)

**Engineer tasks:**

Create unit tests for the scheduler adapter and admin endpoints.

**File: `services/agent/src/core/tests/test_scheduler.py`** (create):

```python
"""Tests for the scheduler service."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from interfaces.scheduler.adapter import SchedulerAdapter


class TestComputeNextRun:
    """Test cron expression parsing and next_run computation."""

    def test_every_minute(self) -> None:
        """Test '* * * * *' returns a time within 60 seconds."""
        now = datetime.now(UTC).replace(tzinfo=None)
        next_run = SchedulerAdapter._compute_next_run("* * * * *")
        assert next_run > now
        diff = (next_run - now).total_seconds()
        assert diff <= 60

    def test_daily_at_nine(self) -> None:
        """Test '0 9 * * *' returns a time with hour=9, minute=0."""
        next_run = SchedulerAdapter._compute_next_run("0 9 * * *")
        assert next_run.hour == 9
        assert next_run.minute == 0

    def test_weekdays_only(self) -> None:
        """Test '0 8 * * 1-5' returns a weekday."""
        next_run = SchedulerAdapter._compute_next_run("0 8 * * 1-5")
        assert next_run.weekday() < 5  # 0=Mon, 4=Fri


class TestSchedulerAdapter:
    """Test SchedulerAdapter initialization and control."""

    def test_init(self) -> None:
        """Test adapter initializes with correct defaults."""
        session_factory = MagicMock()
        service_factory = MagicMock()

        adapter = SchedulerAdapter(
            session_factory=session_factory,
            service_factory=service_factory,
        )

        assert adapter.platform_name == "scheduler"
        assert adapter._running is False
        assert adapter._task is None

    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        """Test adapter start and stop lifecycle."""
        session_factory = MagicMock()
        service_factory = MagicMock()

        adapter = SchedulerAdapter(
            session_factory=session_factory,
            service_factory=service_factory,
        )

        await adapter.start()
        assert adapter._running is True
        assert adapter._task is not None

        await adapter.stop()
        assert adapter._running is False


class TestCronValidation:
    """Test cron expression validation in admin endpoints."""

    def test_valid_cron_expressions(self) -> None:
        """Test that valid cron expressions are accepted."""
        from croniter import croniter

        valid = [
            "* * * * *",
            "0 9 * * *",
            "0 8 * * 1-5",
            "*/15 * * * *",
            "0 0 1 * *",
        ]
        for expr in valid:
            assert croniter.is_valid(expr), f"{expr} should be valid"

    def test_invalid_cron_expressions(self) -> None:
        """Test that invalid cron expressions are rejected."""
        from croniter import croniter

        invalid = [
            "not a cron",
            "* * *",
            "60 * * * *",
            "",
        ]
        for expr in invalid:
            assert not croniter.is_valid(expr), f"{expr} should be invalid"
```

**Ops tasks:**
- Run `./stack check`
- Run `pytest services/agent/src/core/tests/test_scheduler.py -v`

**Files affected:**
- `services/agent/src/core/tests/test_scheduler.py` (create)

---

## 4. Configuration Changes

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AGENT_TELEGRAM_BOT_TOKEN` | Telegram bot token for notifications | None |

No new required env vars. The scheduler uses existing infrastructure (email service, Telegram token).

### No changes to `config/tools.yaml`

The scheduler is not a tool -- it's an adapter. It does not need tool registration.

---

## 5. Testing Strategy

### Unit Tests (Phase 6)
- Cron expression parsing (valid/invalid)
- Next run time computation
- Adapter lifecycle (start/stop)

### Manual Testing
1. Create a job via admin UI with cron `*/2 * * * *` (every 2 minutes)
2. Verify it appears in context detail scheduler tab
3. Click "Run Now" and verify execution
4. Check conversation history for results
5. Test email notification (if configured)
6. Test toggle (pause/resume)
7. Test deletion
8. Verify cascade delete when context is deleted

### Integration Testing (future)
- Full end-to-end: create job -> wait for execution -> verify conversation + notification

---

## 6. Quality Checks

After each phase:
```bash
./stack check
```

Final verification:
```bash
pytest services/agent/src/core/tests/test_scheduler.py -v
./stack check --no-fix
```

---

## 7. Security Considerations

1. **Input Validation**: Cron expressions validated via `croniter.is_valid()`. Job names sanitized with regex. Prompt limited to 5000 chars.

2. **Multi-Tenant Isolation**: Jobs scoped to `context_id`. AgentService created per-context via ServiceFactory. Each job runs with context-owner's credentials/permissions.

3. **Authentication**: All admin endpoints require admin authentication (Entra ID via `verify_admin_user` and `require_csrf`).

4. **Timeout Protection**: Each job has a configurable timeout (default 300s, max 3600s). Prevents runaway executions.

5. **Concurrency Control**: Semaphore limits concurrent job executions to 3. Prevents resource exhaustion. Running job tracking prevents duplicate executions.

6. **Notification Target Validation**: Email addresses should be validated before use. Telegram chat IDs are opaque strings (validated by Telegram API).

7. **No Arbitrary Code Execution**: Jobs execute via `AgentService.handle_request()` with the same security model as any other platform request. No shell commands or direct code execution.

8. **Cascade Delete**: When a context is deleted, all its scheduled jobs are cascade-deleted via FK constraint.

9. **Crash Recovery**: On startup, `initialize_next_run_times()` resets stuck "running" jobs and seeds next_run_at for new jobs.

---

## 8. Success Criteria

- [ ] `ScheduledJob` model in database with migration
- [ ] SchedulerAdapter starts/stops with the application
- [ ] Jobs execute at cron-defined times via AgentService
- [ ] Results stored in conversation history
- [ ] Email notifications sent when configured
- [ ] Telegram notifications sent when configured
- [ ] Admin UI: scheduler tab on context detail page
- [ ] Admin UI: global scheduler overview page
- [ ] CRUD operations for jobs via admin API
- [ ] "Run Now" triggers immediate execution
- [ ] Toggle (pause/resume) works
- [ ] `./stack check` passes with no errors
- [ ] Unit tests pass

---

## 9. Agent Delegation Strategy

### Engineer (Sonnet) - Implementation
- Write all new code files (adapter, admin endpoints, tests)
- Modify existing files (models, app.py, config, template)
- Debug complex Mypy or integration issues

### Ops (Haiku) - Quality and Deployment
- Run quality gate: `./stack check` after each phase
- Fix simple lint errors (auto-fixable)
- Run `poetry add croniter@^3.0` to add dependency
- Git operations (commit, push, PR)
- Report test results
- Escalate complex issues to Engineer

### Cost Optimization
Each implementation step follows:
1. Engineer writes/modifies code
2. Engineer delegates to Ops for quality check
3. Ops reports back (or escalates if complex errors)
4. Repeat for next step

### Step Summary

| Step | Description | Engineer | Ops |
|------|-------------|----------|-----|
| 1 | DB Model + Migration | Write model, migration | poetry add, stack check |
| 2 | Scheduler Adapter | Write adapter code | stack check |
| 3 | App Startup Integration | Wire into app.py | stack check |
| 4 | Admin API Endpoints | Write endpoints | stack check |
| 5 | Context Detail Tab | Modify HTML template | stack check |
| 6 | Tests | Write test file | pytest, stack check |

---

## 10. File Summary

### New Files
| Path | Description |
|------|-------------|
| `services/agent/src/interfaces/scheduler/__init__.py` | Package init |
| `services/agent/src/interfaces/scheduler/adapter.py` | SchedulerAdapter (main logic) |
| `services/agent/src/interfaces/http/admin_scheduler.py` | Admin API endpoints |
| `services/agent/alembic/versions/20260213_add_scheduled_jobs.py` | DB migration |
| `services/agent/src/core/tests/test_scheduler.py` | Unit tests |

### Modified Files
| Path | Change |
|------|--------|
| `services/agent/pyproject.toml` | Add `croniter` dependency |
| `services/agent/src/core/db/models.py` | Add `ScheduledJob` model + Context relationship |
| `services/agent/src/core/runtime/config.py` | Add `telegram_bot_token` setting |
| `services/agent/src/interfaces/http/app.py` | Import + register scheduler router, start/stop adapter in lifespan |
| `services/agent/src/interfaces/http/admin_shared.py` | Add "Scheduler" nav item |
| `services/agent/src/interfaces/http/templates/admin_context_detail.html` | Add scheduler tab + modal + JS |
