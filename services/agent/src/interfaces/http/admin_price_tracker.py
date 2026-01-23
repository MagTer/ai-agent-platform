# ruff: noqa: E501
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
from modules.price_tracker.models import PriceWatch, Product, ProductStore, Store
from modules.price_tracker.parser import PriceParser
from modules.price_tracker.service import PriceTrackerService

from .schemas.price_tracker import (
    DealResponse,
    PricePointResponse,
    PriceWatchCreate,
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
    # Validate frequency range (24 hours to 7 days)
    if not (24 <= data.check_frequency_hours <= 168):
        raise HTTPException(
            status_code=400,
            detail="check_frequency_hours must be between 24 and 168 (inclusive)",
        )

    try:
        product_store = await service.link_product_store(
            product_id=product_id,
            store_id=data.store_id,
            store_url=data.store_url,
            check_frequency_hours=data.check_frequency_hours,
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
    if check_frequency_hours is None:
        raise HTTPException(status_code=400, detail="check_frequency_hours is required")

    # Validate frequency range (24 hours to 7 days)
    if not (24 <= check_frequency_hours <= 168):
        raise HTTPException(
            status_code=400,
            detail="check_frequency_hours must be between 24 and 168 (inclusive)",
        )

    try:
        stmt = select(ProductStore).where(
            ProductStore.product_id == product_uuid, ProductStore.store_id == store_uuid
        )
        result = await session.execute(stmt)
        product_store = result.scalar_one_or_none()

        if not product_store:
            raise HTTPException(status_code=404, detail="Product-store link not found")

        # Update frequency
        product_store.check_frequency_hours = check_frequency_hours

        # Recalculate next_check_at with jitter (same logic as scheduler)
        now_utc = datetime.now(UTC).replace(tzinfo=None)
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


@router.get("/", response_class=HTMLResponse, dependencies=[Depends(require_admin_or_redirect)])
async def price_tracker_dashboard() -> str:
    """Server-rendered admin dashboard for price tracking.

    Returns:
        HTML dashboard for managing products, deals, and price watches.

    Security:
        Requires admin role via Entra ID authentication.
    """
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Price Tracker - Admin Dashboard</title>
    <style>
        :root {
            --primary: #2563eb;
            --bg: #f3f4f6;
            --bg-card: #fff;
            --bg-hover: #f9fafb;
            --white: #fff;
            --border: #e5e7eb;
            --text: #1f2937;
            --text-secondary: #6b7280;
            --text-muted: #6b7280;
            --success: #10b981;
            --error: #ef4444;
            --warning: #f59e0b;
        }

        body {
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            margin: 0;
            background: var(--bg);
            color: var(--text);
        }

        .header {
            background: var(--white);
            border-bottom: 1px solid var(--border);
            padding: 0 20px;
            height: 56px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .brand {
            font-weight: 600;
            font-size: 18px;
        }

        .tab-nav {
            display: flex;
            gap: 24px;
            font-size: 13px;
            font-weight: 500;
        }

        .nav-item {
            cursor: pointer;
            border-bottom: 2px solid transparent;
            color: var(--text-muted);
            padding: 18px 4px;
            transition: all 0.2s;
        }

        .nav-item:hover {
            color: var(--primary);
        }

        .nav-item.active {
            border-bottom-color: var(--primary);
            color: var(--primary);
        }

        .container {
            max-width: 1200px;
            margin: 40px auto;
            padding: 0 20px;
        }

        .screen {
            display: none;
        }

        .screen.active {
            display: block;
        }

        .section-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }

        .section-title {
            font-size: 20px;
            font-weight: 600;
        }

        .btn {
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
            border: none;
            transition: opacity 0.2s;
        }

        .btn:hover {
            opacity: 0.9;
        }

        .btn-primary {
            background: var(--primary);
            color: white;
        }

        .btn-secondary {
            background: var(--white);
            color: var(--text);
            border: 1px solid var(--border);
        }

        .btn-sm {
            padding: 4px 8px;
            font-size: 11px;
        }

        .search-box {
            width: 100%;
            max-width: 400px;
            padding: 8px 12px;
            border: 1px solid var(--border);
            border-radius: 6px;
            font-size: 13px;
        }

        .grid {
            display: grid;
            gap: 16px;
            margin-top: 20px;
        }

        .card {
            background: var(--white);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 20px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.05);
        }

        .card-title {
            font-weight: 600;
            font-size: 15px;
            margin-bottom: 8px;
        }

        .card-meta {
            font-size: 12px;
            color: var(--text-muted);
            margin-bottom: 12px;
        }

        .store-pills {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin: 12px 0;
        }

        .pill {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            background: #f3f4f6;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 500;
        }

        .badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 500;
        }

        .badge-success {
            background: #d1fae5;
            color: #065f46;
        }

        .badge-warning {
            background: #fef3c7;
            color: #92400e;
        }

        .badge-error {
            background: #fee2e2;
            color: #991b1b;
        }

        .badge-ok {
            background: #d1fae5;
            color: #065f46;
        }

        .badge-err {
            background: #fee2e2;
            color: #991b1b;
        }

        .price {
            font-size: 20px;
            font-weight: 700;
            color: var(--success);
        }

        .price-old {
            font-size: 14px;
            text-decoration: line-through;
            color: var(--text-muted);
            margin-right: 8px;
        }

        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.5);
            z-index: 100;
            align-items: center;
            justify-content: center;
        }

        .modal.open {
            display: flex;
        }

        .modal-content {
            background: var(--white);
            border-radius: 8px;
            padding: 24px;
            max-width: 500px;
            width: 90%;
            max-height: 80vh;
            overflow-y: auto;
        }

        .modal-title {
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 20px;
        }

        .form-group {
            margin-bottom: 16px;
        }

        .form-label {
            display: block;
            font-size: 13px;
            font-weight: 500;
            margin-bottom: 6px;
        }

        .form-input, .form-select {
            width: 100%;
            padding: 8px 12px;
            border: 1px solid var(--border);
            border-radius: 6px;
            font-size: 13px;
            box-sizing: border-box;
        }

        .form-checkbox {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 13px;
        }

        .btn-group {
            display: flex;
            gap: 8px;
            margin-top: 20px;
        }

        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: var(--text-muted);
        }

        .error-msg {
            background: #fee2e2;
            color: #991b1b;
            padding: 12px;
            border-radius: 6px;
            font-size: 13px;
            margin-bottom: 16px;
        }

        .filters {
            display: flex;
            gap: 8px;
            margin-bottom: 16px;
        }

        .filter-btn {
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 500;
            cursor: pointer;
            background: var(--white);
            border: 1px solid var(--border);
            transition: all 0.2s;
        }

        .filter-btn.active {
            background: var(--primary);
            color: white;
            border-color: var(--primary);
        }

        .context-info {
            font-size: 12px;
            color: var(--text-muted);
            padding: 8px 12px;
            background: #f9fafb;
            border-radius: 6px;
            margin-bottom: 16px;
        }

        .watch-item {
            background: var(--white);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 16px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }

        .loading {
            text-align: center;
            padding: 40px;
            color: var(--text-muted);
        }

        .toast-container {
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 200;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        .toast {
            padding: 12px 16px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 500;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            animation: slideIn 0.2s ease-out;
            min-width: 300px;
        }

        @keyframes slideIn {
            from {
                transform: translateX(100%);
                opacity: 0;
            }
            to {
                transform: translateX(0);
                opacity: 1;
            }
        }

        .toast-success {
            background: #d1fae5;
            color: #065f46;
        }

        .toast-error {
            background: #fee2e2;
            color: #991b1b;
        }

        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }

        .modal-header h3 {
            margin: 0;
            font-size: 18px;
            font-weight: 600;
        }

        .modal-body {
            overflow-y: auto;
            max-height: 60vh;
        }

        .history-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 12px;
        }

        .history-table th {
            text-align: left;
            padding: 8px;
            background: var(--bg);
            font-weight: 600;
            border-bottom: 2px solid var(--border);
        }

        .history-table td {
            padding: 8px;
            border-bottom: 1px solid var(--border);
        }

        .date-range-buttons {
            display: flex;
            gap: 8px;
            margin-bottom: 16px;
        }

        .date-range-btn {
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 500;
            cursor: pointer;
            background: var(--white);
            border: 1px solid var(--border);
            transition: all 0.2s;
        }

        .date-range-btn.active {
            background: var(--primary);
            color: white;
            border-color: var(--primary);
        }

        .chart-container {
            position: relative;
            height: 400px;
            margin-bottom: 20px;
        }

        .chart-details {
            margin-top: 16px;
            border-top: 1px solid var(--border);
            padding-top: 16px;
        }

        .chart-details summary {
            cursor: pointer;
            font-weight: 500;
            color: var(--primary);
            user-select: none;
            padding: 8px;
            border-radius: 4px;
            transition: all 0.2s;
        }

        .chart-details summary:hover {
            background: var(--bg);
        }

        .chart-details[open] summary {
            color: var(--text);
        }
    </style>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
