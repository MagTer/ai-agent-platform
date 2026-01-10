# AI Agent Platform - Implementation Primer

**Purpose:** Essential context for implementing features in this codebase. Read this before reading any implementation plan.

**Last Updated:** 2026-01-09

---

## 1. Architecture Essentials

### Modular Monolith with Strict Layer Dependency

**Location:** `services/agent/src/`

```
interfaces/     Layer 1 (Top)    - HTTP API, CLI adapters
    ↓ can import everything below
orchestrator/   Layer 2          - Planner Agent, Skill Delegate, Workflows
    ↓ can import modules + core
modules/        Layer 3          - RAG, Indexer, Fetcher, Embedder (ISOLATED)
    ↓ can ONLY import core
core/           Layer 4 (Bottom) - DB, Models, Config, Observability
    ↓ NEVER imports from above
```

**Critical Rule:** Modules CANNOT import other modules. They are isolated.

**Dependency Matrix:**

| From ↓ / To → | core | modules | orchestrator | interfaces |
|---------------|------|---------|--------------|------------|
| **core**      | ✅   | ❌      | ❌           | ❌         |
| **modules**   | ✅   | ❌      | ❌           | ❌         |
| **orchestrator** | ✅ | ✅     | ✅           | ❌         |
| **interfaces**| ✅   | ✅      | ✅           | ✅         |

### Protocol-Based Dependency Injection

**Pattern:** Core defines protocols, modules implement, interfaces inject at startup.

**Example:**

```python
# 1. Define protocol in core/protocols/embedder.py
from typing import Protocol

class IEmbedder(Protocol):
    async def embed(self, text: str) -> list[float]:
        """Embed text to vector."""
        ...

# 2. Implement in modules/embedder/embedder.py
from core.protocols import IEmbedder

class LiteLLMEmbedder:
    async def embed(self, text: str) -> list[float]:
        # Implementation
        return embedding

# 3. Register provider in core/providers.py
_embedder: IEmbedder | None = None

def set_embedder(embedder: IEmbedder) -> None:
    global _embedder
    _embedder = embedder

def get_embedder() -> IEmbedder:
    if _embedder is None:
        raise ProviderError("Embedder not configured")
    return _embedder

# 4. Inject at startup in interfaces/app.py
from core.providers import set_embedder
from modules.embedder import LiteLLMEmbedder

@app.on_event("startup")
async def startup():
    embedder = LiteLLMEmbedder()
    set_embedder(embedder)

# 5. Use in core tools
from core.providers import get_embedder

embedder = get_embedder()
result = await embedder.embed("text")
```

**Key Protocols:**
- `IEmbedder` - Text to vectors
- `IFetcher` - Web fetching
- `IRAGManager` - RAG pipeline
- `ICodeIndexer` - Code indexing
- `ILLMProtocol` - LLM client interface

---

## 2. Code Standards (NON-NEGOTIABLE)

### Type Safety
```python
# ✅ Correct
def process(items: list[str]) -> dict[str, int]:
    return {item: len(item) for item in items}

# ❌ Wrong
from typing import List, Dict, Any
def process(items: List[str]) -> Dict[str, Any]:  # NO!
    return {item: len(item) for item in items}
```

**Rules:**
- **Lowercase generic types:** `list`, `dict`, `set`, `tuple` (NOT `List`, `Dict`, etc.)
- **Never use `Any`** - Always specify concrete types
- **Strict typing:** All functions have type hints
- **No relative imports:** Use absolute paths (`from core.db import ...`)

### Async-First
```python
# ✅ All I/O is async
async def fetch_data(url: str) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        return response.json()

# ❌ No synchronous I/O
import requests  # NEVER use this
def fetch_data(url: str) -> dict:
    return requests.get(url).json()  # Blocks event loop!
```

**Rules:**
- All database operations: `async with get_session() as session`
- All HTTP requests: `async with httpx.AsyncClient()`
- All LLM calls: `await llm_client.complete(...)`
- Use `asyncio.gather()` for parallel operations

### Quality Gate (MANDATORY)

**Before completing ANY task:**
```bash
python scripts/code_check.py
```

**This runs (in order):**
1. **Ruff** - Linting + auto-fixes
2. **Black** - Formatting (auto-formats)
3. **Mypy** - Strict type checking
4. **Pytest** - All tests must pass

**If this fails, you MUST fix errors. No exceptions.**

### Import Organization
```python
# Standard library
import logging
from datetime import datetime
from typing import TYPE_CHECKING

# Third-party
from sqlalchemy import select
from sqlalchemy.orm import Mapped, mapped_column

# Local - absolute paths only
from core.db import get_session
from core.protocols import IEmbedder
from core.providers import get_embedder

# Type-checking imports (avoid circular deps)
if TYPE_CHECKING:
    from modules.rag.manager import RAGManager
```

---

## 3. Key Patterns

### Database Models (SQLAlchemy 2.0)

```python
# Location: core/db/models.py
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime

class BaseModel(Base):
    __abstract__ = True

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

class MyModel(BaseModel):
    __tablename__ = "my_models"

    name: Mapped[str] = mapped_column(String(255), index=True)
    context_id: Mapped[int] = mapped_column(ForeignKey("contexts.id"), index=True)

    # Relationships
    context: Mapped["Context"] = relationship(back_populates="my_models")
```

**Rules:**
- Use `Mapped[type]` for all columns
- Add `index=True` for foreign keys and frequently queried columns
- Use `relationship()` for associations
- Follow naming: table names are plural (`my_models`)

### Database Sessions

