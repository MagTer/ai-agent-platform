"""Background scheduler for nightly Homey device cache sync."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.db.oauth_models import OAuthToken

if TYPE_CHECKING:
    from core.tools.homey import HomeyTool

logger = logging.getLogger(__name__)


class HomeyDeviceSyncScheduler:
    """Background scheduler for nightly Homey device cache refresh.

    Runs at 03:00 UTC every night to refresh device caches for all
    contexts with Homey OAuth tokens.
    """

    SYNC_HOUR = 3  # 03:00 UTC

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Initialize scheduler.

        Args:
            session_factory: Factory for creating database sessions.
        """
        self.session_factory = session_factory
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background scheduler."""
        if self._running:
            logger.warning("Homey sync scheduler already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Homey device sync scheduler started")

    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Homey device sync scheduler stopped")

    async def _run_loop(self) -> None:
        """Main scheduler loop - waits until 03:00 UTC then runs sync."""
        while self._running:
            try:
                # Calculate time until next 03:00 UTC
                now = datetime.now(UTC)
                next_sync = datetime.combine(
                    now.date(),
                    time(hour=self.SYNC_HOUR, tzinfo=UTC),
                )
                if next_sync <= now:
                    next_sync += timedelta(days=1)

                wait_seconds = (next_sync - now).total_seconds()
                logger.info(f"Next Homey device sync in {wait_seconds / 3600:.1f} hours")

                await asyncio.sleep(wait_seconds)

                if self._running:
                    await self._sync_all_contexts()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Homey sync scheduler error: {e}", exc_info=True)
                # Wait 1 hour before retrying on error
                await asyncio.sleep(3600)

    async def _sync_all_contexts(self) -> None:
        """Sync device caches for all contexts with Homey tokens."""
        from core.tools.homey import HomeyTool

        async with self.session_factory() as session:
            # Find all contexts with Homey OAuth tokens
            stmt = select(distinct(OAuthToken.context_id)).where(OAuthToken.provider == "homey")
            result = await session.execute(stmt)
            context_ids = result.scalars().all()

            if not context_ids:
                logger.info("No contexts with Homey tokens to sync")
                return

            logger.info(f"Syncing Homey devices for {len(context_ids)} contexts")

            tool = HomeyTool()

            for context_id in context_ids:
                try:
                    await self._sync_context_devices(context_id, tool, session)
                except Exception as e:
                    logger.error(f"Failed to sync context {context_id}: {e}")

            await session.commit()

    async def _sync_context_devices(
        self,
        context_id: UUID,
        tool: HomeyTool,
        session: AsyncSession,
    ) -> None:
        """Sync devices for a single context.

        Args:
            context_id: Context UUID.
            tool: HomeyTool instance.
            session: Database session.
        """
        from core.providers import get_token_manager_optional

        token_manager = get_token_manager_optional()
        if not token_manager:
            logger.warning("Token manager not available for sync")
            return

        oauth_token = await token_manager.get_token(
            provider="homey",
            context_id=context_id,
        )

        if not oauth_token:
            logger.debug(f"No valid Homey token for context {context_id}")
            return

        # Get user's Homeys
        homeys = await tool._get_user_homeys(oauth_token)

        for homey in homeys:
            homey_id = homey.get("_id")
            if not homey_id:
                continue

            try:
                session_token, homey_url = await tool._ensure_session(homey_id, oauth_token)

                devices = await tool._homey_request(
                    "GET",
                    homey_url,
                    "/api/manager/devices/device",
                    session_token,
                )

                if devices:
                    await tool._populate_cache(context_id, homey_id, devices, session)
                    logger.info(f"Synced {len(devices)} devices for Homey {homey_id}")

            except Exception as e:
                logger.error(f"Failed to sync Homey {homey_id}: {e}")


__all__ = ["HomeyDeviceSyncScheduler"]
