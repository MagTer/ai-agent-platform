# ruff: noqa: E501, RUF005
"""Admin API endpoints for price tracker module."""

from __future__ import annotations

import logging
import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.user_service import get_user_default_context
from core.db.engine import AsyncSessionLocal, get_db
from core.providers import get_fetcher
from interfaces.http.admin_auth import AdminUser, require_admin_or_redirect, verify_admin_user
from interfaces.http.admin_shared import UTF8HTMLResponse, render_admin_page
from interfaces.http.schemas.price_tracker import (
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
from modules.price_tracker.models import PriceWatch, Product, ProductStore, Store
from modules.price_tracker.parser import PriceParser
from modules.price_tracker.service import PriceTrackerService

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
                stmt = stmt.join(ProductStore, Product.id == ProductStore.product_id)
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
                    package_size=product.package_size,
                    package_quantity=(
                        float(product.package_quantity) if product.package_quantity else None
                    ),
                    stores=stores_data,
                )
            )

        return product_responses
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception("Failed to list products")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/products", status_code=201)
async def create_product(
    data: ProductCreate,
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
    service: PriceTrackerService = Depends(get_price_tracker_service),
) -> dict[str, str]:
    """Create a new product to track.

    Products are scoped to the authenticated user's context for multi-tenancy.
    Different package sizes should be created as separate products.

    Args:
        data: Product creation data.
        admin: Authenticated admin user.
        session: Database session.
        service: Price tracker service.

    Returns:
        Dictionary with product_id and success message.

    Security:
        Requires admin role via Entra ID authentication.
    """
    try:
        from decimal import Decimal

        # Validate package_quantity if provided
        if data.package_quantity is not None and data.package_quantity <= 0:
            raise HTTPException(status_code=400, detail="package_quantity must be positive")

        context_uuid = uuid.UUID(data.context_id)
        package_qty = Decimal(str(data.package_quantity)) if data.package_quantity else None

        product = await service.create_product(
            context_id=context_uuid,
            name=data.name,
            brand=data.brand,
            category=data.category,
            unit=data.unit,
            package_size=data.package_size,
            package_quantity=package_qty,
        )
        return {"product_id": str(product.id), "message": "Product created successfully"}
    except HTTPException:
        raise
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
            package_size=product.package_size,
            package_quantity=(
                float(product.package_quantity) if product.package_quantity else None
            ),
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
        if data.package_size is not None:
            product.package_size = data.package_size if data.package_size else None
        if data.package_quantity is not None:
            from decimal import Decimal

            if data.package_quantity <= 0:
                raise HTTPException(status_code=400, detail="package_quantity must be positive")
            product.package_quantity = Decimal(str(data.package_quantity))

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


