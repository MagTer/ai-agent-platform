# Codebase Cleanup Plan - Parallel Execution

**Created:** 2026-01-25
**Status:** Ready for execution

---

## Overview

This plan addresses all issues found by the 5 architect agents. Tasks are organized into **independent parallel tracks** that can be executed simultaneously by engineer agents.

---

## Dependency Graph

```
Track 1 (DB Schema) ──────────────────────────────┐
Track 2 (App.py Move) ────────────────────────────┤
Track 3 (Module Dependencies) ────────────────────┼──→ Final: stack check
Track 4 (Core Tool Imports) ──────────────────────┤
Track 5 (API Fixes) ──────────────────────────────┤
Track 6 (Any Types) - Can start after Track 2 ───┘
Track 7 (Relative Imports) - Can start after Track 2
```

**Note:** Tracks 1-5 are fully independent. Tracks 6-7 should wait for Track 2 (app.py move) to avoid merge conflicts.

---

## Track 1: Database Schema Fix (CRITICAL)

**Priority:** Critical
**Estimated files:** 4
**Can run parallel:** Yes (independent)

### Problem
Product model missing 3 columns that exist in migrations:
- `context_id` (UUID, NOT NULL, FK)
- `package_size` (String)
- `package_quantity` (Numeric)

### Tasks

#### 1.1 Update Product Model
**File:** `services/agent/src/modules/price_tracker/models.py`

Add to `Product` class (after `id`, before `name`):
```python
context_id: Mapped[uuid.UUID] = mapped_column(
    ForeignKey("contexts.id", ondelete="CASCADE"), index=True
)
```

Add after `unit`:
```python
package_size: Mapped[str | None] = mapped_column(String(50), nullable=True)
package_quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
```

Required import: `from decimal import Decimal` and `from sqlalchemy import Numeric`

#### 1.2 Update ProductCreate Schema
**File:** `services/agent/src/interfaces/http/schemas/price_tracker.py`

Add to `ProductCreate`:
```python
context_id: str  # Required - UUID as string
package_size: str | None = None
package_quantity: float | None = None
```

#### 1.3 Update PriceTrackerService
**File:** `services/agent/src/modules/price_tracker/service.py`

Update `create_product()` method signature and implementation:
```python
async def create_product(
    self,
    name: str,
    brand: str | None,
    category: str | None,
    unit: str | None,
    context_id: uuid.UUID,  # ADD THIS
    package_size: str | None = None,  # ADD THIS
    package_quantity: Decimal | None = None,  # ADD THIS
) -> Product:
```

Update Product instantiation to include new fields.

#### 1.4 Update API Endpoint
**File:** `services/agent/src/interfaces/http/admin_price_tracker.py`

Update `create_product` endpoint to:
1. Accept `context_id` from request body (or derive from authenticated user)
2. Pass all new fields to service

### Verification
```bash
# After changes, verify no migration drift:
cd services/agent && poetry run alembic revision --autogenerate -m "verify_no_drift"
# Should produce empty migration
```

---

## Track 2: Move app.py to Interfaces (CRITICAL)

**Priority:** Critical
**Estimated files:** 10-15
**Can run parallel:** Yes (independent)

### Problem
`core/core/app.py` imports from `interfaces/` and `modules/`, violating architecture.

### Tasks

#### 2.1 Create New App Location
**New file:** `services/agent/src/interfaces/http/app.py`

Move entire contents of `core/core/app.py` to this new location.

#### 2.2 Update Imports in New app.py
Change internal imports from relative to absolute where needed:
```python
# Old (in core/core/):
from ..middleware.rate_limit import ...
from ..tools.mcp_loader import ...

# New (in interfaces/http/):
from core.middleware.rate_limit import ...
from core.tools.mcp_loader import ...
```

#### 2.3 Update Entry Points
**Files to update:**
- `services/agent/src/main.py` (if exists)
- `services/agent/pyproject.toml` (entry points)
- `Dockerfile` or docker-compose references
- Any import of `from core.core.app import ...`