</head>
<body>
    <!-- Toast Container -->
    <div class="toast-container" id="toastContainer"></div>

    <div class="header">
        <div class="brand">Price Tracker</div>
        <div class="tab-nav">
            <div class="nav-item active" onclick="switchTab('products')">Products</div>
            <div class="nav-item" onclick="switchTab('deals')">Deals</div>
            <div class="nav-item" onclick="switchTab('watches')">Watches</div>
        </div>
    </div>

    <div class="container">
        <!-- Products Screen -->
        <div class="screen active" id="screen-products">
            <div class="context-info" id="contextInfo" style="display: none;">
                <strong>Your Context:</strong> <span id="contextEmail"></span>
            </div>
            <div class="section-header">
                <input type="text" id="searchProducts" class="search-box" placeholder="Search products...">
                <button class="btn btn-primary" onclick="showModal('addProduct')">+ New Product</button>
            </div>
            <div class="grid" id="productGrid">
                <div class="loading">Loading products...</div>
            </div>
        </div>

        <!-- Deals Screen -->
        <div class="screen" id="screen-deals">
            <div class="section-header">
                <div class="section-title">Current Deals</div>
            </div>
            <div class="filters">
                <button class="filter-btn active" onclick="filterDeals(null)">All</button>
                <button class="filter-btn" onclick="filterDeals('grocery')">Grocery</button>
                <button class="filter-btn" onclick="filterDeals('pharmacy')">Pharmacy</button>
            </div>
            <div class="grid" id="dealsGrid">
                <div class="loading">Loading deals...</div>
            </div>
        </div>

        <!-- Watches Screen -->
        <div class="screen" id="screen-watches">
            <div class="section-header">
                <div class="section-title">Price Watches</div>
                <button class="btn btn-primary" onclick="showModal('addWatch')">+ New Watch</button>
            </div>
            <div id="watchesList">
                <div class="loading">Loading watches...</div>
            </div>
        </div>
    </div>

    <!-- Add Product Modal -->
    <div class="modal" id="modal-addProduct">
        <div class="modal-content">
            <div class="modal-title">Add Product</div>
            <div id="addProductError"></div>
            <div class="form-group">
                <label class="form-label">Product Name *</label>
                <input type="text" id="newProductName" class="form-input" required>
            </div>
            <div class="form-group">
                <label class="form-label">Brand</label>
                <input type="text" id="newProductBrand" class="form-input">
            </div>
            <div class="form-group">
                <label class="form-label">Category</label>
                <input type="text" id="newProductCategory" class="form-input">
            </div>
            <div class="form-group">
                <label class="form-label">Unit</label>
                <input type="text" id="newProductUnit" class="form-input" placeholder="pcs, kg, l...">
            </div>
            <div class="form-group">
                <label class="form-label">Store</label>
                <select id="newProductStore" class="form-select"></select>
            </div>
            <div class="form-group">
                <label class="form-label">Product URL</label>
                <input type="url" id="newProductUrl" class="form-input">
            </div>
            <div class="form-group">
                <label class="form-label">Check Frequency</label>
                <select id="newProductFrequency" class="form-select">
                    <option value="24">Daily (24h)</option>
                    <option value="48">Every 2 days (48h)</option>
                    <option value="72">Every 3 days (72h)</option>
                    <option value="168">Weekly (168h)</option>
                </select>
                <div style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">
                    How often to check for price changes
                </div>
            </div>
            <div class="btn-group">
                <button class="btn btn-primary" onclick="createProduct()">Create</button>
                <button class="btn btn-secondary" onclick="hideModal('addProduct')">Cancel</button>
            </div>
        </div>
    </div>

    <!-- Add Store Link Modal -->
    <div class="modal" id="modal-addStore">
        <div class="modal-content">
            <div class="modal-title">Add Store Link</div>
            <div id="addStoreError"></div>
            <input type="hidden" id="linkProductId">
            <div class="form-group">
                <label class="form-label">Store *</label>
                <select id="linkStoreId" class="form-select"></select>
            </div>
            <div class="form-group">
                <label class="form-label">URL *</label>
                <input type="url" id="linkStoreUrl" class="form-input" required>
            </div>
            <div class="form-group">
                <label class="form-label">Check Frequency *</label>
                <select id="linkStoreFrequency" class="form-select">
                    <option value="24">Daily (24h)</option>
                    <option value="48">Every 2 days (48h)</option>
                    <option value="72">Every 3 days (72h)</option>
                    <option value="168">Weekly (168h)</option>
                </select>
                <div style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">
                    Minimum 24 hours, maximum 7 days
                </div>
            </div>
            <div class="btn-group">
                <button class="btn btn-primary" onclick="linkStore()">Add</button>
                <button class="btn btn-secondary" onclick="hideModal('addStore')">Cancel</button>
            </div>
        </div>
    </div>

    <!-- Add Watch Modal -->
    <div class="modal" id="modal-addWatch">
        <div class="modal-content">
            <div class="modal-title">New Price Watch</div>
            <div id="addWatchError"></div>
            <div class="form-group">
                <label class="form-label">Product *</label>
                <select id="watchProductId" class="form-select"></select>
            </div>
            <div class="form-group">
                <label class="form-label">Target Price (SEK)</label>
                <input type="number" id="watchTargetPrice" class="form-input" step="0.01" placeholder="Optional">
            </div>
            <div class="form-group">
                <label class="form-label">Notify on Price Drop (%)</label>
                <input type="number" id="watchPriceDropPercent" class="form-input" min="1" max="100" placeholder="e.g. 15">
                <div style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">
                    Alert when price drops by at least this percentage from regular price
                </div>
            </div>
            <div class="form-group">
                <label class="form-label">Target Unit Price (SEK/unit)</label>
                <input type="number" id="watchUnitPriceTarget" class="form-input" step="0.01" placeholder="e.g. 3.50">
                <div style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">
                    Alert when unit price (SEK/kg, SEK/l, etc) falls below this value
                </div>
            </div>
            <div class="form-group">
                <label class="form-label">Unit Price Drop (%)</label>
                <input type="number" id="watchUnitPriceDropPercent" class="form-input" min="1" max="100" placeholder="e.g. 15">
                <div style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">
                    Alert when unit price drops by at least this percentage
                </div>
            </div>
            <div class="form-group">
                <label class="form-checkbox">
                    <input type="checkbox" id="watchAlertAny">
                    <span>Notify on any offer</span>
                </label>
            </div>
            <div class="form-group">
                <label class="form-label">Email Address *</label>
                <input type="email" id="watchEmail" class="form-input" required>
            </div>
            <div class="btn-group">
                <button class="btn btn-primary" onclick="createWatch()">Create Watch</button>
                <button class="btn btn-secondary" onclick="hideModal('addWatch')">Cancel</button>
            </div>
        </div>
    </div>

    <!-- Price History Modal -->
    <div class="modal" id="modal-priceHistory">
        <div class="modal-content" style="max-width: 900px;">
            <div class="modal-header">
                <h3>Price History</h3>
                <button class="btn" onclick="hideModal('priceHistory')">&times;</button>
            </div>
            <div class="modal-body">
                <div class="date-range-buttons">
                    <button class="date-range-btn" onclick="loadPriceHistory(currentHistoryProductId, 7)">7 days</button>
                    <button class="date-range-btn active" onclick="loadPriceHistory(currentHistoryProductId, 30)">30 days</button>
                    <button class="date-range-btn" onclick="loadPriceHistory(currentHistoryProductId, 90)">90 days</button>
                </div>
                <div id="priceHistoryContent">
                    <div class="loading">Loading price history...</div>
                </div>
            </div>
        </div>
    </div>

    <!-- Edit Product Modal -->
    <div class="modal" id="modal-editProduct">
        <div class="modal-content">
            <div class="modal-title">Edit Product</div>
            <div id="editProductError"></div>
            <input type="hidden" id="editProductId">
            <div class="form-group">
                <label class="form-label">Product Name *</label>
                <input type="text" id="editProductName" class="form-input" required>
            </div>
            <div class="form-group">
                <label class="form-label">Brand</label>
                <input type="text" id="editProductBrand" class="form-input">
            </div>
            <div class="form-group">
                <label class="form-label">Category</label>
                <input type="text" id="editProductCategory" class="form-input">
            </div>
            <div class="form-group">
                <label class="form-label">Unit</label>
                <input type="text" id="editProductUnit" class="form-input" placeholder="pcs, kg, l...">
            </div>
            <div class="btn-group">
                <button class="btn btn-primary" onclick="saveProduct()">Save</button>
                <button class="btn btn-secondary" onclick="hideModal('editProduct')">Cancel</button>
            </div>
        </div>
    </div>

    <!-- Edit Frequency Modal -->
    <div class="modal" id="modal-editFrequency">
        <div class="modal-content">
            <div class="modal-title">Edit Check Frequency</div>
            <div id="editFrequencyError"></div>
            <input type="hidden" id="editFrequencyProductId">
            <input type="hidden" id="editFrequencyStoreId">
            <div class="form-group">
                <label class="form-label">Store</label>
                <div id="editFrequencyStoreName" style="font-weight: 500; color: var(--text);"></div>
            </div>
            <div class="form-group">
                <label class="form-label">Check Frequency</label>
                <select id="editFrequencyValue" class="form-select">
                    <option value="24">Daily (24h)</option>
                    <option value="48">Every 2 days (48h)</option>
                    <option value="72">Every 3 days (72h)</option>
                    <option value="168">Weekly (168h)</option>
                </select>
            </div>
            <div class="btn-group">
                <button class="btn btn-primary" onclick="saveFrequency()">Save</button>
                <button class="btn btn-secondary" onclick="hideModal('editFrequency')">Cancel</button>
            </div>
        </div>
    </div>

    <script>
        const BASE_URL = '/platformadmin/price-tracker';
        let stores = [];
        let products = [];
        let deals = [];
        let watches = [];
        let currentDealFilter = null;
        let currentHistoryProductId = null;
        let searchTimeout = null;
        let userContextId = null;
        let userEmail = null;

        async function apiRequest(path, options = {}) {
            const headers = {
                'Content-Type': 'application/json',
                ...options.headers
            };
            const response = await fetch(BASE_URL + path, { ...options, headers });
            if (!response.ok) {
                const error = await response.text();
                throw new Error(error);
            }
            return response.json();
        }

        function relativeTime(isoString) {
            if (!isoString) return 'Never';
            const date = new Date(isoString);
            const now = new Date();
            const diffMs = now - date;
            const diffMins = Math.floor(diffMs / 60000);
            const diffHours = Math.floor(diffMs / 3600000);
            const diffDays = Math.floor(diffMs / 86400000);

            if (diffMins < 1) return 'Just now';
            if (diffMins < 60) return `${diffMins}min ago`;
            if (diffHours < 24) return `${diffHours}h ago`;
            if (diffDays === 1) return '1 day ago';
            if (diffDays < 30) return `${diffDays} days ago`;
            return date.toLocaleDateString('sv-SE');
        }

        function formatFrequency(hours) {
            if (hours === 24) return 'Daily';
            if (hours === 48) return 'Every 2 days';
            if (hours === 72) return 'Every 3 days';
            if (hours === 168) return 'Weekly';
            if (hours < 24) return `Every ${hours}h`;
            if (hours < 168) return `Every ${Math.round(hours / 24)} days`;
            return `Every ${Math.round(hours / 168)} weeks`;
        }

        function estimateMonthlyChecks(hours) {
            const checksPerDay = 24 / hours;
            const checksPerMonth = Math.round(checksPerDay * 30);
            return checksPerMonth;
        }

        function showToast(message, type = 'success') {
            const container = document.getElementById('toastContainer');
            const toast = document.createElement('div');
            toast.className = `toast toast-${type}`;
            toast.textContent = message;
            container.appendChild(toast);

            setTimeout(() => {
                toast.style.opacity = '0';
                setTimeout(() => toast.remove(), 200);
            }, 3000);
        }

        async function confirmAction(message) {
            return new Promise((resolve) => {
                resolve(confirm(message));
            });
        }

        async function loadUserContext() {
            try {
                const data = await apiRequest('/me/context');
                userContextId = data.context_id;
                userEmail = data.email;

                if (userContextId) {
                    const contextInfo = document.getElementById('contextInfo');
                    const contextEmail = document.getElementById('contextEmail');
                    contextEmail.textContent = userEmail;
                    contextInfo.style.display = 'block';
                }
            } catch (e) {
                console.error('Failed to load user context:', e);
            }
        }

        async function loadStores() {
            try {
                stores = await apiRequest('/stores');
                const storeSelects = ['newProductStore', 'linkStoreId'];
                storeSelects.forEach(id => {
                    const select = document.getElementById(id);
                    select.innerHTML = stores.map(s =>
                        `<option value="${s.id}">${s.name}</option>`
                    ).join('');
                });
            } catch (e) {
                console.error('Failed to load stores:', e);
            }
        }

        async function loadProducts() {
            const grid = document.getElementById('productGrid');
            try {
                // Add context_id parameter if available
                const path = userContextId ? `/products?context_id=${userContextId}` : '/products';
                products = await apiRequest(path);

                if (products.length === 0) {
                    grid.innerHTML = '<div class="empty-state">No products added yet. Click "+ New Product" to get started.</div>';
                    return;
                }

                grid.innerHTML = products.map(p => `
                    <div class="card">
                        <div style="display: flex; justify-content: space-between; align-items: start;">
                            <div class="card-title">${escapeHtml(p.name)}</div>
                            <div style="display: flex; gap: 4px;">
                                <button class="btn btn-sm btn-secondary" onclick="showEditProductModal('${p.id}')" title="Edit product">Edit</button>
                                <button class="btn btn-sm" onclick="deleteProduct('${p.id}')" style="background: var(--error); color: white;" title="Delete product">Delete</button>
                            </div>
                        </div>
                        <div class="card-meta">
                            ${p.brand ? escapeHtml(p.brand) : ''}
                            ${p.category ? '&middot; ' + escapeHtml(p.category) : ''}
                            ${p.unit ? '&middot; ' + escapeHtml(p.unit) : ''}
                        </div>
                        ${p.stores.map(s => `
                            <div style="border-top: 1px solid var(--border); margin-top: 12px; padding-top: 12px;">
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                                    <strong style="font-size: 13px;">${escapeHtml(s.store_name)}</strong>
                                    <div style="display: flex; gap: 4px; align-items: center;">
                                        <button class="btn btn-sm btn-secondary" onclick="triggerCheck('${s.product_store_id}')">Check price</button>
                                        <button class="btn btn-sm" onclick="unlinkStore('${p.id}', '${s.store_id}')" style="background: transparent; color: var(--text-muted); padding: 2px 4px;" title="Remove store link">&times;</button>
                                    </div>
                                </div>
                                <div style="font-size: 12px; color: var(--text-muted); margin-bottom: 4px;">
                                    Last checked: ${relativeTime(s.last_checked_at)}
                                    &middot; Checks ${formatFrequency(s.check_frequency_hours).toLowerCase()} (~${estimateMonthlyChecks(s.check_frequency_hours)}/month)
                                    <button class="btn btn-sm" onclick="showEditFrequencyModal('${p.id}', '${s.store_id}', '${escapeHtml(s.store_name)}', ${s.check_frequency_hours})" style="background: transparent; color: var(--primary); padding: 0 4px; font-size: 11px; margin-left: 4px;" title="Edit frequency">Edit</button>
                                </div>
                                ${s.price_sek ? `
                                    <div style="font-size: 13px; margin-top: 4px;">
                                        Price: <strong>${s.price_sek.toFixed(2)} kr</strong>
                                        ${s.unit_price_sek ? ` (${s.unit_price_sek.toFixed(2)} kr/${p.unit || 'unit'})` : ''}
                                        &middot; ${s.in_stock ? '<span class="badge badge-success">In stock</span>' : '<span class="badge badge-error">Out of stock</span>'}
                                    </div>
                                ` : '<div style="font-size: 12px; color: var(--text-muted);">No price recorded</div>'}
                            </div>
                        `).join('')}
                        <div style="display: flex; gap: 8px; margin-top: 12px;">
                            <button class="btn btn-sm btn-secondary" onclick="showAddStoreModal('${p.id}')">Add store</button>
                            <button class="btn btn-sm btn-secondary" onclick="showPriceHistory('${p.id}')">History</button>
                        </div>
                    </div>
                `).join('');
            } catch (e) {
                grid.innerHTML = `<div class="error-msg">Failed to load products: ${e.message}</div>`;
            }
        }

        async function loadDeals() {
            const grid = document.getElementById('dealsGrid');
            try {
                const path = currentDealFilter ? `/deals?store_type=${currentDealFilter}` : '/deals';
                deals = await apiRequest(path);

                if (deals.length === 0) {
                    grid.innerHTML = '<div class="empty-state">No current deals found.</div>';
                    return;
                }

                grid.innerHTML = deals.map(d => `
                    <div class="card">
                        <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 8px;">
                            <div class="card-title"><a href="${d.product_url}" target="_blank" style="color: inherit; text-decoration: none;">${escapeHtml(d.product_name)}</a></div>
                            ${d.discount_percent > 0 ? `<span class="badge badge-error" style="font-size: 14px;">-${d.discount_percent.toFixed(0)}%</span>` : ''}
                        </div>
                        <div class="card-meta">
                            ${escapeHtml(d.store_name)}
                            &middot; ${relativeTime(d.checked_at)}
                        </div>
                        <div style="margin: 12px 0;">
                            ${d.price_sek ? `<span class="price-old">${d.price_sek.toFixed(2)} kr</span>` : ''}
                            <span class="price">${d.offer_price_sek.toFixed(2)} kr</span>
                        </div>
                        <span class="badge badge-success">${escapeHtml(d.offer_type)}</span>
                        ${d.offer_details ? `<div style="margin-top: 8px; font-size: 12px; color: var(--text-muted)">${escapeHtml(d.offer_details)}</div>` : ''}
                    </div>
                `).join('');
            } catch (e) {
                grid.innerHTML = `<div class="error-msg">Failed to load deals: ${e.message}</div>`;
            }
        }

        async function loadWatches() {
            const list = document.getElementById('watchesList');
            try {
                // Add context_id parameter if available
                const path = userContextId ? `/watches?context_id=${userContextId}` : '/watches';
                watches = await apiRequest(path);

                if (watches.length === 0) {
                    list.innerHTML = '<div class="empty-state">No active watches. Click "+ New Watch" to create one.</div>';
                    return;
                }

                list.innerHTML = watches.map(w => {
                    const conditions = [];
                    if (w.target_price_sek) conditions.push(`Target price: ${w.target_price_sek} kr`);
                    if (w.price_drop_threshold_percent) conditions.push(`Price drop: ${w.price_drop_threshold_percent}%`);
                    if (w.unit_price_target_sek) conditions.push(`Unit price: ${w.unit_price_target_sek} kr`);
                    if (w.unit_price_drop_threshold_percent) conditions.push(`Unit price drop: ${w.unit_price_drop_threshold_percent}%`);
                    if (w.alert_on_any_offer) conditions.push('Any offer');
                    const conditionText = conditions.length > 0 ? conditions.join(' &middot; ') : 'No conditions';
                    const contextShort = w.context_id.substring(0, 8);

                    return `
                    <div class="watch-item">
                        <div>
                            <div style="font-weight: 600;">${escapeHtml(w.product_name)}</div>
                            <div style="font-size: 12px; color: var(--text-muted); margin-top: 4px;">
                                ${conditionText}
                            </div>
                            <div style="font-size: 11px; color: var(--text-muted); margin-top: 6px;">
                                <strong>Email:</strong> ${escapeHtml(w.email_address)}
                                &middot; <strong>Context:</strong> ${contextShort}
                                &middot; <strong>Created:</strong> ${relativeTime(w.created_at)}
                                &middot; <strong>Last notified:</strong> ${relativeTime(w.last_alerted_at)}
                            </div>
                        </div>
                        <button class="btn btn-sm btn-secondary" onclick="deleteWatch('${w.watch_id}')">Delete</button>
                    </div>
                    `;
                }).join('');
            } catch (e) {
                list.innerHTML = `<div class="error-msg">Failed to load watches: ${e.message}</div>`;
            }
        }

        async function createProduct() {
            const name = document.getElementById('newProductName').value.trim();
            if (!name) return;

            const data = {
                name,
                brand: document.getElementById('newProductBrand').value.trim() || null,
                category: document.getElementById('newProductCategory').value.trim() || null,
                unit: document.getElementById('newProductUnit').value.trim() || null
            };

            try {
                const result = await apiRequest('/products', {
                    method: 'POST',
                    body: JSON.stringify(data)
                });

                const storeId = document.getElementById('newProductStore').value;
                const storeUrl = document.getElementById('newProductUrl').value.trim();
                const frequency = parseInt(document.getElementById('newProductFrequency').value) || 24;

                if (storeUrl && storeId) {
                    await apiRequest(`/products/${result.product_id}/stores`, {
                        method: 'POST',
                        body: JSON.stringify({
                            store_id: storeId,
                            store_url: storeUrl,
                            check_frequency_hours: frequency
                        })
                    });
                }

                hideModal('addProduct');
                await loadProducts();
            } catch (e) {
                document.getElementById('addProductError').innerHTML = `<div class="error-msg">${e.message}</div>`;
            }
        }

        function showAddStoreModal(productId) {
            document.getElementById('linkProductId').value = productId;
            showModal('addStore');
        }

        async function linkStore() {
            const productId = document.getElementById('linkProductId').value;
            const storeId = document.getElementById('linkStoreId').value;
            const storeUrl = document.getElementById('linkStoreUrl').value.trim();
            const frequency = parseInt(document.getElementById('linkStoreFrequency').value) || 24;

            if (!storeUrl) return;

            try {
                await apiRequest(`/products/${productId}/stores`, {
                    method: 'POST',
                    body: JSON.stringify({ store_id: storeId, store_url: storeUrl, check_frequency_hours: frequency })
                });

                hideModal('addStore');
                await loadProducts();
            } catch (e) {
                document.getElementById('addStoreError').innerHTML = `<div class="error-msg">${e.message}</div>`;
            }
        }

        async function triggerCheck(productStoreId) {
            try {
                await apiRequest(`/check/${productStoreId}`, { method: 'POST' });
                showToast('Price check completed!', 'success');
                await loadProducts();
            } catch (e) {
                showToast('Price check failed: ' + e.message, 'error');
            }
        }

        function showPriceHistory(productId) {
            currentHistoryProductId = productId;
            showModal('priceHistory');
            loadPriceHistory(productId, 30);
        }

        let priceHistoryChart = null;

        const storeColors = {
            'willys': '#10b981',
            'ica': '#ef4444',
            'apotea': '#3b82f6',
            'med24': '#f59e0b'
        };

        function getStoreColor(storeSlug) {
            return storeColors[storeSlug.toLowerCase()] || '#9ca3af';
        }

        async function loadPriceHistory(productId, days) {
            const content = document.getElementById('priceHistoryContent');

            // Update active button
            document.querySelectorAll('.date-range-btn').forEach(btn => btn.classList.remove('active'));
            event.target.classList.add('active');

            try {
                content.innerHTML = '<div class="loading">Loading price history...</div>';
                const history = await apiRequest(`/products/${productId}/prices?days=${days}`);

                if (history.length === 0) {
                    content.innerHTML = '<div class="empty-state">No price history available.</div>';
                    return;
                }

                // Group history by store_slug
                const groupedByStore = {};
                history.forEach(entry => {
                    const storeSlug = entry.store_slug || entry.store_name.toLowerCase().replace(/\s+/g, '-');
                    if (!groupedByStore[storeSlug]) {
                        groupedByStore[storeSlug] = {
                            store_name: entry.store_name,
                            data: []
                        };
                    }
                    groupedByStore[storeSlug].data.push(entry);
                });

                // Sort data by date for each store
                Object.values(groupedByStore).forEach(store => {
                    store.data.sort((a, b) => new Date(a.checked_at) - new Date(b.checked_at));
                });

                // Prepare Chart.js datasets
                const datasets = Object.entries(groupedByStore).map(([storeSlug, storeData]) => {
                    return {
                        label: storeData.store_name,
                        data: storeData.data.map(entry => ({
                            x: new Date(entry.checked_at),
                            y: entry.price_sek || entry.offer_price_sek
                        })),
                        borderColor: getStoreColor(storeSlug),
                        backgroundColor: getStoreColor(storeSlug) + '10',
                        borderWidth: 2,
                        tension: 0.3,
                        fill: false,
                        pointRadius: 4,
                        pointHoverRadius: 6,
                        pointBackgroundColor: getStoreColor(storeSlug),
                        pointBorderColor: '#fff',
                        pointBorderWidth: 2
                    };
                });

                // Build HTML with chart and collapsible table
                content.innerHTML = `
                    <div class="chart-container">
                        <canvas id="priceHistoryChart"></canvas>
                    </div>
                    <details class="chart-details">
                        <summary>Show raw data</summary>
                        <table class="history-table">
                            <thead>
                                <tr>
                                    <th>Date</th>
                                    <th>Store</th>
                                    <th>Price</th>
                                    <th>Offer</th>
                                    <th>Unit price</th>
                                    <th>Stock</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${history.map(h => {
                                    const date = new Date(h.checked_at);
                                    const formattedDate = date.toLocaleDateString('sv-SE') + ' ' + date.toLocaleTimeString('sv-SE', { hour: '2-digit', minute: '2-digit' });
                                    return `
                                        <tr>
                                            <td>${formattedDate}</td>
                                            <td>${escapeHtml(h.store_name)}</td>
                                            <td>${h.price_sek ? h.price_sek.toFixed(2) + ' kr' : '-'}</td>
                                            <td>${h.offer_price_sek ? h.offer_price_sek.toFixed(2) + ' kr' : '-'}</td>
                                            <td>${h.unit_price_sek ? h.unit_price_sek.toFixed(2) + ' kr' : '-'}</td>
                                            <td><span class="badge ${h.in_stock ? 'badge-ok' : 'badge-err'}">${h.in_stock ? 'Yes' : 'No'}</span></td>
                                        </tr>
                                    `;
                                }).join('')}
                            </tbody>
                        </table>
                    </details>
                `;

                // Create Chart.js instance
                if (priceHistoryChart) {
                    priceHistoryChart.destroy();
                }

                const ctx = document.getElementById('priceHistoryChart');
                if (ctx) {
                    priceHistoryChart = new Chart(ctx, {
                        type: 'line',
                        data: {
                            datasets: datasets
                        },
                        options: {
                            responsive: true,
                            maintainAspectRatio: false,
                            interaction: {
                                intersect: false,
                                mode: 'index'
                            },
                            scales: {
                                x: {
                                    type: 'time',
                                    time: {
                                        unit: 'day',
                                        displayFormats: {
                                            day: 'MMM d'
                                        }
                                    },
                                    title: {
                                        display: true,
                                        text: 'Date'
                                    }
                                },
                                y: {
                                    beginAtZero: false,
                                    title: {
                                        display: true,
                                        text: 'Price (SEK)'
                                    },
                                    ticks: {
                                        callback: function(value) {
                                            return value.toFixed(2) + ' kr';
                                        }
                                    }
                                }
                            },
                            plugins: {
                                legend: {
                                    position: 'top',
                                    labels: {
                                        usePointStyle: true,
                                        padding: 15
                                    }
                                },
                                tooltip: {
                                    callbacks: {
                                        label: function(context) {
                                            return context.dataset.label + ': ' + context.parsed.y.toFixed(2) + ' SEK';
                                        },
                                        title: function(context) {
                                            if (context.length > 0) {
                                                const date = new Date(context[0].label);
                                                return date.toLocaleDateString('sv-SE');
                                            }
                                            return '';
                                        }
                                    }
                                }
                            }
                        }
                    });
                }
            } catch (e) {
                content.innerHTML = `<div class="error-msg">Failed to load price history: ${e.message}</div>`;
            }
        }

        async function createWatch() {
            const productId = document.getElementById('watchProductId').value;
            const email = document.getElementById('watchEmail').value.trim();
            const targetPrice = document.getElementById('watchTargetPrice').value;
            const priceDropPercent = document.getElementById('watchPriceDropPercent').value;
            const unitPriceTarget = document.getElementById('watchUnitPriceTarget').value;
            const unitPriceDropPercent = document.getElementById('watchUnitPriceDropPercent').value;
            const alertAny = document.getElementById('watchAlertAny').checked;

            if (!productId || !email) return;

            const data = {
                product_id: productId,
                email_address: email,
                target_price_sek: targetPrice ? parseFloat(targetPrice) : null,
                price_drop_threshold_percent: priceDropPercent ? parseInt(priceDropPercent) : null,
                unit_price_target_sek: unitPriceTarget ? parseFloat(unitPriceTarget) : null,
                unit_price_drop_threshold_percent: unitPriceDropPercent ? parseInt(unitPriceDropPercent) : null,
                alert_on_any_offer: alertAny
            };

            try {
                // Use user's context_id
                const contextParam = userContextId ? `?context_id=${userContextId}` : '?context_id=00000000-0000-0000-0000-000000000000';
                await apiRequest(`/watches${contextParam}`, {
                    method: 'POST',
                    body: JSON.stringify(data)
                });

                hideModal('addWatch');
                showToast('Price watch created!', 'success');
                await loadWatches();
            } catch (e) {
                document.getElementById('addWatchError').innerHTML = `<div class="error-msg">${e.message}</div>`;
            }
        }

        async function deleteWatch(watchId) {
            const confirmed = await confirmAction('Are you sure you want to delete this watch?');
            if (!confirmed) return;

            try {
                await apiRequest(`/watches/${watchId}`, { method: 'DELETE' });
                showToast('Watch deleted!', 'success');
                await loadWatches();
            } catch (e) {
                showToast('Failed to delete watch: ' + e.message, 'error');
            }
        }

        async function deleteProduct(productId) {
            const confirmed = await confirmAction('Are you sure you want to delete this product? This will also delete all store links and price history.');
            if (!confirmed) return;

            try {
                await apiRequest(`/products/${productId}`, { method: 'DELETE' });
                showToast('Product deleted!', 'success');
                await loadProducts();
            } catch (e) {
                showToast('Failed to delete product: ' + e.message, 'error');
            }
        }

        function showEditProductModal(productId) {
            const product = products.find(p => p.id === productId);
            if (!product) {
                showToast('Product not found', 'error');
                return;
            }

            document.getElementById('editProductId').value = productId;
            document.getElementById('editProductName').value = product.name || '';
            document.getElementById('editProductBrand').value = product.brand || '';
            document.getElementById('editProductCategory').value = product.category || '';
            document.getElementById('editProductUnit').value = product.unit || '';
            document.getElementById('editProductError').innerHTML = '';
            showModal('editProduct');
        }

        async function saveProduct() {
            const productId = document.getElementById('editProductId').value;
            const name = document.getElementById('editProductName').value.trim();

            if (!name) {
                document.getElementById('editProductError').innerHTML = '<div class="error-msg">Product name is required</div>';
                return;
            }

            const data = {
                name,
                brand: document.getElementById('editProductBrand').value.trim() || null,
                category: document.getElementById('editProductCategory').value.trim() || null,
                unit: document.getElementById('editProductUnit').value.trim() || null
            };

            try {
                await apiRequest(`/products/${productId}`, {
                    method: 'PUT',
                    body: JSON.stringify(data)
                });

                hideModal('editProduct');
                showToast('Product updated!', 'success');
                await loadProducts();
            } catch (e) {
                document.getElementById('editProductError').innerHTML = `<div class="error-msg">${e.message}</div>`;
            }
        }

        async function unlinkStore(productId, storeId) {
            const confirmed = await confirmAction('Remove store link?');
            if (!confirmed) return;

            try {
                await apiRequest(`/products/${productId}/stores/${storeId}`, { method: 'DELETE' });
                showToast('Store link removed!', 'success');
                await loadProducts();
            } catch (e) {
                showToast('Failed to remove store link: ' + e.message, 'error');
            }
        }

        function showEditFrequencyModal(productId, storeId, storeName, currentFrequency) {
            document.getElementById('editFrequencyProductId').value = productId;
            document.getElementById('editFrequencyStoreId').value = storeId;
            document.getElementById('editFrequencyStoreName').textContent = storeName;
            document.getElementById('editFrequencyValue').value = currentFrequency;
            document.getElementById('editFrequencyError').innerHTML = '';
            showModal('editFrequency');
        }

        async function saveFrequency() {
            const productId = document.getElementById('editFrequencyProductId').value;
            const storeId = document.getElementById('editFrequencyStoreId').value;
            const frequency = parseInt(document.getElementById('editFrequencyValue').value);

            try {
                await apiRequest(`/products/${productId}/stores/${storeId}/frequency`, {
                    method: 'PUT',
                    body: JSON.stringify({ check_frequency_hours: frequency })
                });

                hideModal('editFrequency');
                showToast('Frequency updated!', 'success');
                await loadProducts();
            } catch (e) {
                document.getElementById('editFrequencyError').innerHTML = `<div class="error-msg">${e.message}</div>`;
            }
        }

        function switchTab(tab) {
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            event.target.classList.add('active');

            document.querySelectorAll('.screen').forEach(el => el.classList.remove('active'));
            document.getElementById(`screen-${tab}`).classList.add('active');

            if (tab === 'products') loadProducts();
            else if (tab === 'deals') loadDeals();
            else if (tab === 'watches') loadWatches();
        }

        function filterDeals(storeType) {
            currentDealFilter = storeType;
            document.querySelectorAll('.filter-btn').forEach(el => el.classList.remove('active'));
            event.target.classList.add('active');
            loadDeals();
        }

        function showModal(name) {
            const modal = document.getElementById(`modal-${name}`);

            // Clear all form inputs
            modal.querySelectorAll('input[type="text"], input[type="email"], input[type="url"], input[type="number"]').forEach(input => {
                input.value = '';
            });

            // Clear all checkboxes
            modal.querySelectorAll('input[type="checkbox"]').forEach(checkbox => {
                checkbox.checked = false;
            });

            // Reset selects to first option
            modal.querySelectorAll('select').forEach(select => {
                select.selectedIndex = 0;
            });

            // Clear error messages
            modal.querySelectorAll('.error-msg').forEach(el => el.remove());
            modal.querySelectorAll('[id$="Error"]').forEach(el => el.innerHTML = '');

            // Special handling for specific modals
            if (name === 'addWatch') {
                const select = document.getElementById('watchProductId');
                select.innerHTML = products.map(p =>
                    `<option value="${p.id}">${escapeHtml(p.name)}</option>`
                ).join('');
            }

            modal.classList.add('open');
        }

        function hideModal(name) {
            document.getElementById(`modal-${name}`).classList.remove('open');
        }

        function escapeHtml(str) {
            if (!str) return '';
            const div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        }

        document.getElementById('searchProducts').addEventListener('input', (e) => {
            const query = e.target.value.trim();

            // Clear previous timeout
            if (searchTimeout) {
                clearTimeout(searchTimeout);
            }

            // Debounce: wait 300ms after user stops typing
            searchTimeout = setTimeout(async () => {
                const grid = document.getElementById('productGrid');

                if (!query) {
                    // If search is empty, load all products
                    await loadProducts();
                    return;
                }

                try {
                    grid.innerHTML = '<div class="loading">Searching...</div>';
                    const contextParam = userContextId ? `&context_id=${userContextId}` : '';
                    products = await apiRequest(`/products?search=${encodeURIComponent(query)}${contextParam}`);

                    if (products.length === 0) {
                        grid.innerHTML = '<div class="empty-state">No products found.</div>';
                        return;
                    }

                    grid.innerHTML = products.map(p => `
                        <div class="card">
                            <div style="display: flex; justify-content: space-between; align-items: start;">
                                <div class="card-title">${escapeHtml(p.name)}</div>
                                <div style="display: flex; gap: 4px;">
                                    <button class="btn btn-sm btn-secondary" onclick="showEditProductModal('${p.id}')" title="Edit product">Edit</button>
                                    <button class="btn btn-sm" onclick="deleteProduct('${p.id}')" style="background: var(--error); color: white;" title="Delete product">Delete</button>
                                </div>
                            </div>
                            <div class="card-meta">
                                ${p.brand ? escapeHtml(p.brand) : ''}
                                ${p.category ? '&middot; ' + escapeHtml(p.category) : ''}
                                ${p.unit ? '&middot; ' + escapeHtml(p.unit) : ''}
                            </div>
                            <div class="store-pills">
                                ${p.stores.map(s => `
                                    <div class="pill">
                                        <span>${escapeHtml(s.store_name)}</span>
                                        <button class="btn btn-sm btn-secondary" onclick="triggerCheck('${s.product_store_id}')">Check price</button>
                                    <button class="btn btn-sm" onclick="unlinkStore('${p.id}', '${s.store_id}')" style="background: transparent; color: var(--text-muted); padding: 2px 4px; margin-left: 4px;" title="Remove store link">&times;</button>
                                    </div>
                                `).join('')}
                            </div>
                            <div style="display: flex; gap: 8px; margin-top: 12px;">
                                <button class="btn btn-sm btn-secondary" onclick="showAddStoreModal('${p.id}')">Add store</button>
                                <button class="btn btn-sm btn-secondary" onclick="showPriceHistory('${p.id}')">History</button>
                            </div>
                        </div>
                    `).join('');
                } catch (e) {
                    grid.innerHTML = `<div class="error-msg">Search failed: ${e.message}</div>`;
                }
            }, 300);
        });

        // Load data on page load
        (async function() {
            await loadUserContext();
            loadStores();
            loadProducts();
        })();

        // Auto-refresh deals every minute
        setInterval(() => {
            const activeTab = document.querySelector('.screen.active').id;
            if (activeTab === 'screen-deals') loadDeals();
        }, 60000);
    </script>
</body>
</html>"""


__all__ = ["router"]
