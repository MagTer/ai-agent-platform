# Price Tracker: Package Size Support

**Created:** 2026-01-24
**Author:** Architect (Opus)
**Status:** Ready for Implementation

---

## Problem Statement

Users cannot link the same product type with different package sizes to the same store.

**Example scenario:**
- User has "Toalettpapper 24-pack" linked to Willys
- User wants to add "Toalettpapper 16-pack" at the same Willys store
- Database constraint error: `UniqueConstraint("product_id", "store_id")`

**Root cause:** The `ProductStore` table has a unique constraint on `(product_id, store_id)`, preventing multiple links between the same product and store.

---

## Solution: Add Package Size Metadata to Product

Make the `Product` model represent a specific SKU including package size. Each package size variant becomes a separate product that can independently link to stores.

### Design Principles

1. **Product = Specific SKU** - "Toalettpapper 24-pack" and "Toalettpapper 16-pack" are separate products
2. **Keep unique constraint** - Maintains data integrity
3. **Enable unit price comparison** - Package quantity enables calculating kr/unit
4. **Minimal schema change** - Two new nullable columns, backward compatible

### How Prisjakt/MatPriskollen Handle This

These services treat each package size as a separate trackable item:
- "Coca-Cola 33cl" and "Coca-Cola 1.5L" are different products
- Unit price (kr/liter) displayed for comparison
- User explicitly chooses which variant to track

We follow the same pattern.

---

## Architecture Compliance

| Layer | Files Modified | Compliance |
|-------|----------------|------------|
| core | None | N/A |
| modules | `modules/price_tracker/models.py` | OK - only imports core |
| modules | `modules/price_tracker/service.py` | OK - only imports core |
| interfaces | `interfaces/http/admin_price_tracker.py` | OK - imports modules |
| interfaces | `interfaces/http/schemas/price_tracker.py` | OK - no imports |

**No architecture violations** - all changes follow layer dependency rules.

---

## Implementation Roadmap

### Phase 1: Database Schema Changes

#### Step 1.1: Create Alembic Migration

**Engineer tasks:**
- Create migration file: `services/agent/alembic/versions/20260124_add_package_size_to_products.py`

**Migration content:**
```python
"""Add package_size and package_quantity to price_tracker_products.

Revision ID: 20260124_package_size
Revises: 20260123_add_context_id_to_products
Create Date: 2026-01-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260124_package_size"
down_revision: str | Sequence[str] | None = "20260123_add_context_id_to_products"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add package size columns to products table."""
    op.add_column(
        "price_tracker_products",
        sa.Column("package_size", sa.String(50), nullable=True),
    )
    op.add_column(
        "price_tracker_products",
        sa.Column("package_quantity", sa.Numeric(10, 2), nullable=True),
    )


def downgrade() -> None:
    """Remove package size columns."""
    op.drop_column("price_tracker_products", "package_quantity")
    op.drop_column("price_tracker_products", "package_size")
```

**File:** `services/agent/alembic/versions/20260124_add_package_size_to_products.py` (create)

---

#### Step 1.2: Update Product ORM Model

**Engineer tasks:**
- Add `package_size` and `package_quantity` columns to Product model

**File:** `services/agent/src/modules/price_tracker/models.py`

**Current code (lines 50-54):**
```python
    name: Mapped[str] = mapped_column(String(255))
    brand: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)  # kg, liter, st, etc.
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
```

**Replace with:**
```python
    name: Mapped[str] = mapped_column(String(255))
    brand: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)  # kg, liter, st, etc.
    package_size: Mapped[str | None] = mapped_column(String(50), nullable=True)  # "24-pack", "500ml", "1kg"
    package_quantity: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)  # 24, 0.5, 1.0
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
```

**QA tasks (after Engineer completes):**
- Run `stack check` to verify no type errors
- Verify model imports are correct

---

### Phase 2: Service Layer Updates

#### Step 2.1: Update create_product Method

**Engineer tasks:**
- Add package_size and package_quantity parameters to `create_product()`

**File:** `services/agent/src/modules/price_tracker/service.py`

**Current method signature (lines 298-305):**
```python
    async def create_product(
        self,
        context_id: str,
        name: str,
        brand: str | None,
        category: str | None,
        unit: str | None,
    ) -> Product:
```

**Replace with:**
```python
    async def create_product(
        self,
        context_id: str,
        name: str,
        brand: str | None,
        category: str | None,
        unit: str | None,
        package_size: str | None = None,
        package_quantity: float | None = None,
    ) -> Product:
```

**Current Product creation (lines 318-325):**
```python
        async with self.session_factory() as session:
            product = Product(
                context_id=uuid.UUID(context_id),
                name=name,
                brand=brand,
                category=category,
                unit=unit,
            )
```

**Replace with:**
```python
        async with self.session_factory() as session:
            product = Product(
                context_id=uuid.UUID(context_id),
                name=name,
                brand=brand,
                category=category,
                unit=unit,
                package_size=package_size,
                package_quantity=package_quantity,
            )
```

**Update docstring to document new parameters.**

**QA tasks (after Engineer completes):**
- Run `stack check`

