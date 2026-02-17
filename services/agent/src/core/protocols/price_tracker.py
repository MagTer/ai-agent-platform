"""Protocol for price tracking operations."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class IPriceTracker(Protocol):
    """Abstract interface for price tracking operations.

    This protocol defines the contract for tracking product prices across stores,
    managing price history, and handling user watch alerts.
    """

    async def get_price_history(
        self, product_id: str, days: int = 30
    ) -> list[dict[str, str | float | datetime | None]]:
        """Get price history for a product across all stores.

        Args:
            product_id: UUID of the product.
            days: Number of days of history to retrieve.

        Returns:
            List of price point dictionaries with:
                - checked_at: Timestamp
                - price_sek: Price at that time
                - store_name: Name of the store
                - offer_price_sek: Offer price if applicable (may be None)
        """
        ...

    async def get_current_deals(
        self, store_type: str | None = None
    ) -> list[dict[str, str | float]]:
        """Get products currently on offer.

        Args:
            store_type: Filter by store type (grocery, pharmacy, etc.). Optional.

        Returns:
            List of product dictionaries with:
                - product_id: UUID string
                - product_name: Name of product
                - store_name: Name of store
                - regular_price_sek: Regular price
                - offer_price_sek: Offer price
                - offer_type: Type of offer
        """
        ...

    async def get_products(
        self, search: str | None = None, store_id: str | None = None
    ) -> list[dict[str, str]]:
        """List products with optional filtering.

        Args:
            search: Search term for product name/brand. Optional.
            store_id: Filter by specific store. Optional.

        Returns:
            List of product dictionaries with:
                - id: UUID string
                - name: Product name
                - brand: Product brand
                - category: Product category
        """
        ...


@runtime_checkable
class IPriceScheduler(Protocol):
    """Abstract interface for the price check scheduler."""

    def get_status(self) -> dict[str, bool | str | date | dict[str, int] | None]:
        """Get scheduler status and statistics."""
        ...


__all__ = ["IPriceScheduler", "IPriceTracker"]
