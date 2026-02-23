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
            LOGGER.warning(
                "Email service not configured, skipping notification for job %s", job.name
            )
            return

        # Validate notification_target is set (checked by caller, but be defensive)
        if not job.notification_target:
            LOGGER.warning("No notification target set for job %s", job.name)
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
            LOGGER.info(
                "Email notification sent for job %s to %s", job.name, job.notification_target
            )
        else:
            LOGGER.error("Email notification failed for job %s: %s", job.name, email_result.error)

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

        # Validate notification_target is set (checked by caller, but be defensive)
        if not job.notification_target:
            LOGGER.warning("No notification target set for job %s", job.name)
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
            # notification_target is guaranteed non-None by check above
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
                update(ScheduledJob).where(ScheduledJob.status == "running").values(status="active")
            )
            await session.execute(reset_stmt)

            await session.commit()
            LOGGER.info("Initialized schedule for %d jobs", len(jobs))