Search pattern:
```bash
grep -rn "from core.core.app import\|from core.core import app" services/agent/src/
```

#### 2.4 Delete Old File
Remove `services/agent/src/core/core/app.py` after all imports updated.

#### 2.5 Update __init__.py Exports
If `core/core/__init__.py` exports app, remove that export.

### Verification
```bash
./stack check
```

---

## Track 3: Fix Module-to-Module Dependencies (HIGH)

**Priority:** High
**Estimated files:** 3
**Can run parallel:** Yes (independent)

### Problem
Modules import from other modules (forbidden by architecture).

### Tasks

#### 3.1 Fix modules/rag importing modules.embedder
**File:** `services/agent/src/modules/rag/__init__.py`

**Current:**
```python
from modules.embedder import Embedder, get_embedder
```

**Fix:** Use dependency injection via constructor:
```python
from core.protocols import IEmbedder  # Protocol already exists

class RAGManager:
    def __init__(self, embedder: IEmbedder, ...):
        self._embedder = embedder
```

Update initialization in `app.py` to inject embedder instance.

#### 3.2 Fix modules/fetcher importing modules.rag
**File:** `services/agent/src/modules/fetcher/__init__.py`

**Current:**
```python
from modules.rag import RAGManager
```

**Fix:** Accept RAGManager via constructor or use protocol:
```python
from core.protocols import IRAGManager  # May need to create this

class WebFetcher:
    def __init__(self, rag_manager: IRAGManager | None = None, ...):
        self._rag_manager = rag_manager
```

#### 3.3 Fix modules/indexer importing modules.embedder
**File:** `services/agent/src/modules/indexer/ingestion.py`

**Current:**
```python
from modules.embedder import get_embedder
```

**Fix:** Inject embedder via constructor parameter.

### Verification
```bash
# Should return empty:
grep -rn "from modules\." services/agent/src/modules/ | grep -v "from modules.price_tracker\|from modules.indexer\|from modules.email\|from modules.context7"
```

---

## Track 4: Fix Core Tool Imports (HIGH)

**Priority:** High
**Estimated files:** 2
**Can run parallel:** Yes (independent)

### Problem
Core tools import directly from modules instead of using providers.

### Tasks

#### 4.1 Fix price_tracker tool
**File:** `services/agent/src/core/tools/price_tracker.py`

**Current (line ~67):**
```python
from modules.price_tracker.service import PriceTrackerService
```

**Fix:** Use provider pattern:
```python
from core.providers import get_price_tracker

# In tool's run method:
service = get_price_tracker()
```

#### 4.2 Fix send_email tool
**File:** `services/agent/src/core/tools/send_email.py`

**Current (line ~10):**
```python
from modules.email.templates import wrap_html_email
```

**Fix options:**
1. Move `wrap_html_email` to `core/utils/email.py`
2. Or access via email service protocol

### Verification
```bash
# Should return empty:
grep -rn "from modules\." services/agent/src/core/
```

---

## Track 5: API Endpoint Fixes (HIGH)

**Priority:** High
**Estimated files:** 2
**Can run parallel:** Yes (independent)

### Tasks

#### 5.1 Fix Crash Log Path Mismatch
**File:** `services/agent/src/interfaces/http/admin_diagnostics.py`

**Current (line ~126):**
```python
log_path = Path("services/agent/last_crash.log")
```

**Fix:** Match where app.py writes (line ~127 in app.py):
```python
log_path = Path("data/crash.log")
```

#### 5.2 Remove Unused Variable
**File:** `services/agent/src/interfaces/http/admin_price_tracker.py`

**Line ~165:** Remove unused `already_joined_product_store = False`

### Verification
```bash
./stack check
```

---

## Track 6: Replace Any Types (MEDIUM)

**Priority:** Medium
**Estimated files:** 15+
**Can run parallel:** Yes, but WAIT for Track 2

**Note:** Start this track AFTER Track 2 completes to avoid conflicts in app.py.

### Tasks

