# Price Tracker GUI Restoration Plan

**Created:** 2026-01-26
**Author:** Claude Opus 4.5
**Status:** Ready for implementation

---

## Summary

The Price Tracker admin GUI lost functionality during a previous git reset. The backend API is complete but the frontend dashboard only shows read-only views. This plan restores all CRUD functionality.

---

## Current State Analysis

### Backend (Complete)

All API endpoints exist and are functional:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/me/context` | GET | Get user's context ID |
| `/stores` | GET | List configured stores |
| `/products` | GET | List products with filters |
| `/products` | POST | Create new product |
| `/products/{id}` | GET/PUT/DELETE | Single product CRUD |
| `/products/{id}/stores` | POST | Link product to store |
| `/products/{id}/stores/{sid}/frequency` | PUT | Update check schedule |
| `/products/{id}/stores/{sid}` | DELETE | Unlink from store |
| `/products/{id}/prices` | GET | Get price history |
| `/check/{ps_id}` | POST | Manual price check |
| `/deals` | GET | Current deals/offers |
| `/watches` | GET/POST | List/create watches |
| `/watches/{id}` | PUT/DELETE | Update/delete watch |

**Backend Validation (verified working):**
- `check_frequency_hours`: 72-240 (3-10 days)
- `check_weekday`: 0=Monday to 6=Sunday
- Morning spread: 06:00-12:00 with random minute
- Jitter: +/-10% on frequency-based scheduling
- Rate limiting: 5s delay between same-store requests
- Batch size: 10 items per 5-min scheduler cycle

### Frontend (Missing Features)

Current GUI only has:
- 4 "Quick Actions" buttons (read-only views)
- Products list (read-only)
- Deals list (read-only)
- Watches list (read-only)
- Stores list (read-only)

**Missing:**
1. Product creation form
2. Product edit modal
3. Product delete confirmation
4. Store link form (add URL)
5. Store link edit/delete
6. Frequency/weekday editor modal
7. Manual price check button
8. Price history chart
9. Watch creation form
10. Watch edit modal
11. Watch delete button

---

## Implementation Plan

### Phase 1: Product Management (Priority High)

**File:** `admin_price_tracker.py` (modify existing `extra_js`)

#### 1.1 Add Product Creation Form

Add button in quick actions and modal form:

```javascript
// Add to Quick Actions div
<button class="btn btn-primary" onclick="showCreateProductModal()">+ Add Product</button>

