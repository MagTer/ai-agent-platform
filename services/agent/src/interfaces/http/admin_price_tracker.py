# ruff: noqa: E501
"""Admin API endpoints for price tracker module."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import AsyncSessionLocal, get_db
from core.providers import get_fetcher
from modules.price_tracker.models import PriceWatch, Product, ProductStore, Store
from modules.price_tracker.parser import PriceParser
from modules.price_tracker.service import PriceTrackerService

from .admin_auth import verify_admin_api_key
from .schemas.price_tracker import (
    DealResponse,
    PricePointResponse,
    PriceWatchCreate,
    ProductCreate,
    ProductResponse,
    ProductStoreLink,
    StoreResponse,
)

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/price-tracker",
    tags=["admin", "price-tracker"],
    dependencies=[Depends(verify_admin_api_key)],
)


def get_price_tracker_service() -> PriceTrackerService:
    """Get PriceTrackerService instance."""
    return PriceTrackerService(AsyncSessionLocal)


@router.get("/stores", response_model=list[StoreResponse])
async def list_stores(
    session: AsyncSession = Depends(get_db),
) -> list[StoreResponse]:
    """List all configured stores.

    Returns:
        List of store information including slug, type, and status.

    Security:
        Requires admin API key via X-API-Key header.
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
    session: AsyncSession = Depends(get_db),
) -> list[ProductResponse]:
    """List products with optional search/filter.

    Args:
        search: Search term for product name or brand.
        store_id: Filter by specific store UUID.
        session: Database session.

    Returns:
        List of products with linked stores.

    Security:
        Requires admin API key via X-API-Key header.
    """
    try:
        stmt = select(Product)

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
                stmt = (
                    stmt.join(ProductStore, Product.id == ProductStore.product_id)
                    .where(ProductStore.store_id == store_uuid)
                    .distinct()
                )
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

            stores_data: list[dict[str, str | int | None]] = [
                {
                    "store_id": str(ps.store_id),
                    "store_name": store.name,
                    "store_slug": store.slug,
                    "store_url": ps.store_url,
                    "check_frequency_hours": ps.check_frequency_hours,
                }
                for ps, store in ps_rows
            ]

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


@router.post("/products", status_code=201)
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
        Requires admin API key via X-API-Key header.
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


@router.get("/products/{product_id}", response_model=ProductResponse)
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
        Requires admin API key via X-API-Key header.
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

        stores_data: list[dict[str, str | int | None]] = [
            {
                "store_id": str(ps.store_id),
                "store_name": store.name,
                "store_slug": store.slug,
                "store_url": ps.store_url,
                "check_frequency_hours": ps.check_frequency_hours,
            }
            for ps, store in ps_rows
        ]

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


