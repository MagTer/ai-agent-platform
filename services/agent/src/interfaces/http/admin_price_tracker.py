# ruff: noqa: E501, RUF005
"""Admin API endpoints for price tracker module."""

from __future__ import annotations

import logging
import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.user_service import get_user_default_context
from core.db.engine import AsyncSessionLocal, get_db
from core.providers import get_fetcher
from interfaces.http.admin_auth import AdminUser, require_admin_or_redirect, verify_admin_user
from interfaces.http.admin_shared import render_admin_page
from modules.price_tracker.models import PriceWatch, Product, ProductStore, Store
from modules.price_tracker.parser import PriceParser
from modules.price_tracker.service import PriceTrackerService

from .schemas.price_tracker import (
    DealResponse,
    PricePointResponse,
    PriceWatchCreate,
    PriceWatchUpdate,
    ProductCreate,
    ProductResponse,
    ProductStoreLink,
    ProductUpdate,
    StoreResponse,
)

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platformadmin/price-tracker",
    tags=["platform-admin", "price-tracker"],
)


def get_price_tracker_service() -> PriceTrackerService:
    """Get PriceTrackerService instance."""
    return PriceTrackerService(AsyncSessionLocal)


@router.get("/me/context")
async def get_my_context(
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, str | None]:
    """Get the authenticated user's default context_id.

    Returns:
        Dictionary with context_id and email.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        user_context = await get_user_default_context(admin.db_user, session)
        if not user_context:
            return {
                "context_id": None,
                "email": admin.email,
                "message": "No default context found",
            }

        return {
            "context_id": str(user_context.id),
            "email": admin.email,
        }
    except Exception as e:
        LOGGER.exception("Failed to get user context")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get(
    "/stores", response_model=list[StoreResponse], dependencies=[Depends(verify_admin_user)]
)
async def list_stores(
    session: AsyncSession = Depends(get_db),
) -> list[StoreResponse]:
    """List all configured stores.

    Returns:
        List of store information including slug, type, and status.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        stmt = select(Store).where(Store.is_active.is_(True)).order_by(Store.name)
        result = await session.execute(stmt)
        stores = result.scalars().all()

        return [
            StoreResponse(
                id=str(store.id),
                name=store.name,
                slug=store.slug,
                store_type=store.store_type,
                base_url=store.base_url,
                is_active=store.is_active,
            )
            for store in stores
        ]
    except Exception as e:
        LOGGER.exception("Failed to list stores")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/products", response_model=list[ProductResponse])
