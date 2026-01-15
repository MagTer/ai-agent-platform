"""Background scheduler for periodic price checks."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload

from core.protocols import IFetcher

from .models import PricePoint, PriceWatch, ProductStore
from .notifier import PriceNotifier
from .parser import PriceExtractionResult, PriceParser

logger = logging.getLogger(__name__)


class PriceCheckScheduler:
    """Background scheduler for periodic price checks."""

    CHECK_INTERVAL_SECONDS = 300  # Check for due items every 5 minutes
    RATE_LIMIT_DELAY = 5.0  # Seconds between requests to same store
    BATCH_SIZE = 10  # Max items to check per cycle

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        fetcher: IFetcher,
        notifier: PriceNotifier | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.fetcher = fetcher
        self.parser = PriceParser()
        self.notifier = notifier
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background scheduler."""
        if self._running:
            logger.warning("Scheduler already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Price check scheduler started")

    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Price check scheduler stopped")

    async def _run_loop(self) -> None:
        """Main scheduler loop."""
        while self._running:
            try:
                await self._check_due_products()
            except Exception as e:
                logger.error(f"Scheduler error: {e}", exc_info=True)

            await asyncio.sleep(self.CHECK_INTERVAL_SECONDS)

    async def _check_due_products(self) -> None:
        """Check all products that are due for a price check."""
        async with self.session_factory() as session:
            now = datetime.now(UTC).replace(tzinfo=None)

            # Find product-stores where:
            # 1. is_active = True
            # 2. last_checked_at is NULL OR last_checked_at + frequency < now
            stmt = (
                select(ProductStore)
                .options(
                    joinedload(ProductStore.product),
                    joinedload(ProductStore.store),
                )
                .where(
                    ProductStore.is_active.is_(True),
                    (
                        ProductStore.last_checked_at.is_(None)
                        | (
                            ProductStore.last_checked_at
                            + func.make_interval(0, 0, 0, 0, ProductStore.check_frequency_hours)
                            < now
                        )
                    ),
                )
                .limit(self.BATCH_SIZE)
            )

            result = await session.execute(stmt)
            due_items = result.unique().scalars().all()

            if not due_items:
                logger.debug("No products due for price check")
                return

            logger.info(f"Checking {len(due_items)} products")

            last_store_id = None
            for product_store in due_items:
                try:
                    # Rate limit per store
                    if last_store_id == product_store.store_id:
                        await asyncio.sleep(self.RATE_LIMIT_DELAY)

                    await self._check_single_product(product_store, session)

                    # Update last_checked_at
                    product_store.last_checked_at = datetime.now(UTC).replace(tzinfo=None)
                    await session.commit()

                    last_store_id = product_store.store_id

                except Exception as e:
                    logger.error(f"Failed to check product {product_store.id}: {e}")
                    await session.rollback()

    async def _check_single_product(
        self,
        product_store: ProductStore,
        session: AsyncSession,
    ) -> None:
        """Check price for a single product-store combination."""
        logger.info(f"Checking price: {product_store.product.name} at {product_store.store.name}")

        # Fetch page content
        fetch_result = await self.fetcher.fetch(product_store.store_url)
        if not fetch_result.get("ok"):
            logger.warning(f"Failed to fetch {product_store.store_url}")
            return

        # Extract price using LLM
        text_content = fetch_result.get("text", "")
        extraction = await self.parser.extract_price(
            text_content=text_content,
            store_slug=product_store.store.slug,
            product_name=product_store.product.name,
        )

        # Record price point
        price_point = PricePoint(
            product_store_id=product_store.id,
            price_sek=extraction.price_sek,
            unit_price_sek=extraction.unit_price_sek,
            offer_price_sek=extraction.offer_price_sek,
            offer_type=extraction.offer_type,
            offer_details=extraction.offer_details,
            in_stock=extraction.in_stock,
            raw_data=extraction.raw_response,
            checked_at=datetime.now(UTC).replace(tzinfo=None),
        )
        session.add(price_point)

        # Check for alerts
        await self._check_alerts(product_store, extraction, session)

    async def _check_alerts(
        self,
        product_store: ProductStore,
        extraction: PriceExtractionResult,
        session: AsyncSession,
    ) -> None:
        """Check if price triggers any alerts."""
        if not self.notifier:
            return

        # Get active watches for this product
        stmt = select(PriceWatch).where(
            PriceWatch.product_id == product_store.product_id,
            PriceWatch.is_active.is_(True),
        )
        result = await session.execute(stmt)
        watches = result.scalars().all()

        current_price = extraction.offer_price_sek or extraction.price_sek
        if current_price is None:
            return

        now = datetime.now(UTC).replace(tzinfo=None)

        for watch in watches:
            should_alert = False

            # Check target price
            if watch.target_price_sek and current_price <= watch.target_price_sek:
                should_alert = True

            # Check for any offer
            if watch.alert_on_any_offer and extraction.offer_type:
                should_alert = True

            # Don't spam - check last alerted time (24h cooldown)
            if should_alert and watch.last_alerted_at:
                cooldown = timedelta(hours=24)
                if (now - watch.last_alerted_at) < cooldown:
                    logger.debug(f"Skipping alert for watch {watch.id} - cooldown")
                    continue

            if should_alert:
                logger.info(f"Sending alert for watch {watch.id}")
                # Convert target_price to Decimal if present
                target_price_decimal = (
                    Decimal(str(watch.target_price_sek)) if watch.target_price_sek else None
                )
                success = await self.notifier.send_price_alert(
                    to_email=watch.email_address,
                    product_name=product_store.product.name,
                    store_name=product_store.store.name,
                    current_price=current_price,
                    target_price=target_price_decimal,
                    offer_type=extraction.offer_type,
                    offer_details=extraction.offer_details,
                    product_url=product_store.store_url,
                )

                if success:
                    watch.last_alerted_at = now
