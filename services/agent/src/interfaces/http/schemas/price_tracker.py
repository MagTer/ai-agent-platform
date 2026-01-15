"""Pydantic schemas for price tracker API."""

from decimal import Decimal

from pydantic import BaseModel


class ProductCreate(BaseModel):
    """Schema for creating a new product."""

    name: str
    brand: str | None = None
    category: str | None = None
    unit: str | None = None


class ProductStoreLink(BaseModel):
    """Schema for linking a product to a store."""

    store_id: str
    store_url: str
    check_frequency_hours: int = 24


class PriceWatchCreate(BaseModel):
    """Schema for creating a price watch."""

    product_id: str
    target_price_sek: Decimal | None = None
    alert_on_any_offer: bool = False
    email_address: str


class StoreResponse(BaseModel):
    """Schema for store data response."""

    id: str
    name: str
    slug: str
    store_type: str
    base_url: str
    is_active: bool


class ProductResponse(BaseModel):
    """Schema for product data response with linked stores."""

    id: str
    name: str
    brand: str | None
    category: str | None
    unit: str | None
    stores: list[dict[str, str | int | None]]


class PricePointResponse(BaseModel):
    """Schema for a single price point in history."""

    checked_at: str
    store_name: str
    store_slug: str
    price_sek: float | None
    unit_price_sek: float | None
    offer_price_sek: float | None
    offer_type: str | None
    offer_details: str | None
    in_stock: bool


class DealResponse(BaseModel):
    """Schema for current deals/offers."""

    product_id: str
    product_name: str
    store_name: str
    store_slug: str
    price_sek: float | None
    offer_price_sek: float
    offer_type: str
    offer_details: str | None


__all__ = [
    "ProductCreate",
    "ProductStoreLink",
    "PriceWatchCreate",
    "StoreResponse",
    "ProductResponse",
    "PricePointResponse",
    "DealResponse",
]