```python
# Async session with proper cleanup
from core.db import get_session

async def get_user(user_id: int) -> User | None:
    async with get_session() as session:
        stmt = select(User).where(User.id == user_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

# In FastAPI endpoints, use dependency injection
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

@app.get("/users/{user_id}")
async def get_user_endpoint(
    user_id: int,
    session: AsyncSession = Depends(get_session)
):
    stmt = select(User).where(User.id == user_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
```

### Testing Patterns

```python
# Location: services/agent/tests/unit/test_my_feature.py
import pytest
from core.tests.mocks import MockLLMClient, InMemoryAsyncSession

@pytest.mark.asyncio
async def test_my_feature():
    """Test basic functionality."""
    # Arrange
    llm = MockLLMClient()
    session = InMemoryAsyncSession()
    manager = MyFeatureManager(llm)

    # Act
    result = await manager.process("input")

    # Assert
    assert result == "expected"
    assert llm.call_count == 1

# Use fixtures for common setup
@pytest.fixture
async def mock_session():
    """Provide in-memory database session."""
    return InMemoryAsyncSession()
```

**Test locations:**
- Unit tests: `services/agent/tests/unit/`
- Integration tests: `services/agent/tests/integration/`
- Follow pattern from existing tests (see `test_rag_manager.py`)

### Error Handling

```python
# Use observability logger
from core.observability import logger

async def risky_operation():
    try:
        result = await external_call()
        return result
    except ExternalError as e:
        logger.error("External call failed", error=str(e), exc_info=True)
        raise RuntimeError(f"Operation failed: {e}") from e
```

### Configuration

```python
# Location: core/config.py
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

class MyFeatureConfig(BaseModel):
    enabled: bool = Field(default=True)
    api_key: str = Field(default="")
    timeout: int = Field(default=30)

class Settings(BaseSettings):
    # ... existing fields ...

    my_feature: MyFeatureConfig = Field(default_factory=MyFeatureConfig)

    class Config:
        env_file = ".env"
        env_nested_delimiter = "__"
```

**Environment variables:**
```bash
# In .env
MY_FEATURE__ENABLED=true
MY_FEATURE__API_KEY=secret
MY_FEATURE__TIMEOUT=60
```

---

## 4. Tools & Commands

### Package Management
```bash
# Add dependency
poetry add package-name

# Add dev dependency
poetry add --group dev package-name

# Update dependencies
poetry update

# Install all deps
poetry install
```

### Database Migrations
```bash
# Create migration
poetry run alembic revision --autogenerate -m "Add my_table"

# Apply migrations
poetry run alembic upgrade head

# Rollback
poetry run alembic downgrade -1
```

### Testing
```bash
# Run all tests
pytest services/agent/tests/ -v

# Run specific test
pytest services/agent/tests/unit/test_my_feature.py -v

# Run with coverage
pytest --cov=services/agent/src --cov-report=html
```

### Quality Checks
```bash
# Run full quality gate (MANDATORY before completing tasks)
python scripts/code_check.py

# Individual tools
poetry run ruff check .
poetry run black .
poetry run mypy
poetry run pytest
```

---

## 5. Documentation References

**Full documentation:** `docs/`

- **Architecture details:** `docs/ARCHITECTURE.md`
- **Development workflow:** `docs/development.md`
- **Operations:** `docs/OPERATIONS.md`
- **Testing:** `docs/testing/00_overview.md`

**Layer rules:** `.clinerules` (project-wide standards)

**Skills format:** `docs/SKILLS_FORMAT.md`

---

## 6. Common Pitfalls

### ❌ Don't Do This:

```python
# Circular imports (module importing another module)
from modules.rag import RAGManager  # If you're in modules/fetcher/

# Synchronous I/O in async code
import requests
response = requests.get(url)  # Blocks!

# Using Any type
def process(data: Any) -> Any:  # Mypy will fail

# Relative imports
from ..core import models  # Use absolute: from core.models import ...

# Forgetting to run quality checks
# (just commit and hope it works)
```

### ✅ Do This:

```python
# Use protocols for cross-module communication
from core.protocols import IRAGManager
from core.providers import get_rag_manager

# Async I/O
import httpx
async with httpx.AsyncClient() as client:
    response = await client.get(url)

# Concrete types
def process(data: list[dict[str, str]]) -> dict[str, int]:

# Absolute imports
from core.models import User, Context

# Always run quality gate
python scripts/code_check.py
```

---

## 7. Multi-Tenancy (Context-Based Isolation)

**State hierarchy:**
```
Context → Conversation → Session → Message
```

**All data scoped to `context_id`:**
- Database: Foreign keys to `contexts.id`
- Qdrant: Every memory tagged with `context_id`
- OAuth tokens: Per-context authentication

**Service Factory Pattern:**
```python
# Services are created per-request, not global singletons
@app.post("/v1/agent")
async def run_agent(
    request: AgentRequest,
    factory: ServiceFactory = Depends(get_service_factory),
    session: AsyncSession = Depends(get_db),
):
    context_id = await extract_context_id(request, session)
    service = await factory.create_service(context_id, session)
    return await service.handle_request(request, session)
```

---

## Summary: Key Takeaways

1. **Architecture:** Layered monolith, protocol-based DI, modules are isolated
2. **Standards:** Strict typing (`list[str]`, no `Any`), async-first, absolute imports
3. **Quality Gate:** `python scripts/code_check.py` MUST pass before completion
4. **Patterns:** Follow existing code (providers, DB models, testing mocks)
5. **Tools:** Poetry, Alembic, Pytest
6. **Docs:** Full details in `docs/` directory

**When in doubt:** Read existing implementations in similar modules (e.g., `modules/rag/` or `modules/embedder/`).