@router.post("/products/{product_id}/stores", status_code=201)
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
        Requires admin API key via X-API-Key header.
    """
    try:
        product_store = await service.link_product_store(
            product_id=product_id,
            store_id=data.store_id,
            store_url=data.store_url,
        )
        return {
            "product_store_id": str(product_store.id),
            "message": "Product linked to store successfully",
        }
    except Exception as e:
        LOGGER.exception(f"Failed to link product {product_id} to store {data.store_id}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/products/{product_id}/stores/{store_id}")
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
        Requires admin API key via X-API-Key header.
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


@router.get("/products/{product_id}/prices", response_model=list[PricePointResponse])
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
        Requires admin API key via X-API-Key header.
    """
    try:
        product_uuid = uuid.UUID(product_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid product_id format") from e

    try:
        from datetime import timedelta

        from modules.price_tracker.models import PricePoint

        cutoff_date = datetime.now(UTC) - timedelta(days=days)

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


@router.post("/check/{product_store_id}")
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
        Requires admin API key via X-API-Key header.
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


@router.get("/deals", response_model=list[DealResponse])
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
        Requires admin API key via X-API-Key header.
    """
    try:
        from datetime import timedelta

        from modules.price_tracker.models import PricePoint

        # Get deals from last 24 hours
        cutoff = datetime.now(UTC) - timedelta(days=1)

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
        deals: list[DealResponse] = []

        for price_point, product, store in rows:
            key = (product.id, store.id)
            if key in seen:
                continue
            seen.add(key)

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
                )
            )

        return deals
    except Exception as e:
        LOGGER.exception("Failed to get current deals")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/watches")
async def list_watches(
    context_id: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """List price watches, optionally filtered by context.

    Args:
        context_id: Filter by context UUID. Optional.
        session: Database session.

    Returns:
        List of price watch configurations.

    Security:
        Requires admin API key via X-API-Key header.
    """
    try:
        stmt = select(PriceWatch, Product).join(Product, PriceWatch.product_id == Product.id)

        if context_id:
            try:
                context_uuid = uuid.UUID(context_id)
                stmt = stmt.where(PriceWatch.context_id == context_uuid)
            except ValueError as e:
                raise HTTPException(status_code=400, detail="Invalid context_id format") from e

        stmt = stmt.where(PriceWatch.is_active.is_(True)).order_by(PriceWatch.created_at.desc())

        result = await session.execute(stmt)
        rows = result.all()

        watches_list: list[dict[str, Any]] = []
        for watch, product in rows:
            target_price = float(watch.target_price_sek) if watch.target_price_sek else None
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


@router.post("/watches", status_code=201)
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
        Requires admin API key via X-API-Key header.
    """
    try:
        watch = await service.create_watch(
            context_id=context_id,
            product_id=data.product_id,
            email=data.email_address,
            target_price=data.target_price_sek,
            alert_on_any_offer=data.alert_on_any_offer,
            price_drop_threshold_percent=data.price_drop_threshold_percent,
        )
        return {"watch_id": str(watch.id), "message": "Price watch created successfully"}
    except Exception as e:
        LOGGER.exception("Failed to create price watch")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/watches/{watch_id}")
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
        Requires admin API key via X-API-Key header.
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


@router.get("/", response_class=HTMLResponse)
async def price_tracker_dashboard() -> str:
    """Server-rendered admin dashboard for price tracking.

    Returns:
        HTML dashboard with Swedish UI for managing products, deals, and watches.

    Security:
        Requires admin API key stored in localStorage or cookie.
    """
    return """<!DOCTYPE html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Prisspaning - Admin Dashboard</title>
    <style>
        :root {
            --primary: #2563eb;
            --bg: #f3f4f6;
            --white: #fff;
            --border: #e5e7eb;
            --text: #1f2937;
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
    </style>
</head>
<body>
    <div class="header">
        <div class="brand">Prisspaning</div>
        <div class="tab-nav">
            <div class="nav-item active" onclick="switchTab('products')">Produkter</div>
            <div class="nav-item" onclick="switchTab('deals')">Erbjudanden</div>
            <div class="nav-item" onclick="switchTab('watches')">Bevakningar</div>
        </div>
    </div>

    <div class="container">
        <!-- Products Screen -->
        <div class="screen active" id="screen-products">
            <div class="section-header">
                <input type="text" id="searchProducts" class="search-box" placeholder="Sok produkter...">
                <button class="btn btn-primary" onclick="showModal('addProduct')">+ Ny produkt</button>
            </div>
            <div class="grid" id="productGrid">
                <div class="loading">Laddar produkter...</div>
            </div>
        </div>

        <!-- Deals Screen -->
        <div class="screen" id="screen-deals">
            <div class="section-header">
                <div class="section-title">Aktuella erbjudanden</div>
            </div>
            <div class="filters">
                <button class="filter-btn active" onclick="filterDeals(null)">Alla</button>
                <button class="filter-btn" onclick="filterDeals('grocery')">Matvaror</button>
                <button class="filter-btn" onclick="filterDeals('pharmacy')">Apotek</button>
            </div>
            <div class="grid" id="dealsGrid">
                <div class="loading">Laddar erbjudanden...</div>
            </div>
        </div>

        <!-- Watches Screen -->
        <div class="screen" id="screen-watches">
            <div class="section-header">
                <div class="section-title">Prisbevakning</div>
                <button class="btn btn-primary" onclick="showModal('addWatch')">+ Ny bevakning</button>
            </div>
            <div id="watchesList">
                <div class="loading">Laddar bevakningar...</div>
            </div>
        </div>
    </div>

    <!-- Add Product Modal -->
    <div class="modal" id="modal-addProduct">
        <div class="modal-content">
            <div class="modal-title">Lagg till produkt</div>
            <div id="addProductError"></div>
            <div class="form-group">
                <label class="form-label">Produktnamn *</label>
                <input type="text" id="newProductName" class="form-input" required>
            </div>
            <div class="form-group">
                <label class="form-label">Varumarke</label>
                <input type="text" id="newProductBrand" class="form-input">
            </div>
            <div class="form-group">
                <label class="form-label">Kategori</label>
                <input type="text" id="newProductCategory" class="form-input">
            </div>
            <div class="form-group">
                <label class="form-label">Enhet</label>
                <input type="text" id="newProductUnit" class="form-input" placeholder="st, kg, l...">
            </div>
            <div class="form-group">
                <label class="form-label">Butik</label>
                <select id="newProductStore" class="form-select"></select>
            </div>
            <div class="form-group">
                <label class="form-label">URL till produkt</label>
                <input type="url" id="newProductUrl" class="form-input">
            </div>
            <div class="btn-group">
                <button class="btn btn-primary" onclick="createProduct()">Skapa</button>
                <button class="btn btn-secondary" onclick="hideModal('addProduct')">Avbryt</button>
            </div>
        </div>
    </div>

    <!-- Add Store Link Modal -->
    <div class="modal" id="modal-addStore">
        <div class="modal-content">
            <div class="modal-title">Lagg till butik</div>
            <div id="addStoreError"></div>
            <input type="hidden" id="linkProductId">
            <div class="form-group">
                <label class="form-label">Butik *</label>
                <select id="linkStoreId" class="form-select"></select>
            </div>
            <div class="form-group">
                <label class="form-label">URL *</label>
                <input type="url" id="linkStoreUrl" class="form-input" required>
            </div>
            <div class="form-group">
                <label class="form-label">Kontrollfrekvens (timmar)</label>
                <input type="number" id="linkStoreFrequency" class="form-input" value="24" min="1">
            </div>
            <div class="btn-group">
                <button class="btn btn-primary" onclick="linkStore()">Lagg till</button>
                <button class="btn btn-secondary" onclick="hideModal('addStore')">Avbryt</button>
            </div>
        </div>
    </div>

    <!-- Add Watch Modal -->
    <div class="modal" id="modal-addWatch">
        <div class="modal-content">
            <div class="modal-title">Ny prisbevakning</div>
            <div id="addWatchError"></div>
            <div class="form-group">
                <label class="form-label">Produkt *</label>
                <select id="watchProductId" class="form-select"></select>
            </div>
            <div class="form-group">
                <label class="form-label">Malpris (SEK)</label>
                <input type="number" id="watchTargetPrice" class="form-input" step="0.01" placeholder="Valfritt">
            </div>
            <div class="form-group">
                <label class="form-label">Notifiera vid prisfall (%)</label>
                <input type="number" id="watchPriceDropPercent" class="form-input" min="1" max="100" placeholder="t.ex. 15">
                <div style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">
                    Larma nar priset sjunker med minst denna procent fran ordinarie pris
                </div>
            </div>
            <div class="form-group">
                <label class="form-checkbox">
                    <input type="checkbox" id="watchAlertAny">
                    <span>Notifiera vid alla erbjudanden</span>
                </label>
            </div>
            <div class="form-group">
                <label class="form-label">E-postadress *</label>
                <input type="email" id="watchEmail" class="form-input" required>
            </div>
            <div class="btn-group">
                <button class="btn btn-primary" onclick="createWatch()">Skapa bevakning</button>
                <button class="btn btn-secondary" onclick="hideModal('addWatch')">Avbryt</button>
            </div>
        </div>
    </div>

    <script>
        const API_KEY = localStorage.getItem('admin_api_key') || '';
        const BASE_URL = '/admin/price-tracker';
        let stores = [];
        let products = [];
        let deals = [];
        let watches = [];
        let currentDealFilter = null;

        async function apiRequest(path, options = {}) {
            const headers = {
                'X-API-Key': API_KEY,
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
                products = await apiRequest('/products');

                if (products.length === 0) {
                    grid.innerHTML = '<div class="empty-state">Inga produkter tillagda an. Klicka pa "+ Ny produkt" for att komma igang.</div>';
                    return;
                }

                grid.innerHTML = products.map(p => `
                    <div class="card">
                        <div class="card-title">${escapeHtml(p.name)}</div>
                        <div class="card-meta">
                            ${p.brand ? escapeHtml(p.brand) : ''}
                            ${p.category ? '&middot; ' + escapeHtml(p.category) : ''}
                            ${p.unit ? '&middot; ' + escapeHtml(p.unit) : ''}
                        </div>
                        <div class="store-pills">
                            ${p.stores.map(s => `
                                <div class="pill">
                                    <span>${escapeHtml(s.store_name)}</span>
                                    <button class="btn btn-sm btn-secondary" onclick="triggerCheck('${s.store_id}', '${p.id}')">Kolla pris</button>
                                </div>
                            `).join('')}
                        </div>
                        <button class="btn btn-sm btn-secondary" onclick="showAddStoreModal('${p.id}')">Lagg till butik</button>
                    </div>
                `).join('');
            } catch (e) {
                grid.innerHTML = `<div class="error-msg">Misslyckades att ladda produkter: ${e.message}</div>`;
            }
        }

        async function loadDeals() {
            const grid = document.getElementById('dealsGrid');
            try {
                const path = currentDealFilter ? `/deals?store_type=${currentDealFilter}` : '/deals';
                deals = await apiRequest(path);

                if (deals.length === 0) {
                    grid.innerHTML = '<div class="empty-state">Inga aktuella erbjudanden hittades.</div>';
                    return;
                }

                grid.innerHTML = deals.map(d => `
                    <div class="card">
                        <div class="card-title">${escapeHtml(d.product_name)}</div>
                        <div class="card-meta">${escapeHtml(d.store_name)}</div>
                        <div style="margin: 12px 0;">
                            ${d.price_sek ? `<span class="price-old">${d.price_sek.toFixed(2)} kr</span>` : ''}
                            <span class="price">${d.offer_price_sek.toFixed(2)} kr</span>
                        </div>
                        <span class="badge badge-success">${escapeHtml(d.offer_type)}</span>
                        ${d.offer_details ? `<div style="margin-top: 8px; font-size: 12px; color: var(--text-muted)">${escapeHtml(d.offer_details)}</div>` : ''}
                    </div>
                `).join('');
            } catch (e) {
                grid.innerHTML = `<div class="error-msg">Misslyckades att ladda erbjudanden: ${e.message}</div>`;
            }
        }

        async function loadWatches() {
            const list = document.getElementById('watchesList');
            try {
                watches = await apiRequest('/watches');

                if (watches.length === 0) {
                    list.innerHTML = '<div class="empty-state">Inga aktiva bevakningar. Klicka pa "+ Ny bevakning" for att skapa en.</div>';
                    return;
                }

                list.innerHTML = watches.map(w => {
                    const conditions = [];
                    if (w.target_price_sek) conditions.push(`Malpris: ${w.target_price_sek} kr`);
                    if (w.price_drop_threshold_percent) conditions.push(`Prisfall: ${w.price_drop_threshold_percent}%`);
                    if (w.alert_on_any_offer) conditions.push('Alla erbjudanden');
                    const conditionText = conditions.length > 0 ? conditions.join(' &middot; ') : 'Inga villkor';

                    return `
                    <div class="watch-item">
                        <div>
                            <div style="font-weight: 600;">${escapeHtml(w.product_name)}</div>
                            <div style="font-size: 12px; color: var(--text-muted); margin-top: 4px;">
                                ${conditionText}
                                &middot; ${escapeHtml(w.email_address)}
                            </div>
                        </div>
                        <button class="btn btn-sm btn-secondary" onclick="deleteWatch('${w.watch_id}')">Ta bort</button>
                    </div>
                    `;
                }).join('');
            } catch (e) {
                list.innerHTML = `<div class="error-msg">Misslyckades att ladda bevakningar: ${e.message}</div>`;
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

                if (storeUrl && storeId) {
                    await apiRequest(`/products/${result.product_id}/stores`, {
                        method: 'POST',
                        body: JSON.stringify({ store_id: storeId, store_url: storeUrl })
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

            if (!storeUrl) return;

            try {
                await apiRequest(`/products/${productId}/stores`, {
                    method: 'POST',
                    body: JSON.stringify({ store_id: storeId, store_url: storeUrl })
                });

                hideModal('addStore');
                await loadProducts();
            } catch (e) {
                document.getElementById('addStoreError').innerHTML = `<div class="error-msg">${e.message}</div>`;
            }
        }

        async function triggerCheck(storeId, productId) {
            const product = products.find(p => p.id === productId);
            if (!product) return;

            const store = product.stores.find(s => s.store_id === storeId);
            if (!store) return;

            try {
                const productStore = product.stores.find(s => s.store_id === storeId);
                await apiRequest(`/check/${productStore.product_store_id}`, { method: 'POST' });
                alert('Priskontroll genomford!');
                await loadProducts();
            } catch (e) {
                alert('Priskontroll misslyckades: ' + e.message);
            }
        }

        async function createWatch() {
            const productId = document.getElementById('watchProductId').value;
            const email = document.getElementById('watchEmail').value.trim();
            const targetPrice = document.getElementById('watchTargetPrice').value;
            const priceDropPercent = document.getElementById('watchPriceDropPercent').value;
            const alertAny = document.getElementById('watchAlertAny').checked;

            if (!productId || !email) return;

            const data = {
                product_id: productId,
                email_address: email,
                target_price_sek: targetPrice ? parseFloat(targetPrice) : null,
                price_drop_threshold_percent: priceDropPercent ? parseInt(priceDropPercent) : null,
                alert_on_any_offer: alertAny
            };

            try {
                await apiRequest('/watches?context_id=00000000-0000-0000-0000-000000000000', {
                    method: 'POST',
                    body: JSON.stringify(data)
                });

                hideModal('addWatch');
                await loadWatches();
            } catch (e) {
                document.getElementById('addWatchError').innerHTML = `<div class="error-msg">${e.message}</div>`;
            }
        }

        async function deleteWatch(watchId) {
            if (!confirm('Ar du saker pa att du vill ta bort denna bevakning?')) return;

            try {
                await apiRequest(`/watches/${watchId}`, { method: 'DELETE' });
                await loadWatches();
            } catch (e) {
                alert('Misslyckades att ta bort bevakning: ' + e.message);
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
            modal.classList.add('open');

            if (name === 'addWatch') {
                const select = document.getElementById('watchProductId');
                select.innerHTML = products.map(p =>
                    `<option value="${p.id}">${escapeHtml(p.name)}</option>`
                ).join('');
            }
        }

        function hideModal(name) {
            document.getElementById(`modal-${name}`).classList.remove('open');
            document.querySelectorAll(`#modal-${name} .error-msg`).forEach(el => el.remove());
        }

        function escapeHtml(str) {
            if (!str) return '';
            const div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        }

        document.getElementById('searchProducts').addEventListener('input', (e) => {
            const query = e.target.value.toLowerCase();
            document.querySelectorAll('#productGrid .card').forEach(card => {
                const text = card.textContent.toLowerCase();
                card.style.display = text.includes(query) ? 'block' : 'none';
            });
        });

        loadStores();
        loadProducts();

        setInterval(() => {
            const activeTab = document.querySelector('.screen.active').id;
            if (activeTab === 'screen-deals') loadDeals();
        }, 60000);
    </script>
</body>
</html>"""


__all__ = ["router"]
