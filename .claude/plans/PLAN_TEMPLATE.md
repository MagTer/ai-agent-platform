# [Feature/Task Name]

**Created:** YYYY-MM-DD
**Planner:** Opus 4.5
**Status:** Planning Complete / Implementation In Progress / Completed
**Implementer:** Sonnet 4.5

---

## 1. Executive Summary

**Problem Statement:**
[What problem are we solving? Why is this needed?]

**Solution Approach:**
[High-level approach chosen. In 2-3 sentences.]

**Success Criteria:**
- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

---

## 2. Codebase Context

### 2.1 Relevant Architecture

**Layers Involved:**
- `core/` - [What core components are affected?]
- `modules/` - [Which modules?]
- `orchestrator/` - [Any orchestrator changes?]
- `interfaces/` - [API or CLI changes?]

**Key Files:**
```
services/agent/src/
├── core/
│   ├── file1.py         # [Purpose, what we'll modify]
│   └── file2.py         # [Purpose, what we'll add]
├── modules/
│   └── feature/
│       └── manager.py   # [New file, what it does]
└── interfaces/
    └── api/
        └── routes.py    # [Endpoints to add]
```

### 2.2 Current Implementation Patterns

**Pattern 1: [e.g., Dependency Injection]**
```python
# Example from codebase showing current pattern
# Located in: services/agent/src/core/providers.py

from core.protocols import IEmbedder

_embedder: IEmbedder | None = None

def set_embedder(embedder: IEmbedder) -> None:
    global _embedder
    _embedder = embedder
```

**Pattern 2: [e.g., Database Models]**
```python
# Example from: services/agent/src/core/db/models.py

from sqlalchemy.orm import Mapped, mapped_column

class BaseModel(Base):
    __abstract__ = True

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
```

**Pattern 3: [e.g., Error Handling]**
[Show how errors are currently handled in similar code]

### 2.3 Dependencies