---

### Phase 3: API Schema Updates

#### Step 3.1: Update ProductCreate Schema

**Engineer tasks:**
- Add package_size and package_quantity to ProductCreate schema

**File:** `services/agent/src/interfaces/http/schemas/price_tracker.py`

**Current ProductCreate (lines 8-14):**
```python
class ProductCreate(BaseModel):
    """Schema for creating a new product."""

    name: str
    brand: str | None = None
    category: str | None = None
    unit: str | None = None
```

**Replace with:**
```python
class ProductCreate(BaseModel):
    """Schema for creating a new product.

    For products with package sizes (e.g., toilet paper, beverages),
    create separate products for each size variant:
    - "Toalettpapper 24-pack" (package_size="24-pack", package_quantity=24)
    - "Toalettpapper 16-pack" (package_size="16-pack", package_quantity=16)

    This enables unit price comparison across package sizes.
    """

    name: str
    brand: str | None = None
    category: str | None = None
    unit: str | None = None
    package_size: str | None = None  # Human-readable: "24-pack", "500ml", "1kg"
    package_quantity: float | None = None  # Numeric value for calculations: 24, 0.5, 1.0
```

---

#### Step 3.2: Update ProductUpdate Schema

**Engineer tasks:**
- Add package_size and package_quantity to ProductUpdate schema

**File:** `services/agent/src/interfaces/http/schemas/price_tracker.py`

**Current ProductUpdate (lines 17-23):**
```python
class ProductUpdate(BaseModel):
    """Schema for updating an existing product."""

    name: str | None = None
    brand: str | None = None
    category: str | None = None
    unit: str | None = None
```

**Replace with:**
```python
class ProductUpdate(BaseModel):
    """Schema for updating an existing product."""

    name: str | None = None
    brand: str | None = None
    category: str | None = None
    unit: str | None = None
    package_size: str | None = None
    package_quantity: float | None = None
```

---

#### Step 3.3: Update ProductResponse Schema

**Engineer tasks:**
- Add package_size and package_quantity to ProductResponse schema

**File:** `services/agent/src/interfaces/http/schemas/price_tracker.py`

**Current ProductResponse (lines 69-77):**
```python
class ProductResponse(BaseModel):
    """Schema for product data response with linked stores."""

    id: str
    name: str
    brand: str | None
    category: str | None
    unit: str | None
    stores: list[dict[str, str | int | float | None]]
```

**Replace with:**
```python
class ProductResponse(BaseModel):
    """Schema for product data response with linked stores."""

    id: str
    name: str
    brand: str | None
    category: str | None
    unit: str | None
    package_size: str | None
    package_quantity: float | None
    stores: list[dict[str, str | int | float | None]]
```

**QA tasks (after Engineer completes):**
- Run `stack check`

---

### Phase 4: API Endpoint Updates

#### Step 4.1: Update create_product Endpoint

**Engineer tasks:**
- Update create_product endpoint to pass package_size and package_quantity

**File:** `services/agent/src/interfaces/http/admin_price_tracker.py`

Find the `create_product` endpoint (search for `@router.post("/products"`) and update the service call to include the new fields:

```python
product = await service.create_product(
    context_id=context_id,
    name=data.name,
    brand=data.brand,
    category=data.category,
    unit=data.unit,
    package_size=data.package_size,
    package_quantity=data.package_quantity,
)
```

---

#### Step 4.2: Update list_products Response

**Engineer tasks:**
- Include package_size and package_quantity in product list response

**File:** `services/agent/src/interfaces/http/admin_price_tracker.py`

Find where `ProductResponse` is constructed in `list_products` endpoint and add:

```python
ProductResponse(
    id=str(product.id),
    name=product.name,
    brand=product.brand,
    category=product.category,
    unit=product.unit,
    package_size=product.package_size,
    package_quantity=float(product.package_quantity) if product.package_quantity else None,
    stores=stores_list,
)
```

---

#### Step 4.3: Update update_product Endpoint

**Engineer tasks:**
- Handle package_size and package_quantity in update endpoint

Find the `update_product` endpoint and add handling for new fields:

```python
if data.package_size is not None:
    product.package_size = data.package_size
if data.package_quantity is not None:
    product.package_quantity = data.package_quantity
```

**QA tasks (after Engineer completes):**
- Run `stack check`

---

### Phase 5: Dashboard UI Updates (Optional Enhancement)

#### Step 5.1: Update Product Form in Dashboard

**Engineer tasks:**
- Add package_size and package_quantity input fields to the product creation form
- Display package info in product list table

**File:** `services/agent/src/interfaces/http/admin_price_tracker.py`

Look for the HTML form that creates products and add fields for:
- Package Size (text input, optional)
- Package Quantity (number input, optional)

Display package_size in the product list table if present.

**QA tasks (after Engineer completes):**
- Run `stack check`
- Manually test the dashboard form

---

### Phase 6: Testing

#### Step 6.1: Update Service Tests

**Engineer tasks:**
- Add test for creating product with package size
- Add test for updating product package size

**File:** `services/agent/src/modules/price_tracker/tests/test_service.py`