// Modal for creating product
function showCreateProductModal() {
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
```

#### 1.2 Add Product Edit/Delete

Add actions to product items:

```javascript
// In product item template:
<div style="margin-top: 8px;">
    <button class="btn btn-sm" onclick="editProduct('${p.id}')">Edit</button>
    <button class="btn btn-sm btn-danger" onclick="deleteProduct('${p.id}', '${escapeHtml(p.name)}')">Delete</button>
</div>

async function deleteProduct(id, name) {
    if (!confirm(`Delete product "${name}" and all its price history?`)) return;
    await fetch(`/platformadmin/price-tracker/products/${id}`, {method: 'DELETE'});
    showProductsView();
}
```

#### 1.3 Add Store Link Form

Add "Link to Store" button per product:

```javascript
async function showLinkStoreModal(productId) {
    const storesRes = await fetch('/platformadmin/price-tracker/stores');
    const stores = await storesRes.json();

    showModal({
        title: 'Link Product to Store',
        content: `
            <label>Store *</label>
            <select id="link-store-id">
                ${stores.map(s => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join('')}
            </select>
            <label>Product URL *</label>
            <input type="url" id="link-url" placeholder="https://...">
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
                <option value="0">Monday</option>
                <option value="1">Tuesday</option>
                <option value="2">Wednesday</option>
                <option value="3">Thursday</option>
                <option value="4">Friday</option>
                <option value="5">Saturday</option>
                <option value="6">Sunday</option>
            </select>
            <p style="font-size: 12px; color: var(--text-muted);">
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
```

### Phase 2: Schedule Management (Priority High)

#### 2.1 Frequency Editor Modal

Edit check frequency/weekday per store link:

```javascript
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
```

#### 2.2 Manual Price Check Button

Add per-store trigger:

```javascript
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
```

### Phase 3: Watch Management (Priority Medium)

#### 3.1 Watch Creation Form

```javascript
async function showCreateWatchModal(productId, productName) {
    showModal({
        title: `Create Watch for ${escapeHtml(productName)}`,
        content: `
            <label>Email for Alerts *</label>
            <input type="email" id="watch-email" required>

            <label>Target Price (SEK)</label>
            <input type="number" id="watch-target" step="0.01" placeholder="Alert when price drops below">

            <label>
                <input type="checkbox" id="watch-any-offer"> Alert on any offer
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
```

#### 3.2 Watch Edit/Delete

```javascript
async function editWatch(watchId) {
    // Fetch current watch data and show edit modal
    // Similar to create but pre-fills fields
}

async function deleteWatch(watchId, productName) {
    if (!confirm(`Delete watch for "${productName}"?`)) return;
    await fetch(`/platformadmin/price-tracker/watches/${watchId}`, {method: 'DELETE'});
    showWatchesView();
}
```

### Phase 4: Price History Visualization (Priority Medium)

#### 4.1 Price Trend Chart

Use Chart.js (already available via CDN):

```javascript
async function showPriceHistory(productId, productName) {
    const contentEl = document.getElementById('main-content');
    contentEl.innerHTML = '<div class="loading">Loading price history...</div>';

    const res = await fetch(`/platformadmin/price-tracker/products/${productId}/prices?days=90`);
    const prices = await res.json();

    if (prices.length === 0) {
        contentEl.innerHTML = '<div class="card"><p>No price history available</p></div>';
        return;
    }

    // Group by store
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
```

### Phase 5: Modal Infrastructure (Required First)

Add shared modal component to `extra_css` and `extra_js`:

```css
.modal-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 1000; }
.modal { background: var(--bg-primary); border-radius: 8px; padding: 24px; max-width: 500px; width: 90%; max-height: 80vh; overflow-y: auto; }
.modal-header { font-weight: 600; font-size: 18px; margin-bottom: 16px; }
.modal label { display: block; margin-top: 12px; margin-bottom: 4px; font-size: 13px; font-weight: 500; }
.modal input, .modal select { width: 100%; padding: 8px; border: 1px solid var(--border); border-radius: 4px; }
.modal-actions { margin-top: 20px; display: flex; gap: 8px; justify-content: flex-end; }
```

```javascript
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
```

---

## Implementation Order

1. **Phase 5 first** - Modal infrastructure (foundation)
2. **Phase 1** - Product management (core functionality)
3. **Phase 2** - Schedule management (user's priority)
4. **Phase 3** - Watch management (alerts)
5. **Phase 4** - Price charts (nice-to-have)

---

## Testing Checklist

- [ ] Create product with all fields
- [ ] Edit product name/brand
- [ ] Delete product with confirmation
- [ ] Link product to ICA with Monday weekday check
- [ ] Verify next_check_at shows morning time on Monday
- [ ] Edit frequency from weekly to every 3 days
- [ ] Trigger manual price check
- [ ] Create watch with target price
- [ ] Create watch with "any offer" alert
- [ ] Edit watch email
- [ ] Delete watch
- [ ] View 90-day price history chart
- [ ] Verify multi-store chart shows separate lines

---

## Files to Modify

| File | Changes |
|------|---------|
| `admin_price_tracker.py` | Add modal CSS, add all JS functions, update product/watch views with action buttons |

**No new files needed** - all changes in existing dashboard endpoint.

---

## Estimated Changes

- ~300 lines new JavaScript
- ~50 lines new CSS
- Modifications to existing view functions

---

## Notes

- Backend already validates 72-240 hour frequency range
- Backend already handles weekday scheduling with 06:00-12:00 spread
- Rate limiting (5s) and batch size (10) are scheduler-side, not GUI-configurable
- Chart.js available via admin_shared.py CDN includes