#### 6.1 High-Impact Any Replacements

| File | Line | Current | Replace With |
|------|------|---------|--------------|
| `orchestrator/dispatcher.py` | 54, 331 | `agent_service: Any` | Create `IAgentService` protocol |
| `core/core/service.py` | 755 | `list_models() -> Any` | Define `ModelsResponse` type |
| `core/tools/base.py` | 36 | `run(*args: Any, **kwargs: Any) -> Any` | Use TypeVar with bounds |
| `core/mcp/client.py` | 299, 342, 354 | Return `Any` | Define proper return types |

#### 6.2 Create Missing Protocols
**New file (if needed):** `services/agent/src/core/protocols/agent_service.py`

```python
from typing import Protocol, AsyncGenerator

class IAgentService(Protocol):
    async def process(self, ...) -> AsyncGenerator[dict, None]:
        ...
```

#### 6.3 Lower-Priority Any Fixes
- `core/observability/tracing.py` - OpenTelemetry types
- `modules/embedder/__init__.py` - Model type

### Verification
```bash
./stack typecheck
```

---

## Track 7: Convert Relative Imports (MEDIUM)

**Priority:** Medium
**Estimated files:** 20+
**Can run parallel:** Yes, but WAIT for Track 2

**Note:** Start this track AFTER Track 2 completes to avoid conflicts.

### Tasks

Convert all relative imports to absolute. Key directories:

#### 7.1 orchestrator/
```python
# Old:
from .skill_loader import SkillLoader
# New:
from orchestrator.skill_loader import SkillLoader
```

#### 7.2 core/mcp/
```python
# Old:
from ..models.mcp import McpTool
# New:
from core.models.mcp import McpTool
```

#### 7.3 core/tools/
```python
# Old:
from .base import Tool
# New:
from core.tools.base import Tool
```

#### 7.4 modules/email/
```python
# Old:
from .service import ResendEmailService
# New:
from modules.email.service import ResendEmailService
```

### Batch Conversion Script (optional)
```bash
# Find all relative imports:
grep -rn "^from \." services/agent/src/ --include="*.py"
```

### Verification
```bash
./stack check
```

---

## Track 8: Exception Handling (LOW)

**Priority:** Low
**Estimated files:** 10
**Can run parallel:** Yes

### Tasks

Add specific exception types where `except Exception:` is too broad:

| File | Line | Suggestion |
|------|------|------------|
| `core/core/service.py` | 1085 | `except (OSError, ValueError):` |
| `modules/price_tracker/service.py` | Multiple | `except SQLAlchemyError:` |
| `core/mcp/client.py` | 377 | `except (ConnectionError, TimeoutError):` |

### Verification
```bash
./stack check
```

---

## Execution Order

### Phase 1 - Parallel (5 engineers)
Start simultaneously:
1. **Engineer A:** Track 1 (DB Schema)
2. **Engineer B:** Track 2 (App.py Move)
3. **Engineer C:** Track 3 (Module Dependencies)
4. **Engineer D:** Track 4 (Core Tool Imports)
5. **Engineer E:** Track 5 (API Fixes)

### Phase 2 - Parallel (2 engineers)
After Phase 1 completes:
1. **Engineer F:** Track 6 (Any Types)
2. **Engineer G:** Track 7 (Relative Imports)

### Phase 3 - Optional
1. **Engineer H:** Track 8 (Exception Handling)

### Final Step
After all tracks complete:
```bash
./stack check
```

---

## Risk Mitigation

1. **Create feature branch first:**
   ```bash
   git checkout -b fix/codebase-cleanup
   ```

2. **Each engineer works on separate files** - minimal merge conflicts

3. **Run `stack check` after each track** - catch issues early

4. **Track 2 is most complex** - may need extra review

---

## Success Criteria

- [ ] `stack check` passes (Ruff, Black, Mypy, Pytest)
- [ ] No architecture violations (grep checks return empty)
- [ ] No migration drift (alembic autogenerate produces empty)
- [ ] All endpoints functional (manual smoke test)