Add test cases:

```python
async def test_create_product_with_package_size(
    price_tracker_service: PriceTrackerService,
    test_context: Context,
) -> None:
    """Test creating a product with package size metadata."""
    product = await price_tracker_service.create_product(
        context_id=str(test_context.id),
        name="Toalettpapper 24-pack",
        brand="Lambi",
        category="Hygiene",
        unit="st",
        package_size="24-pack",
        package_quantity=24.0,
    )

    assert product.name == "Toalettpapper 24-pack"
    assert product.package_size == "24-pack"
    assert float(product.package_quantity) == 24.0


async def test_create_multiple_package_sizes_same_store(
    price_tracker_service: PriceTrackerService,
    test_context: Context,
    test_store: Store,
) -> None:
    """Test that different package sizes can link to the same store."""
    # Create two products with different package sizes
    product_24 = await price_tracker_service.create_product(
        context_id=str(test_context.id),
        name="Toalettpapper 24-pack",
        brand="Lambi",
        category="Hygiene",
        unit="st",
        package_size="24-pack",
        package_quantity=24.0,
    )

    product_16 = await price_tracker_service.create_product(
        context_id=str(test_context.id),
        name="Toalettpapper 16-pack",
        brand="Lambi",
        category="Hygiene",
        unit="st",
        package_size="16-pack",
        package_quantity=16.0,
    )

    # Both should be able to link to the same store
    link_24 = await price_tracker_service.link_product_store(
        product_id=str(product_24.id),
        store_id=str(test_store.id),
        store_url="https://willys.se/product/toalettpapper-24-pack",
    )

    link_16 = await price_tracker_service.link_product_store(
        product_id=str(product_16.id),
        store_id=str(test_store.id),
        store_url="https://willys.se/product/toalettpapper-16-pack",
    )

    # Both links should exist without constraint violation
    assert link_24.id != link_16.id
    assert link_24.store_id == link_16.store_id
```

**QA tasks (after Engineer completes):**
- Run `stack check`
- Run `stack test` to verify all tests pass

---

## Agent Delegation

### Engineer (Sonnet) - Implementation
- Create migration file
- Update ORM models
- Update service layer
- Update API schemas
- Update API endpoints
- Update dashboard UI
- Write tests

### QA (Haiku) - Quality Assurance
- Run `stack check` after each phase
- Report test results
- Escalate complex Mypy errors to Engineer

### Cost Optimization
Each implementation step should:
1. Engineer writes/modifies code
2. QA runs quality check: `stack check`
3. QA reports back (or escalates if complex errors)
4. Repeat for next step

---

## Configuration Changes

None required - existing database connection handles new columns automatically.

---

## Security Considerations

1. **Input Validation:** package_size is a free-text field (max 50 chars). Consider sanitizing for display.
2. **Numeric Validation:** package_quantity should be positive if provided.
3. **No sensitive data:** Package metadata is not sensitive.

**Recommendation:** Add validation in the API endpoint:
```python
if data.package_quantity is not None and data.package_quantity <= 0:
    raise HTTPException(status_code=400, detail="package_quantity must be positive")
```

---

## Success Criteria

1. User can create "Toalettpapper 24-pack" with package_size="24-pack" and package_quantity=24
2. User can create "Toalettpapper 16-pack" with package_size="16-pack" and package_quantity=16
3. Both products can link to Willys without constraint error
4. Dashboard displays package size info
5. All tests pass
6. `stack check` passes

---

## Migration Verification

After running migration:

```sql
-- Verify columns exist
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'price_tracker_products'
AND column_name IN ('package_size', 'package_quantity');

-- Test insert with package size
INSERT INTO price_tracker_products (context_id, name, package_size, package_quantity, unit)
VALUES ('your-context-uuid', 'Test Product', '24-pack', 24.0, 'st');
```

---

## Rollback Plan

If issues arise:
1. Run `alembic downgrade -1` to remove columns
2. Revert code changes via git

The migration is backward compatible - existing products without package info continue to work.

---

## Files Affected Summary

| File | Action | Phase |
|------|--------|-------|
| `alembic/versions/20260124_add_package_size_to_products.py` | CREATE | 1 |
| `modules/price_tracker/models.py` | MODIFY | 1 |
| `modules/price_tracker/service.py` | MODIFY | 2 |
| `interfaces/http/schemas/price_tracker.py` | MODIFY | 3 |
| `interfaces/http/admin_price_tracker.py` | MODIFY | 4, 5 |
| `modules/price_tracker/tests/test_service.py` | MODIFY | 6 |

---

## Quick Reference: User Workflow After Implementation

1. User wants to track "Toalettpapper" at Willys in two sizes
2. Create Product 1: "Toalettpapper 24-pack" (package_size="24-pack", package_quantity=24, unit="st")
3. Create Product 2: "Toalettpapper 16-pack" (package_size="16-pack", package_quantity=16, unit="st")
4. Link Product 1 to Willys with URL for 24-pack
5. Link Product 2 to Willys with URL for 16-pack
6. Both products track independently, unit prices can be compared
