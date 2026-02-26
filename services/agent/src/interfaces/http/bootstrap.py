"""Application bootstrap: lifespan management, provider registration, seeding."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI

from core.runtime.config import Settings
from core.runtime.litellm_client import LiteLLMClient

LOGGER = logging.getLogger(__name__)

# Shared httpx client for readiness checks (reuse across requests)
_READINESS_HTTP_CLIENT: httpx.AsyncClient | None = None


def get_readiness_http_client() -> httpx.AsyncClient | None:
    """Return the shared HTTP client used by readiness probes."""
    return _READINESS_HTTP_CLIENT


async def _seed_system_context(session_factory: Any) -> uuid.UUID:
    """Idempotently ensure the system context exists; return its UUID.

    Args:
        session_factory: Async session factory from SQLAlchemy.

    Returns:
        UUID of the system context.
    """
    from sqlalchemy import select

    from core.db.models import Context

    async with session_factory() as session:
        row = (
            await session.execute(select(Context).where(Context.name == "system"))
        ).scalar_one_or_none()
        if not row:
            row = Context(
                name="system",
                type="system",
                display_name="System",
                config={},
                default_cwd="/tmp",  # noqa: S108
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            LOGGER.info("System context: %s (created)", row.id)
        else:
            LOGGER.info("System context: %s (existing)", row.id)
        return row.id


async def _seed_system_jobs(session_factory: Any, system_context_id: uuid.UUID) -> None:
    """Idempotently create the golden query scheduled jobs under the system context.

    Args:
        session_factory: Async session factory from SQLAlchemy.
        system_context_id: UUID of the system context.
    """
    from sqlalchemy import select

    from core.db.models import ScheduledJob
    from interfaces.scheduler.adapter import SchedulerAdapter

    golden_jobs = [
        {
            "name": "golden-routing",
            "cron_expression": "0 6 * * *",
            "skill_prompt": "Run semantic eval category=routing",
            "timeout_seconds": 300,
        },
        {
            "name": "golden-regression",
            "cron_expression": "0 7 * * 1",
            "skill_prompt": "Run semantic eval category=regression",
            "timeout_seconds": 300,
        },
        {
            "name": "golden-skills",
            "cron_expression": "0 6 * * 0",
            "skill_prompt": "Run semantic eval category=skills",
            "timeout_seconds": 420,
        },
    ]

    async with session_factory() as session:
        for job_def in golden_jobs:
            existing = (
                await session.execute(
                    select(ScheduledJob).where(
                        ScheduledJob.context_id == system_context_id,
                        ScheduledJob.name == job_def["name"],
                    )
                )
            ).scalar_one_or_none()
            if existing:
                continue

            next_run = SchedulerAdapter._compute_next_run(str(job_def["cron_expression"]))
            job = ScheduledJob(
                context_id=system_context_id,
                name=job_def["name"],
                cron_expression=job_def["cron_expression"],
                skill_prompt=job_def["skill_prompt"],
                timeout_seconds=job_def["timeout_seconds"],
                is_enabled=True,
                status="active",
                next_run_at=next_run,
            )
            session.add(job)
            LOGGER.info("Seeded system job: %s", job_def["name"])

        await session.commit()


def create_lifespan(settings: Settings, litellm_client: LiteLLMClient) -> Any:
    """Create the lifespan context manager for the FastAPI application.

    Args:
        settings: Application settings.
        litellm_client: Shared LiteLLM client instance.

    Returns:
        An async context manager suitable for use as FastAPI lifespan.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Manage application startup and shutdown lifecycle."""
        import asyncio

        global _READINESS_HTTP_CLIENT

        # --- STARTUP ---
        # Initialize shared HTTP client for readiness checks
        _READINESS_HTTP_CLIENT = httpx.AsyncClient(timeout=3.0)

        # Dependency Injection: Register module implementations via orchestrator
        from core.db.engine import AsyncSessionLocal
        from orchestrator.startup import create_email_service, register_providers, start_schedulers

        token_manager = await register_providers(settings, litellm_client)

        # Initialize model capability registry
        from core.runtime.model_registry import ModelCapabilityRegistry

        ModelCapabilityRegistry.get_instance()
        LOGGER.info("Model capability registry initialized")

        # Initialize MCP client pool for context-aware MCP connections
        from core.mcp.client_pool import McpClientPool
        from core.tools.mcp_loader import set_mcp_client_pool

        mcp_pool = McpClientPool(settings)
        set_mcp_client_pool(mcp_pool)
        mcp_pool.start_eviction()
        LOGGER.info("MCP client pool initialized with background eviction")

        # Initialize SkillRegistry for skills-native execution
        from core.skills import SkillRegistry
        from core.tools.loader import load_tool_registry

        # Load base tool registry for skill tool validation
        base_tool_registry = load_tool_registry(settings.tools_config_path)
        # Use async parallel loading for faster startup
        skill_registry = await SkillRegistry.create_async(tool_registry=base_tool_registry)
        LOGGER.info(
            "SkillRegistry initialized with %d skills (async parallel loading)",
            len(skill_registry.available()),
        )

        # Create ServiceFactory for context-aware service creation
        from core.runtime.service_factory import ServiceFactory

        service_factory = ServiceFactory(
            settings=settings,
            litellm_client=litellm_client,
            skill_registry=skill_registry,
        )
        app.state.service_factory = service_factory

        LOGGER.info("ServiceFactory initialized with SkillRegistry")

        # Warm-up LiteLLM connection in background
        async def warm_up_litellm() -> None:
            try:
                await litellm_client.list_models()
            except Exception:
                LOGGER.warning("LiteLLM warm-up failed (non-critical)")

        asyncio.create_task(warm_up_litellm())

        # Database retention cleanup - runs on startup and daily
        async def retention_cleanup_loop() -> None:
            """Run retention cleanup on startup, then daily."""
            from core.db.retention import run_retention_cleanup

            # Initial cleanup on startup (after short delay)
            await asyncio.sleep(30)  # Wait for DB to be fully ready

            while True:
                try:
                    async with AsyncSessionLocal() as session:
                        results = await run_retention_cleanup(session)
                        LOGGER.info(f"Daily retention cleanup: {results}")
                except Exception as e:
                    LOGGER.error(f"Retention cleanup failed: {e}")

                # Sleep for 24 hours
                await asyncio.sleep(24 * 60 * 60)

        asyncio.create_task(retention_cleanup_loop())
        LOGGER.info("Retention cleanup scheduled (startup + daily)")

        # Email service + background schedulers (via orchestrator)
        email_service = create_email_service(settings)
        scheduler, homey_scheduler = await start_schedulers(email_service)

        # Job Scheduler (cron-based skill execution)
        from interfaces.scheduler.adapter import SchedulerAdapter

        telegram_token = (
            settings.telegram_bot_token if hasattr(settings, "telegram_bot_token") else None
        )
        job_scheduler = SchedulerAdapter(
            session_factory=AsyncSessionLocal,
            service_factory=service_factory,
            telegram_bot_token=telegram_token,
        )
        await job_scheduler.initialize_next_run_times()
        await job_scheduler.start()
        LOGGER.info("Job scheduler started")

        # Seed system context and golden query jobs
        system_context_id = await _seed_system_context(AsyncSessionLocal)
        app.state.system_context_id = system_context_id
        await _seed_system_jobs(AsyncSessionLocal, system_context_id)

        yield  # Application runs here

        # --- SHUTDOWN ---
        await job_scheduler.stop()
        await homey_scheduler.stop()
        await scheduler.stop()
        # Clean up email service
        if email_service is not None:
            await email_service.close()
        await mcp_pool.stop()
        await litellm_client.aclose()
        await token_manager.shutdown()
        # Close shared Qdrant client in ServiceFactory
        await service_factory.close()
        # Close shared HTTP client
        if _READINESS_HTTP_CLIENT is not None:
            await _READINESS_HTTP_CLIENT.aclose()

    return lifespan


__all__ = ["create_lifespan", "get_readiness_http_client"]
