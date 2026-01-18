"""Price Tracker Service implementation."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.price_tracker.models import PricePoint, PriceWatch, Product, ProductStore, Store

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """Return naive UTC datetime for database operations.

    Returns naive datetime to match TIMESTAMP WITHOUT TIME ZONE columns.
    """
    return datetime.now(UTC).replace(tzinfo=None)


class PriceTrackerService:
    """Service for managing price tracking operations.

    Handles product tracking, price history, and watch alerts.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Initialize the price tracker service.

        Args:
            session_factory: SQLAlchemy async session factory.
        """
        self.session_factory = session_factory

    async def record_price(
        self, product_store_id: str, price_data: dict[str, Any], session: AsyncSession
    ) -> PricePoint | None:
        """Record a new price point for a product-store combination.

        Args:
            product_store_id: UUID string of the ProductStore.
            price_data: Dictionary containing price information:
                - price_sek: Regular price (required)
                - unit_price_sek: Price per unit (optional)
                - offer_price_sek: Offer price (optional)
                - offer_type: Type of offer (optional)
                - offer_details: Offer description (optional)
                - in_stock: Stock status (default: True)
                - raw_data: Raw scraped data (optional)
            session: Database session.

        Returns:
            Created PricePoint instance, or None if ProductStore not found.
        """
        try:
            product_store_uuid = uuid.UUID(product_store_id)

            # Verify ProductStore exists
            stmt = select(ProductStore).where(ProductStore.id == product_store_uuid)
            result = await session.execute(stmt)
            product_store = result.scalar_one_or_none()

            if not product_store:
                logger.warning(f"ProductStore {product_store_id} not found")
                return None

            # Create price point
            price_point = PricePoint(
                product_store_id=product_store_uuid,
                price_sek=Decimal(str(price_data["price_sek"])),
                unit_price_sek=(
                    Decimal(str(price_data["unit_price_sek"]))
                    if price_data.get("unit_price_sek")
                    else None
                ),
                offer_price_sek=(
                    Decimal(str(price_data["offer_price_sek"]))
                    if price_data.get("offer_price_sek")
                    else None
                ),
                offer_type=price_data.get("offer_type"),
                offer_details=price_data.get("offer_details"),
                in_stock=price_data.get("in_stock", True),
                raw_data=price_data.get("raw_data"),
                checked_at=_utc_now(),
            )

            session.add(price_point)

            # Update ProductStore last_checked_at
            product_store.last_checked_at = _utc_now()

            await session.commit()
            await session.refresh(price_point)

            logger.info(
                f"Recorded price {price_point.price_sek} SEK for ProductStore {product_store_id}"
            )
            return price_point

        except Exception:
            await session.rollback()
            logger.exception(f"Failed to record price for ProductStore {product_store_id}")
            return None

    async def get_price_history(
        self, product_id: str, days: int = 30
    ) -> list[dict[str, str | float | datetime | None]]:
        """Get price history for a product across all stores.

        Args:
            product_id: UUID string of the product.
            days: Number of days of history to retrieve.

        Returns:
            List of price point dictionaries sorted by checked_at descending.
        """
        async with self.session_factory() as session:
            try:
                product_uuid = uuid.UUID(product_id)
                cutoff_date = _utc_now() - timedelta(days=days)

                stmt = (
                    select(PricePoint, Store)
                    .join(ProductStore, PricePoint.product_store_id == ProductStore.id)
                    .join(Store, ProductStore.store_id == Store.id)
                    .where(ProductStore.product_id == product_uuid)
                    .where(PricePoint.checked_at >= cutoff_date)
                    .order_by(PricePoint.checked_at.desc())
                )

                result = await session.execute(stmt)
                rows = result.all()

                return [
                    {
                        "checked_at": price_point.checked_at,
                        "price_sek": float(price_point.price_sek),
                        "store_name": store.name,
                        "offer_price_sek": (
                            float(price_point.offer_price_sek)
                            if price_point.offer_price_sek
                            else None
                        ),
                    }
                    for price_point, store in rows
                ]

            except Exception:
                logger.exception(f"Failed to get price history for product {product_id}")
                return []

    async def get_current_deals(
        self, store_type: str | None = None
    ) -> list[dict[str, str | float]]:
        """Get products currently on offer.

        Args:
            store_type: Filter by store type (grocery, pharmacy, etc.). Optional.

        Returns:
            List of product dictionaries with current offers.
        """
        async with self.session_factory() as session:
            try:
                # Subquery to get latest price point for each product-store
                cutoff = _utc_now() - timedelta(days=1)

                stmt = (
                    select(PricePoint, Product, Store)
                    .join(ProductStore, PricePoint.product_store_id == ProductStore.id)
                    .join(Product, ProductStore.product_id == Product.id)
                    .join(Store, ProductStore.store_id == Store.id)
                    .where(PricePoint.offer_price_sek.is_not(None))
                    .where(PricePoint.checked_at >= cutoff)
                    .order_by(PricePoint.checked_at.desc())
                )

                if store_type:
                    stmt = stmt.where(Store.store_type == store_type)

                result = await session.execute(stmt)
                rows = result.all()

                # Deduplicate by product-store (keep latest)
                seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
                deals: list[dict[str, str | float]] = []

                for price_point, product, store in rows:
                    key = (product.id, store.id)
                    if key in seen:
                        continue
                    seen.add(key)

                    deals.append(
                        {
                            "product_id": str(product.id),
                            "product_name": product.name,
                            "store_name": store.name,
                            "regular_price_sek": float(price_point.price_sek),
                            "offer_price_sek": float(price_point.offer_price_sek),
                            "offer_type": price_point.offer_type or "unknown",
                        }
                    )

                return deals

            except Exception:
                logger.exception("Failed to get current deals")
                return []

    async def get_products(
        self, search: str | None = None, store_id: str | None = None
    ) -> list[dict[str, str]]:
        """List products with optional filtering.

        Args:
            search: Search term for product name/brand. Optional.
            store_id: Filter by specific store. Optional.

        Returns:
            List of product dictionaries.
        """
        async with self.session_factory() as session:
            try:
                stmt = select(Product)

                # Apply search filter
                if search:
                    search_term = f"%{search}%"
                    stmt = stmt.where(
                        or_(
                            Product.name.ilike(search_term),
                            Product.brand.ilike(search_term),
                        )
                    )

                # Apply store filter
                if store_id:
                    store_uuid = uuid.UUID(store_id)
                    stmt = (
                        stmt.join(ProductStore, Product.id == ProductStore.product_id)
                        .where(ProductStore.store_id == store_uuid)
                        .distinct()
                    )

                stmt = stmt.order_by(Product.name)
                result = await session.execute(stmt)
                products = result.scalars().all()

                return [
                    {
                        "id": str(product.id),
                        "name": product.name,
                        "brand": product.brand or "",
                        "category": product.category or "",
                    }
                    for product in products
                ]

            except Exception:
                logger.exception("Failed to get products")
                return []

    async def get_stores(self) -> list[dict[str, str]]:
        """Get all active stores.

        Returns:
            List of store dictionaries.
        """
        async with self.session_factory() as session:
            try:
                stmt = select(Store).where(Store.is_active.is_(True)).order_by(Store.name)

                result = await session.execute(stmt)
                stores = result.scalars().all()

                return [
                    {
                        "id": str(store.id),
                        "name": store.name,
                        "slug": store.slug,
                        "store_type": store.store_type,
                    }
                    for store in stores
                ]

            except Exception:
                logger.exception("Failed to get stores")
                return []

    async def create_product(
        self, name: str, brand: str | None, category: str | None, unit: str | None
    ) -> Product:
        """Create a new product.

        Args:
            name: Product name.
            brand: Product brand. Optional.
            category: Product category. Optional.
            unit: Unit of measurement. Optional.

        Returns:
            Created Product instance.
        """
        async with self.session_factory() as session:
            product = Product(
                name=name,
                brand=brand,
                category=category,
                unit=unit,
            )

            session.add(product)
            await session.commit()
            await session.refresh(product)

            logger.info(f"Created product: {product.name} (ID: {product.id})")
            return product

    async def link_product_store(
        self, product_id: str, store_id: str, store_url: str, check_frequency_hours: int = 24
    ) -> ProductStore:
        """Link a product to a store.

        Args:
            product_id: UUID string of the product.
            store_id: UUID string of the store.
            store_url: URL to the product page on the store website.
            check_frequency_hours: How often to check price (default 24 hours).

        Returns:
            Created ProductStore instance.
        """
        async with self.session_factory() as session:
            product_uuid = uuid.UUID(product_id)
            store_uuid = uuid.UUID(store_id)

            product_store = ProductStore(
                product_id=product_uuid,
                store_id=store_uuid,
                store_url=store_url,
                check_frequency_hours=check_frequency_hours,
            )

            session.add(product_store)
            await session.commit()
            await session.refresh(product_store)

            logger.info(
                f"Linked product {product_id} to store {store_id} "
                f"(ProductStore ID: {product_store.id})"
            )
            return product_store

    async def create_watch(
        self,
        context_id: str,
        product_id: str,
        email: str,
        target_price: Decimal | None,
        alert_on_any_offer: bool,
        price_drop_threshold_percent: int | None = None,
        unit_price_target_sek: Decimal | None = None,
        unit_price_drop_threshold_percent: int | None = None,
    ) -> PriceWatch:
        """Create a price watch for a product.

        Args:
            context_id: UUID string of the context (multi-tenant).
            product_id: UUID string of the product.
            email: Email address for alerts.
            target_price: Target price threshold. Optional.
            alert_on_any_offer: Alert on any offer regardless of price.
            price_drop_threshold_percent: Alert when price drops by this percentage. Optional.
            unit_price_target_sek: Alert when unit price drops below threshold. Optional.
            unit_price_drop_threshold_percent: Alert when unit price drops by %. Optional.

        Returns:
            Created PriceWatch instance.
        """
        async with self.session_factory() as session:
            context_uuid = uuid.UUID(context_id)
            product_uuid = uuid.UUID(product_id)

            watch = PriceWatch(
                context_id=context_uuid,
                product_id=product_uuid,
                email_address=email,
                target_price_sek=target_price,
                alert_on_any_offer=alert_on_any_offer,
                price_drop_threshold_percent=price_drop_threshold_percent,
                unit_price_target_sek=unit_price_target_sek,
                unit_price_drop_threshold_percent=unit_price_drop_threshold_percent,
            )

            session.add(watch)
            await session.commit()
            await session.refresh(watch)

            logger.info(f"Created price watch for product {product_id} (Watch ID: {watch.id})")
            return watch

    async def delete_product(self, product_id: str) -> None:
        """Delete a product and all associated data.

        This cascades to delete:
        - ProductStore links (which cascade to PricePoints)
        - PriceWatches

        Args:
            product_id: UUID string of the product to delete.

        Raises:
            ValueError: If product_id is invalid or product not found.
        """
        async with self.session_factory() as session:
            try:
                product_uuid = uuid.UUID(product_id)
            except ValueError as e:
                raise ValueError(f"Invalid product_id format: {product_id}") from e

            # Get the product
            stmt = select(Product).where(Product.id == product_uuid)
            result = await session.execute(stmt)
            product = result.scalar_one_or_none()

            if not product:
                raise ValueError(f"Product not found: {product_id}")

            # Delete the product (cascading will handle related records)
            await session.delete(product)
            await session.commit()

            logger.info(f"Deleted product {product_id} ({product.name})")


__all__ = ["PriceTrackerService"]