async def list_products(
    search: str | None = None,
    store_id: str | None = None,
    context_id: str | None = None,
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
) -> list[ProductResponse]:
    """List products with optional search/filter.

    Args:
        search: Search term for product name or brand.
        store_id: Filter by specific store UUID.
        context_id: Filter by user context UUID (shows only products with watches in that context).
        admin: Authenticated admin user.
        session: Database session.

    Returns:
        List of products with linked stores.

    Security:
        Requires admin role via Entra ID authentication.
        Users can only query their own context_id.
    """
    try:
        # Security check: if context_id provided, verify user has access to it
        if context_id:
            try:
                context_uuid = uuid.UUID(context_id)
            except ValueError as e:
                raise HTTPException(status_code=400, detail="Invalid context_id format") from e

            # Get user's default context
            user_context = await get_user_default_context(admin.db_user, session)
            if not user_context:
                raise HTTPException(
                    status_code=403,
                    detail="User has no associated context",
                )

            # Verify user can only query their own context
            if context_uuid != user_context.id:
                raise HTTPException(
                    status_code=403,
                    detail="Access denied: you can only view products in your own context",
                )

        # Build query with proper join handling
        stmt = select(Product)
        already_joined_product_store = False

        # Apply context filter: show only products that have watches in this context
        if context_id:
            context_uuid = uuid.UUID(context_id)
            stmt = (
                stmt.join(PriceWatch, Product.id == PriceWatch.product_id)
                .where(PriceWatch.context_id == context_uuid)
                .where(PriceWatch.is_active.is_(True))
                .distinct()
            )

        # Apply search filter
        if search:
            from sqlalchemy import or_

            search_term = f"%{search}%"
            stmt = stmt.where(
                or_(
                    Product.name.ilike(search_term),
                    Product.brand.ilike(search_term),
                )
            )

        # Apply store filter
        if store_id:
            try:
                store_uuid = uuid.UUID(store_id)
                if not already_joined_product_store:
                    stmt = stmt.join(ProductStore, Product.id == ProductStore.product_id)
                    already_joined_product_store = True
                stmt = stmt.where(ProductStore.store_id == store_uuid).distinct()
            except ValueError as e:
                raise HTTPException(status_code=400, detail="Invalid store_id format") from e

        stmt = stmt.order_by(Product.name)
        result = await session.execute(stmt)
        products = result.scalars().all()

        # Fetch linked stores for each product
        product_responses: list[ProductResponse] = []
        for product in products:
            # Get product stores
            ps_stmt = (
                select(ProductStore, Store)
                .join(Store, ProductStore.store_id == Store.id)
                .where(ProductStore.product_id == product.id)
            )
            ps_result = await session.execute(ps_stmt)
            ps_rows = ps_result.all()

            stores_data: list[dict[str, str | int | None | float]] = []
            for ps, store in ps_rows:
                # Get latest price point for this product-store
                from modules.price_tracker.models import PricePoint

                price_stmt = (
                    select(PricePoint)
                    .where(PricePoint.product_store_id == ps.id)
                    .order_by(PricePoint.checked_at.desc())
                    .limit(1)
                )
                price_result = await session.execute(price_stmt)
                latest_price = price_result.scalar_one_or_none()

                store_data: dict[str, str | int | None | float] = {
                    "product_store_id": str(ps.id),
                    "store_id": str(ps.store_id),
                    "store_name": store.name,
                    "store_slug": store.slug,
                    "store_url": ps.store_url,
                    "check_frequency_hours": ps.check_frequency_hours,
                    "check_weekday": ps.check_weekday,
                    "last_checked_at": (
                        ps.last_checked_at.isoformat() if ps.last_checked_at else None
                    ),
                    "price_sek": (
                        float(latest_price.price_sek)
                        if latest_price and latest_price.price_sek
                        else None
                    ),
                    "unit_price_sek": (
                        float(latest_price.unit_price_sek)
                        if latest_price and latest_price.unit_price_sek
                        else None
                    ),
                    "in_stock": latest_price.in_stock if latest_price else None,
                }
                stores_data.append(store_data)

            product_responses.append(
                ProductResponse(
                    id=str(product.id),
                    name=product.name,
                    brand=product.brand,
                    category=product.category,
                    unit=product.unit,
                    stores=stores_data,
                )
            )

        return product_responses
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Failed to list products")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/products", status_code=201, dependencies=[Depends(verify_admin_user)])
async def create_product(
    data: ProductCreate,
    service: PriceTrackerService = Depends(get_price_tracker_service),
) -> dict[str, str]:
    """Create a new product to track.

    Args:
        data: Product creation data.
        service: Price tracker service.

    Returns:
        Dictionary with product_id and success message.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        product = await service.create_product(
            name=data.name,
            brand=data.brand,
            category=data.category,
            unit=data.unit,
        )
        return {"product_id": str(product.id), "message": "Product created successfully"}
    except Exception as e:
        LOGGER.exception("Failed to create product")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get(
    "/products/{product_id}",
    response_model=ProductResponse,
    dependencies=[Depends(verify_admin_user)],
)
async def get_product(
    product_id: str,
    session: AsyncSession = Depends(get_db),
) -> ProductResponse:
    """Get single product with all linked stores.

    Args:
        product_id: Product UUID.
        session: Database session.

    Returns:
        Product data with linked stores.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        product_uuid = uuid.UUID(product_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid product_id format") from e

    try:
        stmt = select(Product).where(Product.id == product_uuid)
        result = await session.execute(stmt)
        product = result.scalar_one_or_none()

        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        # Get linked stores
        ps_stmt = (
            select(ProductStore, Store)
            .join(Store, ProductStore.store_id == Store.id)
            .where(ProductStore.product_id == product.id)
        )
        ps_result = await session.execute(ps_stmt)
        ps_rows = ps_result.all()

        stores_data: list[dict[str, str | int | None | float]] = []
        for ps, store in ps_rows:
            # Get latest price point for this product-store
            from modules.price_tracker.models import PricePoint

            price_stmt = (
                select(PricePoint)
                .where(PricePoint.product_store_id == ps.id)
                .order_by(PricePoint.checked_at.desc())
                .limit(1)
            )
            price_result = await session.execute(price_stmt)
            latest_price = price_result.scalar_one_or_none()

            store_data: dict[str, str | int | None | float] = {
                "product_store_id": str(ps.id),
                "store_id": str(ps.store_id),
                "store_name": store.name,
                "store_slug": store.slug,
                "store_url": ps.store_url,
                "check_frequency_hours": ps.check_frequency_hours,
                "check_weekday": ps.check_weekday,
                "last_checked_at": ps.last_checked_at.isoformat() if ps.last_checked_at else None,
                "price_sek": (
                    float(latest_price.price_sek)
                    if latest_price and latest_price.price_sek
                    else None
                ),
                "unit_price_sek": (
                    float(latest_price.unit_price_sek)
                    if latest_price and latest_price.unit_price_sek
                    else None
                ),
                "in_stock": latest_price.in_stock if latest_price else None,
            }
            stores_data.append(store_data)

        return ProductResponse(
            id=str(product.id),
            name=product.name,
            brand=product.brand,
            category=product.category,
            unit=product.unit,
            stores=stores_data,
        )
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception(f"Failed to get product {product_id}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put("/products/{product_id}", dependencies=[Depends(verify_admin_user)])
async def update_product(
    product_id: str,
    data: ProductUpdate,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Update an existing product.

    Args:
        product_id: Product UUID.
        data: Product update data (only provided fields are updated).
        session: Database session.

    Returns:
        Success message.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        product_uuid = uuid.UUID(product_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid product_id format") from e

    try:
        stmt = select(Product).where(Product.id == product_uuid)
        result = await session.execute(stmt)
        product = result.scalar_one_or_none()

        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        # Update only provided fields
        if data.name is not None:
            product.name = data.name
        if data.brand is not None:
            product.brand = data.brand if data.brand else None
        if data.category is not None:
            product.category = data.category if data.category else None
        if data.unit is not None:
            product.unit = data.unit if data.unit else None

        await session.commit()
        return {"message": "Product updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception(f"Failed to update product {product_id}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post(
    "/products/{product_id}/stores", status_code=201, dependencies=[Depends(verify_admin_user)]
)
async def link_product_to_store(
    product_id: str,
    data: ProductStoreLink,
    service: PriceTrackerService = Depends(get_price_tracker_service),
) -> dict[str, str]:
    """Link a product to a store with URL.

    Args:
        product_id: Product UUID.
        data: Store link data.
        service: Price tracker service.

    Returns:
        Dictionary with product_store_id and success message.

    Security:
        Requires admin role via Entra ID authentication.
    """
    # Validate frequency range (3 days to 10 days)
    if not (72 <= data.check_frequency_hours <= 240):
        raise HTTPException(
            status_code=400,
            detail="check_frequency_hours must be between 72 and 240 (inclusive)",
        )
    # Validate weekday if provided (0=Monday, 6=Sunday)
    if data.check_weekday is not None and not (0 <= data.check_weekday <= 6):
        raise HTTPException(
            status_code=400,
            detail="check_weekday must be between 0 (Monday) and 6 (Sunday)",
        )

    try:
        product_store = await service.link_product_store(
            product_id=product_id,
            store_id=data.store_id,
            store_url=data.store_url,
            check_frequency_hours=data.check_frequency_hours,
            check_weekday=data.check_weekday,
        )
        return {
            "product_store_id": str(product_store.id),
            "message": "Product linked to store successfully",
        }
    except Exception as e:
        LOGGER.exception(f"Failed to link product {product_id} to store {data.store_id}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put(
    "/products/{product_id}/stores/{store_id}/frequency",
    dependencies=[Depends(verify_admin_user)],
)
async def update_check_frequency(
    product_id: str,
    store_id: str,
    request: dict[str, int],
    session: AsyncSession = Depends(get_db),
) -> dict[str, str | None]:
    """Update check frequency for a product-store link.

    Args:
        product_id: Product UUID.
        store_id: Store UUID.
        request: Dictionary containing check_frequency_hours.
        session: Database session.

    Returns:
        Success message with updated next_check_at timestamp.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        product_uuid = uuid.UUID(product_id)
        store_uuid = uuid.UUID(store_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from e

    check_frequency_hours = request.get("check_frequency_hours")
    check_weekday = request.get("check_weekday")  # 0=Monday, 6=Sunday, None=use frequency

    if check_frequency_hours is None:
        raise HTTPException(status_code=400, detail="check_frequency_hours is required")

    # Validate frequency range (3 days to 10 days)
    if not (72 <= check_frequency_hours <= 240):
        raise HTTPException(
            status_code=400,
            detail="check_frequency_hours must be between 72 and 240 (inclusive)",
        )
    # Validate weekday if provided
    if check_weekday is not None and not (0 <= check_weekday <= 6):
        raise HTTPException(
            status_code=400,
            detail="check_weekday must be between 0 (Monday) and 6 (Sunday)",
        )

    try:
        stmt = select(ProductStore).where(
            ProductStore.product_id == product_uuid, ProductStore.store_id == store_uuid
        )
        result = await session.execute(stmt)
        product_store = result.scalar_one_or_none()

        if not product_store:
            raise HTTPException(status_code=404, detail="Product-store link not found")

        # Update frequency and weekday
        product_store.check_frequency_hours = check_frequency_hours
        product_store.check_weekday = check_weekday

        # Calculate next_check_at
        now_utc = datetime.now(UTC).replace(tzinfo=None)

        if check_weekday is not None:
            # Weekday-based: schedule for next occurrence of that weekday
            # Spread checks over morning hours (06:00 - 12:00)
            days_until = (check_weekday - now_utc.weekday()) % 7
            if days_until == 0 and now_utc.hour >= 12:
                # Already past check window today, schedule for next week
                days_until = 7
            # Random hour between 6 and 12
            check_hour = 6 + int(random.random() * 6)  # noqa: S311
            check_minute = int(random.random() * 60)  # noqa: S311
            next_check = now_utc.replace(hour=check_hour, minute=check_minute, second=0)
            next_check = next_check + timedelta(days=days_until)
            product_store.next_check_at = next_check
        else:
            # Frequency-based: use jitter as before
            jitter_percent = 0.1
            jitter_hours = (
                (random.random() * 2 - 1) * jitter_percent * check_frequency_hours  # noqa: S311
            )
            product_store.next_check_at = now_utc + timedelta(
                hours=check_frequency_hours + jitter_hours
            )

        await session.commit()
        await session.refresh(product_store)

        return {
            "message": "Frequency updated",
            "next_check_at": (
                product_store.next_check_at.isoformat() if product_store.next_check_at else None
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception(f"Failed to update frequency for product {product_id}, store {store_id}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete(
    "/products/{product_id}/stores/{store_id}", dependencies=[Depends(verify_admin_user)]
)
async def unlink_product_from_store(
    product_id: str,
    store_id: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Remove product-store link.

    Args:
        product_id: Product UUID.
        store_id: Store UUID.
        session: Database session.

    Returns:
        Success message.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        product_uuid = uuid.UUID(product_id)
        store_uuid = uuid.UUID(store_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from e

    try:
        stmt = select(ProductStore).where(
            ProductStore.product_id == product_uuid, ProductStore.store_id == store_uuid
        )
        result = await session.execute(stmt)
        product_store = result.scalar_one_or_none()

        if not product_store:
            raise HTTPException(status_code=404, detail="Product-store link not found")

        await session.delete(product_store)
        await session.commit()

        return {"message": "Product unlinked from store successfully"}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception(f"Failed to unlink product {product_id} from store {store_id}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get(
    "/products/{product_id}/prices",
    response_model=list[PricePointResponse],
    dependencies=[Depends(verify_admin_user)],
)
async def get_price_history(
    product_id: str,
    days: int = 30,
    session: AsyncSession = Depends(get_db),
) -> list[PricePointResponse]:
    """Get price history for a product across all stores.

    Args:
        product_id: Product UUID.
        days: Number of days of history to retrieve (default 30).
        session: Database session.

    Returns:
        List of price points sorted by checked_at descending.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        product_uuid = uuid.UUID(product_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid product_id format") from e

    try:
        from datetime import timedelta

        from modules.price_tracker.models import PricePoint

        cutoff_date = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)

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
            PricePointResponse(
                checked_at=price_point.checked_at.isoformat(),
                store_name=store.name,
                store_slug=store.slug,
                price_sek=float(price_point.price_sek) if price_point.price_sek else None,
                unit_price_sek=(
                    float(price_point.unit_price_sek) if price_point.unit_price_sek else None
                ),
                offer_price_sek=(
                    float(price_point.offer_price_sek) if price_point.offer_price_sek else None
                ),
                offer_type=price_point.offer_type,
                offer_details=price_point.offer_details,
                in_stock=price_point.in_stock,
            )
            for price_point, store in rows
        ]
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception(f"Failed to get price history for product {product_id}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/check/{product_store_id}", dependencies=[Depends(verify_admin_user)])
async def trigger_price_check(
    product_store_id: str,
    session: AsyncSession = Depends(get_db),
    service: PriceTrackerService = Depends(get_price_tracker_service),
) -> dict[str, str | float | None]:
    """Manually trigger a price check for a product-store combination.

    This endpoint:
    1. Fetches the product page using WebFetcher
    2. Extracts price using PriceParser
    3. Records price using PriceTrackerService

    Args:
        product_store_id: ProductStore UUID.
        session: Database session.
        service: Price tracker service.

    Returns:
        Dictionary with extracted price data.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        ps_uuid = uuid.UUID(product_store_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid product_store_id format") from e

    try:
        # Get ProductStore with joined Store and Product
        stmt = (
            select(ProductStore, Store, Product)
            .join(Store, ProductStore.store_id == Store.id)
            .join(Product, ProductStore.product_id == Product.id)
            .where(ProductStore.id == ps_uuid)
        )
        result = await session.execute(stmt)
        row = result.one_or_none()

        if not row:
            raise HTTPException(status_code=404, detail="Product-store link not found")

        product_store, store, product = row

        # Fetch page content
        fetcher = get_fetcher()
        fetch_result = await fetcher.fetch(product_store.store_url)

        if not fetch_result.get("ok") or not fetch_result.get("text"):
            error_msg = fetch_result.get("error", "Unknown fetch error")
            raise HTTPException(status_code=502, detail=f"Failed to fetch page: {error_msg}")

        # Parse price data
        parser = PriceParser()
        extraction_result = await parser.extract_price(
            text_content=fetch_result["text"],
            store_slug=store.slug,
            product_name=product.name,
        )

        if not extraction_result.price_sek:
            return {
                "message": "Price extraction failed - no price found",
                "confidence": extraction_result.confidence,
                "price_sek": None,
                "offer_price_sek": None,
            }

        # Record price
        unit_price = (
            float(extraction_result.unit_price_sek) if extraction_result.unit_price_sek else None
        )
        offer_price = (
            float(extraction_result.offer_price_sek) if extraction_result.offer_price_sek else None
        )

        price_data: dict[str, Any] = {
            "price_sek": float(extraction_result.price_sek),
            "unit_price_sek": unit_price,
            "offer_price_sek": offer_price,
            "offer_type": extraction_result.offer_type,
            "offer_details": extraction_result.offer_details,
            "in_stock": extraction_result.in_stock,
            "raw_data": extraction_result.raw_response,
        }

        price_point = await service.record_price(product_store_id, price_data, session)

        if not price_point:
            raise HTTPException(status_code=500, detail="Failed to record price")

        unit_price_result = (
            float(price_point.unit_price_sek) if price_point.unit_price_sek else None
        )
        offer_price_result = (
            float(price_point.offer_price_sek) if price_point.offer_price_sek else None
        )

        return {
            "message": "Price check completed successfully",
            "price_sek": float(price_point.price_sek),
            "unit_price_sek": unit_price_result,
            "offer_price_sek": offer_price_result,
            "offer_type": price_point.offer_type,
            "in_stock": price_point.in_stock,
            "confidence": extraction_result.confidence,
        }
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception(f"Failed to trigger price check for {product_store_id}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/deals", response_model=list[DealResponse], dependencies=[Depends(verify_admin_user)])
async def get_current_deals(
    store_type: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> list[DealResponse]:
    """Get all current offers/deals.

    Args:
        store_type: Filter by store type (grocery, pharmacy, etc.). Optional.
        session: Database session.

    Returns:
        List of current deals sorted by checked_at descending.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        from datetime import timedelta

        from modules.price_tracker.models import PricePoint

        # Get deals from last 24 hours
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)

        stmt = (
            select(PricePoint, Product, Store, ProductStore)
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
        deals: list[DealResponse] = []

        for price_point, product, store, product_store in rows:
            key = (product.id, store.id)
            if key in seen:
                continue
            seen.add(key)

            # Calculate discount percentage
            discount_percent = 0.0
            if price_point.price_sek and price_point.offer_price_sek:
                discount_percent = (
                    (float(price_point.price_sek) - float(price_point.offer_price_sek))
                    / float(price_point.price_sek)
                    * 100
                )

            deals.append(
                DealResponse(
                    product_id=str(product.id),
                    product_name=product.name,
                    store_name=store.name,
                    store_slug=store.slug,
                    price_sek=float(price_point.price_sek) if price_point.price_sek else None,
                    offer_price_sek=float(price_point.offer_price_sek),
                    offer_type=price_point.offer_type or "unknown",
                    offer_details=price_point.offer_details,
                    checked_at=price_point.checked_at.isoformat(),
                    discount_percent=discount_percent,
                    product_url=product_store.store_url,
                )
            )

        return deals
    except Exception as e:
        LOGGER.exception("Failed to get current deals")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/watches")
async def list_watches(
    context_id: str | None = None,
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """List price watches, optionally filtered by context.

    Args:
        context_id: Filter by context UUID. If not provided, defaults to user's context.
        admin: Authenticated admin user.
        session: Database session.

    Returns:
        List of price watch configurations.

    Security:
        Requires admin role via Entra ID authentication.
        Users can only query their own context_id.
    """
    try:
        # If no context_id provided, use user's default context
        if not context_id:
            user_context = await get_user_default_context(admin.db_user, session)
            if user_context:
                context_id = str(user_context.id)

        stmt = select(PriceWatch, Product).join(Product, PriceWatch.product_id == Product.id)

        if context_id:
            try:
                context_uuid = uuid.UUID(context_id)

                # Security check: verify user has access to this context
                user_context = await get_user_default_context(admin.db_user, session)
                if user_context and context_uuid != user_context.id:
                    raise HTTPException(
                        status_code=403,
                        detail="Access denied: you can only view watches in your own context",
                    )

                stmt = stmt.where(PriceWatch.context_id == context_uuid)
            except ValueError as e:
                raise HTTPException(status_code=400, detail="Invalid context_id format") from e

        stmt = stmt.where(PriceWatch.is_active.is_(True)).order_by(PriceWatch.created_at.desc())

        result = await session.execute(stmt)
        rows = result.all()

        watches_list: list[dict[str, Any]] = []
        for watch, product in rows:
            target_price = float(watch.target_price_sek) if watch.target_price_sek else None
            unit_price_target = (
                float(watch.unit_price_target_sek) if watch.unit_price_target_sek else None
            )
            last_alerted = watch.last_alerted_at.isoformat() if watch.last_alerted_at else None

            watches_list.append(
                {
                    "watch_id": str(watch.id),
                    "context_id": str(watch.context_id),
                    "product_id": str(watch.product_id),
                    "product_name": product.name,
                    "target_price_sek": target_price,
                    "alert_on_any_offer": watch.alert_on_any_offer,
                    "price_drop_threshold_percent": watch.price_drop_threshold_percent,
                    "unit_price_target_sek": unit_price_target,
                    "unit_price_drop_threshold_percent": watch.unit_price_drop_threshold_percent,
                    "email_address": watch.email_address,
                    "last_alerted_at": last_alerted,
                    "created_at": watch.created_at.isoformat(),
                }
            )

        return watches_list
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Failed to list watches")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/watches", status_code=201, dependencies=[Depends(verify_admin_user)])
async def create_watch(
    data: PriceWatchCreate,
    context_id: str,
    service: PriceTrackerService = Depends(get_price_tracker_service),
) -> dict[str, str]:
    """Create a new price watch alert.

    Args:
        data: Price watch configuration.
        context_id: Context UUID for multi-tenancy.
        service: Price tracker service.

    Returns:
        Dictionary with watch_id and success message.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        watch = await service.create_watch(
            context_id=context_id,
            product_id=data.product_id,
            email=data.email_address,
            target_price=data.target_price_sek,
            alert_on_any_offer=data.alert_on_any_offer,
            price_drop_threshold_percent=data.price_drop_threshold_percent,
            unit_price_target_sek=data.unit_price_target_sek,
            unit_price_drop_threshold_percent=data.unit_price_drop_threshold_percent,
        )
        return {"watch_id": str(watch.id), "message": "Price watch created successfully"}
    except Exception as e:
        LOGGER.exception("Failed to create price watch")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put("/watches/{watch_id}", dependencies=[Depends(verify_admin_user)])
async def update_watch(
    watch_id: str,
    data: PriceWatchUpdate,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Update a price watch.

    Args:
        watch_id: Watch UUID.
        data: Watch update data (only provided fields are updated).
        session: Database session.

    Returns:
        Success message.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        watch_uuid = uuid.UUID(watch_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid watch_id format") from e

    try:
        stmt = select(PriceWatch).where(PriceWatch.id == watch_uuid)
        result = await session.execute(stmt)
        watch = result.scalar_one_or_none()

        if not watch:
            raise HTTPException(status_code=404, detail="Price watch not found")

        # Update only provided fields
        if data.target_price_sek is not None:
            watch.target_price_sek = float(data.target_price_sek)
        if data.alert_on_any_offer is not None:
            watch.alert_on_any_offer = data.alert_on_any_offer
        if data.price_drop_threshold_percent is not None:
            watch.price_drop_threshold_percent = data.price_drop_threshold_percent
        if data.unit_price_target_sek is not None:
            watch.unit_price_target_sek = float(data.unit_price_target_sek)
        if data.unit_price_drop_threshold_percent is not None:
            watch.unit_price_drop_threshold_percent = data.unit_price_drop_threshold_percent
        if data.email_address is not None:
            watch.email_address = data.email_address

        await session.commit()
        return {"message": "Price watch updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception(f"Failed to update watch {watch_id}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/watches/{watch_id}", dependencies=[Depends(verify_admin_user)])
async def delete_watch(
    watch_id: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Delete a price watch.

    Args:
        watch_id: Watch UUID.
        session: Database session.

    Returns:
        Success message.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        watch_uuid = uuid.UUID(watch_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid watch_id format") from e

    try:
        stmt = select(PriceWatch).where(PriceWatch.id == watch_uuid)
        result = await session.execute(stmt)
        watch = result.scalar_one_or_none()

        if not watch:
            raise HTTPException(status_code=404, detail="Price watch not found")

        await session.delete(watch)
        await session.commit()

        return {"message": "Price watch deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception(f"Failed to delete watch {watch_id}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/products/{product_id}", dependencies=[Depends(verify_admin_user)])
async def delete_product(
    product_id: str,
    service: PriceTrackerService = Depends(get_price_tracker_service),
) -> dict[str, str]:
    """Delete a product and all associated data.

    This will cascade delete:
    - ProductStore links
    - PricePoints
    - PriceWatches

    Args:
        product_id: Product UUID.
        service: Price tracker service.

    Returns:
        Success message.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        await service.delete_product(product_id)
        return {"message": "Product deleted successfully"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        LOGGER.exception(f"Failed to delete product {product_id}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/", response_class=HTMLResponse)
async def price_tracker_dashboard(admin: AdminUser = Depends(require_admin_or_redirect)) -> str:
    """Server-rendered admin dashboard for price tracking.

    Returns:
        HTML dashboard for managing products, deals, and price watches.

    Security:
        Requires admin role via Entra ID authentication.
    """
    content = """
        <h1 class="page-title">Price Tracker</h1>
        <p style="color: var(--text-muted); margin-bottom: 24px;">
            Manage product price tracking, store links, and price alerts
        </p>

        <div class="card">
            <div class="card-header">
                <span class="card-title">Quick Actions</span>
            </div>
            <div style="display: flex; gap: 12px; flex-wrap: wrap;">
                <button class="btn btn-primary" onclick="showProductsView()">View Products</button>
                <button class="btn" onclick="showDealsView()">Current Deals</button>
                <button class="btn" onclick="showWatchesView()">My Watches</button>
                <button class="btn" onclick="showStoresView()">Stores</button>
            </div>
        </div>

        <div id="main-content">
            <div class="card">
                <div class="card-header">
                    <span class="card-title">Welcome</span>
                </div>
                <p style="color: var(--text-muted);">
                    Select a quick action above to manage products, view deals, or configure price watches.
                </p>
            </div>
        </div>
    """

    extra_css = """
        .product-item { padding: 12px; border: 1px solid var(--border); border-radius: 6px; margin-bottom: 8px; }
        .product-name { font-weight: 600; font-size: 15px; margin-bottom: 4px; }
        .product-meta { font-size: 12px; color: var(--text-muted); }
        .price { font-weight: 600; color: var(--success); }
        .deal-badge { background: #fef3c7; color: #92400e; padding: 4px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; }
    """

    extra_js = """
        let userContextId = null;

        async function getUserContext() {
            if (userContextId) return userContextId;
            try {
                const res = await fetch('/platformadmin/price-tracker/me/context');
                const data = await res.json();
                userContextId = data.context_id;
                return userContextId;
            } catch (e) {
                console.error('Failed to get user context:', e);
                return null;
            }
        }

        async function showProductsView() {
            const contentEl = document.getElementById('main-content');
            contentEl.innerHTML = '<div class="loading">Loading products...</div>';

            const contextId = await getUserContext();
            if (!contextId) {
                contentEl.innerHTML = '<div style="color: var(--error);">No context found for user</div>';
                return;
            }

            try {
                const res = await fetch(`/platformadmin/price-tracker/products?context_id=${contextId}`);
                const products = await res.json();

                if (products.length === 0) {
                    contentEl.innerHTML = '<div class="card"><p style="color: var(--text-muted);">No products tracked yet</p></div>';
                    return;
                }

                contentEl.innerHTML = '<div class="card"><div class="card-header"><span class="card-title">Tracked Products</span></div>' +
                    products.map(p => `
                        <div class="product-item">
                            <div class="product-name">${escapeHtml(p.name)}</div>
                            <div class="product-meta">
                                Brand: ${escapeHtml(p.brand || 'N/A')} | Category: ${escapeHtml(p.category || 'N/A')}
                            </div>
                            ${p.stores.map(s => `
                                <div style="margin-top: 8px; font-size: 12px;">
                                    ${escapeHtml(s.store_name)}:
                                    ${s.price_sek ? `<span class="price">${s.price_sek} SEK</span>` : 'No price'}
                                    ${s.in_stock === false ? '<span style="color: var(--error); margin-left: 8px;">Out of stock</span>' : ''}
                                </div>
                            `).join('')}
                        </div>
                    `).join('') + '</div>';
            } catch (e) {
                contentEl.innerHTML = '<div style="color: var(--error);">Failed to load products</div>';
            }
        }

        async function showDealsView() {
            const contentEl = document.getElementById('main-content');
            contentEl.innerHTML = '<div class="loading">Loading current deals...</div>';

            try {
                const res = await fetch('/platformadmin/price-tracker/deals');
                const deals = await res.json();

                if (deals.length === 0) {
                    contentEl.innerHTML = '<div class="card"><p style="color: var(--text-muted);">No current deals available</p></div>';
                    return;
                }

                contentEl.innerHTML = '<div class="card"><div class="card-header"><span class="card-title">Current Deals</span></div>' +
                    deals.map(d => `
                        <div class="product-item">
                            <div class="product-name">${escapeHtml(d.product_name)}</div>
                            <div class="product-meta">
                                ${escapeHtml(d.store_name)} | ${d.offer_type}
                            </div>
                            <div style="margin-top: 8px;">
                                ${d.price_sek ? `<span style="text-decoration: line-through; color: var(--text-muted);">${d.price_sek} SEK</span>` : ''}
                                <span class="price" style="margin-left: 8px;">${d.offer_price_sek} SEK</span>
                                ${d.discount_percent > 0 ? `<span class="deal-badge" style="margin-left: 8px;">-${d.discount_percent.toFixed(0)}%</span>` : ''}
                            </div>
                        </div>
                    `).join('') + '</div>';
            } catch (e) {
                contentEl.innerHTML = '<div style="color: var(--error);">Failed to load deals</div>';
            }
        }

        async function showWatchesView() {
            const contentEl = document.getElementById('main-content');
            contentEl.innerHTML = '<div class="loading">Loading price watches...</div>';

            const contextId = await getUserContext();
            if (!contextId) {
                contentEl.innerHTML = '<div style="color: var(--error);">No context found for user</div>';
                return;
            }

            try {
                const res = await fetch(`/platformadmin/price-tracker/watches?context_id=${contextId}`);
                const watches = await res.json();

                if (watches.length === 0) {
                    contentEl.innerHTML = '<div class="card"><p style="color: var(--text-muted);">No price watches configured</p></div>';
                    return;
                }

                contentEl.innerHTML = '<div class="card"><div class="card-header"><span class="card-title">My Price Watches</span></div>' +
                    watches.map(w => `
                        <div class="product-item">
                            <div class="product-name">${escapeHtml(w.product_name)}</div>
                            <div class="product-meta">
                                Target: ${w.target_price_sek ? `${w.target_price_sek} SEK` : 'Any offer'}
                                ${w.alert_on_any_offer ? ' | Alert on any offer' : ''}
                            </div>
                            <div style="margin-top: 4px; font-size: 12px; color: var(--text-muted);">
                                Email: ${escapeHtml(w.email_address)}
                            </div>
                        </div>
                    `).join('') + '</div>';
            } catch (e) {
                contentEl.innerHTML = '<div style="color: var(--error);">Failed to load watches</div>';
            }
        }

        async function showStoresView() {
            const contentEl = document.getElementById('main-content');
            contentEl.innerHTML = '<div class="loading">Loading stores...</div>';

            try {
                const res = await fetch('/platformadmin/price-tracker/stores');
                const stores = await res.json();

                if (stores.length === 0) {
                    contentEl.innerHTML = '<div class="card"><p style="color: var(--text-muted);">No stores configured</p></div>';
                    return;
                }

                contentEl.innerHTML = '<div class="card"><div class="card-header"><span class="card-title">Configured Stores</span></div>' +
                    stores.map(s => `
                        <div class="product-item">
                            <div class="product-name">${escapeHtml(s.name)}</div>
                            <div class="product-meta">
                                Type: ${escapeHtml(s.store_type)} | Slug: ${escapeHtml(s.slug)}
                            </div>
                            <div style="margin-top: 4px; font-size: 12px; color: var(--text-muted);">
                                ${escapeHtml(s.base_url)}
                            </div>
                        </div>
                    `).join('') + '</div>';
            } catch (e) {
                contentEl.innerHTML = '<div style="color: var(--error);">Failed to load stores</div>';
            }
        }

        function escapeHtml(str) {
            if (!str) return '';
            const div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        }

        // Auto-load products on page load
        showProductsView();
    """

    return render_admin_page(
        title="Price Tracker",
        active_page="/platformadmin/price-tracker/",
        content=content,
        user_name=admin.display_name or admin.email.split("@")[0],
        user_email=admin.email,
        breadcrumbs=[("Price Tracker", "#")],
        extra_css=extra_css,
        extra_js=extra_js,
    )