**Existing Dependencies Used:**
- `package==version` - [How we'll use it]

**New Dependencies Required:**
- `new-package==version` - [Why needed, what for]

**Installation:**
```bash
poetry add new-package
```

---

## 3. Architecture Decisions

### Decision 1: [e.g., Where to place new module]

**Options Considered:**
1. **Option A:** [Description]
   - Pros: [...]
   - Cons: [...]
2. **Option B:** [Description] ✅ CHOSEN
   - Pros: [...]
   - Cons: [...]

**Rationale:** [Why we chose Option B]

### Decision 2: [e.g., Protocol vs Direct Import]

**Chosen Approach:** [Description]

**Rationale:** [Why this approach fits the architecture]

---

## 4. Implementation Roadmap

### Phase 1: Core Infrastructure

**Files to Create:**

1. **`services/agent/src/core/protocols/new_protocol.py`**
   ```python
   # Protocol definition
   from typing import Protocol

   class INewFeature(Protocol):
       async def execute(self, input: str) -> str:
           """[Docstring explaining what this does]"""
           ...
   ```

2. **`services/agent/src/core/providers.py`** (ADD to existing)
   ```python
   # Add to existing providers.py

   _new_feature: INewFeature | None = None

   def set_new_feature(feature: INewFeature) -> None:
       global _new_feature
       _new_feature = feature

   def get_new_feature() -> INewFeature:
       if _new_feature is None:
           raise RuntimeError("NewFeature not initialized")
       return _new_feature
   ```

**Files to Modify:**

1. **`services/agent/src/core/db/models.py`** (ADD new model)
   ```python
   # Add after existing models (line ~150)

   class NewFeatureData(Base):
       __tablename__ = "new_feature_data"

       id: Mapped[int] = mapped_column(primary_key=True)
       name: Mapped[str] = mapped_column(String(255), index=True)
       created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

       # Follow existing pattern for relationships
   ```

### Phase 2: Module Implementation

**Files to Create:**

1. **`services/agent/src/modules/new_feature/`** (new directory)
   ```
   modules/new_feature/
   ├── __init__.py
   ├── manager.py      # Main implementation
   ├── config.py       # Configuration
   └── exceptions.py   # Custom exceptions
   ```

2. **`services/agent/src/modules/new_feature/manager.py`**
   ```python
   """
   [Module description]

   This module implements [what it does].
   Follows [which patterns from codebase].
   """
   from core.protocols import INewFeature
   from core.observability import logger, trace_span

   class NewFeatureManager:
       """[Class description]"""

       def __init__(self, config: NewFeatureConfig) -> None:
           self.config = config

       @trace_span("new_feature.execute")
       async def execute(self, input: str) -> str:
           """[Method description]"""
           logger.info("Executing new feature", input_length=len(input))

           # Implementation here
           result = await self._process(input)

           return result

       async def _process(self, input: str) -> str:
           """[Helper method description]"""
           # Follow async patterns from existing code
           pass
   ```

### Phase 3: Integration

**Files to Modify:**

1. **`services/agent/src/interfaces/app.py`** (startup injection)
   ```python
   # Add to startup function (around line ~50)

   @app.on_event("startup")
   async def startup():
       # ... existing startup code ...

       # Initialize new feature
       from modules.new_feature.manager import NewFeatureManager
       from modules.new_feature.config import load_config
       from core.providers import set_new_feature

       config = load_config()
       new_feature = NewFeatureManager(config)
       set_new_feature(new_feature)

       logger.info("NewFeature initialized")
   ```

2. **`services/agent/src/interfaces/api/routes.py`** (if adding API)
   ```python
   # Add new endpoint (around line ~120, after existing routes)

   from pydantic import BaseModel
   from core.providers import get_new_feature

   class NewFeatureRequest(BaseModel):
       input: str

   class NewFeatureResponse(BaseModel):
       result: str

   @router.post("/v1/new-feature", response_model=NewFeatureResponse)
   async def execute_new_feature(
       request: NewFeatureRequest,
       current_user: User = Depends(get_current_user)
   ) -> NewFeatureResponse:
       """[Endpoint description]"""
       feature = get_new_feature()
       result = await feature.execute(request.input)
       return NewFeatureResponse(result=result)
   ```

### Phase 4: Database Migration

**Create Migration:**
```bash
# Run these commands during implementation
poetry run alembic revision --autogenerate -m "Add new_feature_data table"
poetry run alembic upgrade head
```

**Migration file will be at:**
`services/agent/alembic/versions/XXXX_add_new_feature_data.py`

### Phase 5: Tests

**Files to Create:**

1. **`services/agent/tests/unit/test_new_feature.py`**
   ```python
   """Unit tests for NewFeature module"""
   import pytest
   from modules.new_feature.manager import NewFeatureManager

   @pytest.mark.asyncio
   async def test_new_feature_basic():
       """Test basic functionality"""
       manager = NewFeatureManager(config=test_config)
       result = await manager.execute("test input")
       assert result == "expected output"

   # Follow existing test patterns from:
   # - tests/unit/test_rag_manager.py
   # - tests/unit/test_embedder.py
   ```

2. **`services/agent/tests/integration/test_new_feature_api.py`**
   ```python
   """Integration tests for NewFeature API"""
   from fastapi.testclient import TestClient

   def test_new_feature_endpoint(test_client: TestClient):
       """Test API endpoint"""
       response = test_client.post(
           "/v1/new-feature",
           json={"input": "test"}
       )
       assert response.status_code == 200
       # Follow pattern from tests/integration/test_api.py
   ```

---

## 5. Configuration Changes

### 5.1 Environment Variables

**Add to `.env`:**
```bash
# New Feature Configuration
NEW_FEATURE_ENABLED=true
NEW_FEATURE_API_KEY=your_api_key_here
NEW_FEATURE_TIMEOUT=30
```

**Add to `.env.example`:**
```bash
# New Feature Configuration
NEW_FEATURE_ENABLED=true
NEW_FEATURE_API_KEY=
NEW_FEATURE_TIMEOUT=30
```

### 5.2 Config Files

**Modify `services/agent/src/core/config.py`:**
```python
# Add new config section (around line ~80)

class NewFeatureConfig(BaseModel):
    enabled: bool = Field(default=True)
    api_key: str = Field(default="")
    timeout: int = Field(default=30)

class Settings(BaseSettings):
    # ... existing fields ...

    new_feature: NewFeatureConfig = Field(default_factory=NewFeatureConfig)
```

---

## 6. Quality Checks

### 6.1 Architecture Compliance

**Run architecture-guard to verify:**
- [ ] No circular dependencies
- [ ] Modules only import from core/
- [ ] Protocols used for cross-layer communication
- [ ] No relative imports

### 6.2 Code Quality

**Run quality-check:**
```bash
./stack check
```

**Or run individual checks:**
- `./stack lint` - Ruff + Black
- `./stack typecheck` - Mypy
- `./stack test` - Pytest

**Expected checks:**
- [ ] Ruff linting passes
- [ ] Black formatting passes
- [ ] Mypy type checking passes (strict mode)
- [ ] All tests pass

### 6.3 Security Review

**Check for:**
- [ ] No hardcoded secrets
- [ ] Input validation on all endpoints
- [ ] Authentication required where needed
- [ ] SQL injection prevention (parameterized queries)

---

## 7. Documentation Updates

### Files to Update:

1. **`docs/ARCHITECTURE.md`**
   - Add section explaining new feature
   - Update component diagram if needed

2. **`docs/architecture/02_agent.md`**
   - Document new module
   - Explain how it fits in the architecture

3. **`docs/OPERATIONS.md`** (if adding API endpoint)
   - Add smoke test example for new endpoint

4. **`README.md`** (if user-facing)
   - Add feature to capabilities list

---

## 8. Potential Issues & Solutions

### Issue 1: [Anticipated problem]

**Problem:** [Description]

**Solution:** [How to handle it]

**Fallback:** [Alternative approach]

### Issue 2: [Another anticipated issue]

**Problem:** [Description]

**Solution:** [How to handle it]

---

## 9. Testing Strategy

### Unit Tests
- [ ] Test NewFeatureManager in isolation
- [ ] Mock dependencies (embedder, LLM, DB)
- [ ] Cover edge cases and error conditions

### Integration Tests
- [ ] Test API endpoint end-to-end
- [ ] Test database operations
- [ ] Test interaction with other modules

### Manual Testing
```bash
# Start the stack
poetry run stack up

# Test the endpoint
curl -X POST http://localhost:8000/v1/new-feature \
  -H "Content-Type: application/json" \
  -d '{"input": "test"}'

# Expected response:
# {"result": "..."}
```

---

## 10. Rollout Plan

### Step 1: Implementation
- [ ] Implement core infrastructure (Phase 1)
- [ ] Implement module (Phase 2)
- [ ] Add integration (Phase 3)
- [ ] Run quality checks

### Step 2: Testing
- [ ] Write and run unit tests
- [ ] Write and run integration tests
- [ ] Manual smoke testing

### Step 3: Documentation
- [ ] Update architecture docs
- [ ] Update operational docs
- [ ] Add code comments

### Step 4: Deployment
- [ ] Create PR
- [ ] Code review
- [ ] Merge to main
- [ ] Deploy to staging
- [ ] Monitor and validate

---

## 11. Success Validation

**How to verify this is done correctly:**

1. **Functionality:**
   - [ ] Feature works as expected
   - [ ] All acceptance criteria met
   - [ ] Manual testing passes

2. **Quality:**
   - [ ] `./stack check` passes
   - [ ] Architecture compliance verified
   - [ ] Security review clean

3. **Documentation:**
   - [ ] All docs updated
   - [ ] Code is well-commented
   - [ ] Examples provided

4. **Integration:**
   - [ ] Works with existing features
   - [ ] No regressions in other areas
   - [ ] Performance acceptable

---

## 12. Implementation Notes (for Sonnet)

**Order of Operations:**
1. Read entire plan first
2. Start with Phase 1 (core infrastructure)
3. Proceed sequentially through phases
4. Run quality checks after each phase
5. Update this plan with completion status

**When You Get Stuck:**
- Re-read relevant sections of this plan
- Check existing code patterns referenced above
- Use Read tool to examine similar implementations
- Ask user for clarification if requirements unclear

**Do Not:**
- Skip phases (follow order)
- Deviate from patterns shown above
- Add features not in the plan
- Skip quality checks

---

## Status Tracking

- [ ] Phase 1: Core Infrastructure
- [ ] Phase 2: Module Implementation
- [ ] Phase 3: Integration
- [ ] Phase 4: Database Migration
- [ ] Phase 5: Tests
- [ ] Quality Checks Passed
- [ ] Documentation Updated
- [ ] Success Validation Complete

**Notes:**
[Sonnet adds notes here during implementation]