@router.get("/", response_class=UTF8HTMLResponse)
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
                <button class="btn btn-primary" onclick="showCreateProductModal()">+ Add Product</button>
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
        .modal-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 1000; }
        .modal { background: var(--bg-primary); border-radius: 8px; padding: 24px; max-width: 500px; width: 90%; max-height: 80vh; overflow-y: auto; }
        .modal-header { font-weight: 600; font-size: 18px; margin-bottom: 16px; }
        .modal label { display: block; margin-top: 12px; margin-bottom: 4px; font-size: 13px; font-weight: 500; }
        .modal input, .modal select, .modal textarea { width: 100%; padding: 8px; border: 1px solid var(--border); border-radius: 4px; font-family: inherit; }
        .modal-actions { margin-top: 20px; display: flex; gap: 8px; justify-content: flex-end; }
        .store-link { display: flex; justify-content: space-between; align-items: center; padding: 8px; background: var(--bg-secondary); border-radius: 4px; margin-top: 8px; font-size: 12px; }
        .btn-sm { padding: 4px 8px; font-size: 12px; }
        .btn-danger { background: var(--error); color: white; }
        .btn-danger:hover { background: #dc2626; }
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

        // Modal infrastructure
        function showModal({title, content, onSubmit}) {
            const overlay = document.createElement('div');
            overlay.className = 'modal-overlay';
            overlay.onclick = (e) => { if (e.target === overlay) closeModal(); };

            overlay.innerHTML = `
                <div class="modal">
                    <div class="modal-header">${escapeHtml(title)}</div>
                    <form id="modal-form">
                        ${content}
                        <div class="modal-actions">
                            <button type="button" class="btn" onclick="closeModal()">Cancel</button>
                            <button type="submit" class="btn btn-primary">Save</button>
                        </div>
                    </form>
                </div>
            `;

            document.body.appendChild(overlay);

            document.getElementById('modal-form').onsubmit = (e) => {
                e.preventDefault();
                onSubmit();
            };
        }

        function closeModal() {
            document.querySelector('.modal-overlay')?.remove();
        }

        // Product Management
        async function showCreateProductModal() {
            showModal({
                title: 'Add New Product',
                content: `
                    <label>Name *</label>
                    <input type="text" id="prod-name" required>
                    <label>Brand</label>
                    <input type="text" id="prod-brand">
                    <label>Category</label>
                    <input type="text" id="prod-category">
                    <label>Unit (e.g., "kg", "st", "liter")</label>
                    <input type="text" id="prod-unit">
                    <label>Package Size (e.g., "500g", "1L")</label>
                    <input type="text" id="prod-package-size">
                    <label>Package Quantity</label>
                    <input type="number" id="prod-package-qty" step="0.01">
                `,
                onSubmit: createProduct
            });
        }

        async function createProduct() {
            const contextId = await getUserContext();
            const data = {
                name: document.getElementById('prod-name').value,
                brand: document.getElementById('prod-brand').value || null,
                category: document.getElementById('prod-category').value || null,
                unit: document.getElementById('prod-unit').value || null,
                package_size: document.getElementById('prod-package-size').value || null,
                package_quantity: parseFloat(document.getElementById('prod-package-qty').value) || null,
                context_id: contextId
            };
            await fetch('/platformadmin/price-tracker/products', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
            closeModal();
            showProductsView();
        }

        async function editProduct(productId) {
            const res = await fetch(`/platformadmin/price-tracker/products/${productId}`);
            const product = await res.json();

            showModal({
                title: 'Edit Product',
                content: `
                    <label>Name *</label>
                    <input type="text" id="prod-name" value="${escapeHtml(product.name)}" required>
                    <label>Brand</label>
                    <input type="text" id="prod-brand" value="${escapeHtml(product.brand || '')}">
                    <label>Category</label>
                    <input type="text" id="prod-category" value="${escapeHtml(product.category || '')}">
                    <label>Unit</label>
                    <input type="text" id="prod-unit" value="${escapeHtml(product.unit || '')}">
                    <label>Package Size</label>
                    <input type="text" id="prod-package-size" value="${escapeHtml(product.package_size || '')}">
                    <label>Package Quantity</label>
                    <input type="number" id="prod-package-qty" step="0.01" value="${product.package_quantity || ''}">
                `,
                onSubmit: () => updateProduct(productId)
            });
        }

        async function updateProduct(productId) {
            const data = {
                name: document.getElementById('prod-name').value,
                brand: document.getElementById('prod-brand').value || null,
                category: document.getElementById('prod-category').value || null,
                unit: document.getElementById('prod-unit').value || null,
                package_size: document.getElementById('prod-package-size').value || null,
                package_quantity: parseFloat(document.getElementById('prod-package-qty').value) || null
            };
            await fetch(`/platformadmin/price-tracker/products/${productId}`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
            closeModal();
            showProductsView();
        }

        async function deleteProduct(id, name) {
            if (!confirm(`Delete product "${name}" and all its price history?`)) return;
            await fetch(`/platformadmin/price-tracker/products/${id}`, {method: 'DELETE'});
            showProductsView();
        }

        // Store Link Management
        async function showLinkStoreModal(productId) {
            const storesRes = await fetch('/platformadmin/price-tracker/stores');
            const stores = await storesRes.json();

            showModal({
                title: 'Link Product to Store',
                content: `
                    <label>Store *</label>
                    <select id="link-store-id" required>
                        ${stores.map(s => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join('')}
                    </select>
                    <label>Product URL *</label>
                    <input type="url" id="link-url" placeholder="https://..." required>
                    <label>Check Frequency</label>
                    <select id="link-freq">
                        <option value="72">Every 3 days</option>
                        <option value="96">Every 4 days</option>
                        <option value="120">Every 5 days</option>
                        <option value="168" selected>Every 7 days (weekly)</option>
                        <option value="240">Every 10 days</option>
                    </select>
                    <label>Check on Specific Weekday (for weekly offers)</label>
                    <select id="link-weekday">
                        <option value="">Use frequency</option>
                        <option value="0">Monday (ICA/Willys offers)</option>
                        <option value="1">Tuesday</option>
                        <option value="2">Wednesday</option>
                        <option value="3">Thursday</option>
                        <option value="4">Friday</option>
                        <option value="5">Saturday</option>
                        <option value="6">Sunday</option>
                    </select>
                    <p style="font-size: 12px; color: var(--text-muted); margin-top: 8px;">
                        Weekday checks run between 06:00-12:00 to catch morning offer updates.
                    </p>
                `,
                onSubmit: () => linkProductToStore(productId)
            });
        }

        async function linkProductToStore(productId) {
            const weekday = document.getElementById('link-weekday').value;
            const data = {
                store_id: document.getElementById('link-store-id').value,
                store_url: document.getElementById('link-url').value,
                check_frequency_hours: parseInt(document.getElementById('link-freq').value),
                check_weekday: weekday ? parseInt(weekday) : null
            };
            await fetch(`/platformadmin/price-tracker/products/${productId}/stores`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
            closeModal();
            showProductsView();
        }

        async function showFrequencyModal(productId, storeId, currentFreq, currentWeekday) {
            showModal({
                title: 'Edit Check Schedule',
                content: `
                    <label>Check Frequency</label>
                    <select id="edit-freq">
                        <option value="72" ${currentFreq === 72 ? 'selected' : ''}>Every 3 days</option>
                        <option value="96" ${currentFreq === 96 ? 'selected' : ''}>Every 4 days</option>
                        <option value="120" ${currentFreq === 120 ? 'selected' : ''}>Every 5 days</option>
                        <option value="168" ${currentFreq === 168 ? 'selected' : ''}>Every 7 days (weekly)</option>
                        <option value="240" ${currentFreq === 240 ? 'selected' : ''}>Every 10 days</option>
                    </select>
                    <label>Check on Specific Weekday</label>
                    <select id="edit-weekday">
                        <option value="" ${currentWeekday === null ? 'selected' : ''}>Use frequency</option>
                        <option value="0" ${currentWeekday === 0 ? 'selected' : ''}>Monday (ICA/Willys offers)</option>
                        <option value="1" ${currentWeekday === 1 ? 'selected' : ''}>Tuesday</option>
                        <option value="2" ${currentWeekday === 2 ? 'selected' : ''}>Wednesday</option>
                        <option value="3" ${currentWeekday === 3 ? 'selected' : ''}>Thursday</option>
                        <option value="4" ${currentWeekday === 4 ? 'selected' : ''}>Friday</option>
                        <option value="5" ${currentWeekday === 5 ? 'selected' : ''}>Saturday</option>
                        <option value="6" ${currentWeekday === 6 ? 'selected' : ''}>Sunday</option>
                    </select>
                    <p style="font-size: 12px; color: var(--text-muted); margin-top: 8px;">
                        Monday is recommended for ICA/Willys weekly offers.
                    </p>
                `,
                onSubmit: () => updateFrequency(productId, storeId)
            });
        }

        async function updateFrequency(productId, storeId) {
            const weekday = document.getElementById('edit-weekday').value;
            const data = {
                check_frequency_hours: parseInt(document.getElementById('edit-freq').value),
                check_weekday: weekday ? parseInt(weekday) : null
            };
            await fetch(`/platformadmin/price-tracker/products/${productId}/stores/${storeId}/frequency`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
            closeModal();
            showProductsView();
        }

        async function unlinkProductFromStore(productId, storeId, storeName) {
            if (!confirm(`Unlink from ${storeName}?`)) return;
            await fetch(`/platformadmin/price-tracker/products/${productId}/stores/${storeId}`, {
                method: 'DELETE'
            });
            showProductsView();
        }

        async function triggerPriceCheck(productStoreId) {
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = 'Checking...';

            try {
                const res = await fetch(`/platformadmin/price-tracker/check/${productStoreId}`, {
                    method: 'POST'
                });
                const result = await res.json();
                if (result.price_sek) {
                    alert(`Price: ${result.price_sek} SEK${result.offer_price_sek ? ` (Offer: ${result.offer_price_sek} SEK)` : ''}`);
                } else {
                    alert(result.message || 'Price extraction failed');
                }
            } catch (e) {
                alert('Failed to check price');
            }

            btn.disabled = false;
            btn.textContent = 'Check Now';
            showProductsView();
        }

        // Watch Management
        async function showCreateWatchModal(productId, productName) {
            showModal({
                title: `Create Watch for ${escapeHtml(productName)}`,
                content: `
                    <label>Email for Alerts *</label>
                    <input type="email" id="watch-email" required>

                    <label>Target Price (SEK)</label>
                    <input type="number" id="watch-target" step="0.01" placeholder="Alert when price drops below">

                    <label style="display: flex; align-items: center; margin-top: 8px;">
                        <input type="checkbox" id="watch-any-offer" style="width: auto; margin-right: 8px;">
                        Alert on any offer
                    </label>

                    <label>Price Drop Threshold (%)</label>
                    <input type="number" id="watch-drop" placeholder="e.g., 20 for 20% off">

                    <label>Unit Price Target (SEK/unit)</label>
                    <input type="number" id="watch-unit-target" step="0.01">

                    <label>Unit Price Drop Threshold (%)</label>
                    <input type="number" id="watch-unit-drop">
                `,
                onSubmit: () => createWatch(productId)
            });
        }

        async function createWatch(productId) {
            const contextId = await getUserContext();
            const data = {
                product_id: productId,
                email_address: document.getElementById('watch-email').value,
                target_price_sek: parseFloat(document.getElementById('watch-target').value) || null,
                alert_on_any_offer: document.getElementById('watch-any-offer').checked,
                price_drop_threshold_percent: parseFloat(document.getElementById('watch-drop').value) || null,
                unit_price_target_sek: parseFloat(document.getElementById('watch-unit-target').value) || null,
                unit_price_drop_threshold_percent: parseFloat(document.getElementById('watch-unit-drop').value) || null
            };
            await fetch(`/platformadmin/price-tracker/watches?context_id=${contextId}`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
            closeModal();
            showWatchesView();
        }

        async function editWatch(watchId) {
            const contextId = await getUserContext();
            const res = await fetch(`/platformadmin/price-tracker/watches?context_id=${contextId}`);
            const watches = await res.json();
            const watch = watches.find(w => w.watch_id === watchId);

            if (!watch) {
                alert('Watch not found');
                return;
            }

            showModal({
                title: `Edit Watch for ${escapeHtml(watch.product_name)}`,
                content: `
                    <label>Email for Alerts *</label>
                    <input type="email" id="watch-email" value="${escapeHtml(watch.email_address)}" required>

                    <label>Target Price (SEK)</label>
                    <input type="number" id="watch-target" step="0.01" value="${watch.target_price_sek || ''}">

                    <label style="display: flex; align-items: center; margin-top: 8px;">
                        <input type="checkbox" id="watch-any-offer" ${watch.alert_on_any_offer ? 'checked' : ''} style="width: auto; margin-right: 8px;">
                        Alert on any offer
                    </label>

                    <label>Price Drop Threshold (%)</label>
                    <input type="number" id="watch-drop" value="${watch.price_drop_threshold_percent || ''}">

                    <label>Unit Price Target (SEK/unit)</label>
                    <input type="number" id="watch-unit-target" step="0.01" value="${watch.unit_price_target_sek || ''}">

                    <label>Unit Price Drop Threshold (%)</label>
                    <input type="number" id="watch-unit-drop" value="${watch.unit_price_drop_threshold_percent || ''}">
                `,
                onSubmit: () => updateWatch(watchId)
            });
        }

        async function updateWatch(watchId) {
            const data = {
                email_address: document.getElementById('watch-email').value,
                target_price_sek: parseFloat(document.getElementById('watch-target').value) || null,
                alert_on_any_offer: document.getElementById('watch-any-offer').checked,
                price_drop_threshold_percent: parseFloat(document.getElementById('watch-drop').value) || null,
                unit_price_target_sek: parseFloat(document.getElementById('watch-unit-target').value) || null,
                unit_price_drop_threshold_percent: parseFloat(document.getElementById('watch-unit-drop').value) || null
            };
            await fetch(`/platformadmin/price-tracker/watches/${watchId}`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
            closeModal();
            showWatchesView();
        }

        async function deleteWatch(watchId, productName) {
            if (!confirm(`Delete watch for "${productName}"?`)) return;
            await fetch(`/platformadmin/price-tracker/watches/${watchId}`, {method: 'DELETE'});
            showWatchesView();
        }

        // Price History Chart
        async function showPriceHistory(productId, productName) {
            const contentEl = document.getElementById('main-content');
            contentEl.innerHTML = '<div class="loading">Loading price history...</div>';

            const res = await fetch(`/platformadmin/price-tracker/products/${productId}/prices?days=90`);
            const prices = await res.json();

            if (prices.length === 0) {
                contentEl.innerHTML = '<div class="card"><p>No price history available</p><button class="btn btn-sm" onclick="showProductsView()">Back</button></div>';
                return;
            }

            const byStore = {};
            prices.forEach(p => {
                if (!byStore[p.store_name]) byStore[p.store_name] = [];
                byStore[p.store_name].push({
                    x: new Date(p.checked_at),
                    y: p.offer_price_sek || p.price_sek
                });
            });

            contentEl.innerHTML = `
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">Price History: ${escapeHtml(productName)}</span>
                        <button class="btn btn-sm" onclick="showProductsView()">Back</button>
                    </div>
                    <canvas id="price-chart" height="300"></canvas>
                </div>
            `;

            const datasets = Object.entries(byStore).map(([store, data], i) => ({
                label: store,
                data: data.sort((a, b) => a.x - b.x),
                borderColor: ['#3b82f6', '#10b981', '#f59e0b', '#ef4444'][i % 4],
                fill: false
            }));

            new Chart(document.getElementById('price-chart'), {
                type: 'line',
                data: {datasets},
                options: {
                    scales: {
                        x: {type: 'time', time: {unit: 'day'}},
                        y: {beginAtZero: false, title: {display: true, text: 'SEK'}}
                    }
                }
            });
        }

        // Views
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
                    contentEl.innerHTML = '<div class="card"><p style="color: var(--text-muted);">No products tracked yet. Click "Add Product" to get started.</p></div>';
                    return;
                }

                contentEl.innerHTML = '<div class="card"><div class="card-header"><span class="card-title">Tracked Products</span></div>' +
                    products.map(p => `
                        <div class="product-item">
                            <div class="product-name">${escapeHtml(p.name)}</div>
                            <div class="product-meta">
                                Brand: ${escapeHtml(p.brand || 'N/A')} | Category: ${escapeHtml(p.category || 'N/A')}
                            </div>
                            <div style="margin-top: 8px;">
                                <button class="btn btn-sm" onclick="editProduct('${p.id}')">Edit</button>
                                <button class="btn btn-sm" onclick="showLinkStoreModal('${p.id}')">+ Link Store</button>
                                <button class="btn btn-sm" onclick="showCreateWatchModal('${p.id}', '${escapeHtml(p.name)}')">+ Watch</button>
                                <button class="btn btn-sm" onclick="showPriceHistory('${p.id}', '${escapeHtml(p.name)}')">History</button>
                                <button class="btn btn-sm btn-danger" onclick="deleteProduct('${p.id}', '${escapeHtml(p.name)}')">Delete</button>
                            </div>
                            ${p.stores.map(s => `
                                <div class="store-link">
                                    <div>
                                        <strong>${escapeHtml(s.store_name)}</strong>
                                        ${s.price_sek ? `<span class="price" style="margin-left: 8px;">${s.price_sek} SEK</span>` : '<span style="color: var(--text-muted); margin-left: 8px;">No price</span>'}
                                        ${s.in_stock === false ? '<span style="color: var(--error); margin-left: 8px;">Out of stock</span>' : ''}
                                        <div style="font-size: 11px; color: var(--text-muted); margin-top: 2px;">
                                            Check: ${s.check_weekday !== null ? ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][s.check_weekday] : `Every ${s.check_frequency_hours}h`}
                                        </div>
                                    </div>
                                    <div style="display: flex; gap: 4px;">
                                        <button class="btn btn-sm" onclick="triggerPriceCheck('${s.product_store_id}')">Check Now</button>
                                        <button class="btn btn-sm" onclick="showFrequencyModal('${p.id}', '${s.store_id}', ${s.check_frequency_hours}, ${s.check_weekday})">Edit</button>
                                        <button class="btn btn-sm btn-danger" onclick="unlinkProductFromStore('${p.id}', '${s.store_id}', '${escapeHtml(s.store_name)}')">Unlink</button>
                                    </div>
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
                    contentEl.innerHTML = '<div class="card"><p style="color: var(--text-muted);">No price watches configured. Add a product first, then create a watch.</p></div>';
                    return;
                }

                contentEl.innerHTML = '<div class="card"><div class="card-header"><span class="card-title">My Price Watches</span></div>' +
                    watches.map(w => `
                        <div class="product-item">
                            <div class="product-name">${escapeHtml(w.product_name)}</div>
                            <div class="product-meta">
                                Target: ${w.target_price_sek ? `${w.target_price_sek} SEK` : 'Any offer'}
                                ${w.alert_on_any_offer ? ' | Alert on any offer' : ''}
                                ${w.price_drop_threshold_percent ? ` | Drop threshold: ${w.price_drop_threshold_percent}%` : ''}
                            </div>
                            <div style="margin-top: 4px; font-size: 12px; color: var(--text-muted);">
                                Email: ${escapeHtml(w.email_address)}
                            </div>
                            <div style="margin-top: 8px;">
                                <button class="btn btn-sm" onclick="editWatch('${w.watch_id}')">Edit</button>
                                <button class="btn btn-sm btn-danger" onclick="deleteWatch('${w.watch_id}', '${escapeHtml(w.product_name)}')">Delete</button>
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
