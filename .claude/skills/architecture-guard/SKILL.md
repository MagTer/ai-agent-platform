---
name: architecture-guard
description: Validate that code changes follow the modular monolith architecture with strict layer dependencies and protocol-based dependency injection. Use when adding new modules, refactoring code structure, or modifying imports between layers.
allowed-tools: Read, Grep, Glob
model: claude-sonnet-4-5-20250929
---

# Architecture Guard

## When This Skill Activates

You should use this skill when:
- Adding new modules or components
- Refactoring code across multiple layers
- Modifying imports between layers (interfaces, orchestrator, modules, core)
- Implementing new protocols or providers
- The user asks to review architecture compliance
- Before major structural changes

## Architecture Rules (CRITICAL)

This project follows a **Modular Monolith** with strict unidirectional dependencies.

### Directory Structure (`services/agent/src/`)

```
interfaces/     (Layer 1: Top)    - HTTP API, CLI, Event consumers
    ↓ can import everything below
orchestrator/   (Layer 2)         - Workflows, Planner Agent, Skill Delegate
    ↓ can import modules + core
modules/        (Layer 3)         - RAG, Indexer, Fetcher, Embedder
    ↓ can ONLY import core
core/           (Layer 4: Bottom) - DB, Models, Config, Observability
    ↓ NEVER imports from above
```

### Critical Rules

1. **Interfaces Layer** (`interfaces/`)
   - Can import: `orchestrator`, `modules`, `core`
   - Purpose: Adapt external protocols (HTTP, CLI) to internal data structures
   - NO business logic here - pure adapters

2. **Orchestrator Layer** (`orchestrator/`)
   - Can import: `modules`, `core`
   - Purpose: Workflows, task delegation, planning
   - Cannot import: `interfaces`

3. **Modules Layer** (`modules/`)
   - Can import: `core` ONLY
   - **CRITICAL:** Modules are ISOLATED - cannot import each other
   - Purpose: Encapsulated features (RAG, Fetcher, Embedder)
   - Each module is self-contained

4. **Core Layer** (`core/`)
   - **NEVER** imports from: `interfaces`, `orchestrator`, or `modules`
   - Purpose: Foundation layer (DB, Models, Config, Observability)
   - Uses Protocol-based DI to avoid circular dependencies

## Protocol-Based Dependency Injection

### How It Works

The core layer defines **Protocol interfaces** but doesn't know about implementations:

```python
# core/protocols/embedder.py
from typing import Protocol

class IEmbedder(Protocol):
    async def embed(self, text: str) -> list[float]:
        ...
```

Implementations live in the `modules/` layer:

```python
# modules/embedder/embedder.py
from core.protocols import IEmbedder

class LiteLLMEmbedder:
    async def embed(self, text: str) -> list[float]:
        # Implementation
        ...
```

The `interfaces/` layer wires them together at startup via providers:

```python
# interfaces/app.py (startup)
from core.providers import set_embedder
from modules.embedder import LiteLLMEmbedder

embedder = LiteLLMEmbedder()
set_embedder(embedder)  # Inject implementation
```

Core tools then access via providers:

```python
# core/tools/some_tool.py
from core.providers import get_embedder

embedder = get_embedder()  # Gets injected implementation
result = await embedder.embed("text")
```

### Key Protocols

| Protocol | Purpose | Location |
|----------|---------|----------|
| `IEmbedder` | Text embedding interface | `core/protocols/` |
| `IMemoryProtocol` | Vector memory store interface | `core/protocols/` |
| `ILLMProtocol` | LLM client interface | `core/protocols/` |
| `IToolProtocol` | Tool execution interface | `core/protocols/` |
| `IFetcher` | Web fetching interface | `core/protocols/` |
| `IRAGManager` | RAG pipeline interface | `core/protocols/` |
| `ICodeIndexer` | Code indexing interface | `core/protocols/` |

## Import Validation Checklist

### ✅ VALID Import Patterns

```python
# interfaces/ can import anything
from orchestrator.planner import PlannerAgent
from modules.rag.manager import RAGManager
from core.db.models import Conversation

# orchestrator/ can import modules + core
from modules.fetcher import WebFetcher
from core.observability import trace_span

# modules/ can import core only
from core.protocols import IEmbedder
from core.providers import get_embedder
from core.db import get_session

# core/ imports only within core/
from core.models import Message
from core.observability import logger
```

### ❌ INVALID Import Patterns

```python
# core/ importing from higher layers - FORBIDDEN
from modules.rag import RAGManager  # ❌ NEVER
from orchestrator.planner import PlannerAgent  # ❌ NEVER
from interfaces.api import router  # ❌ NEVER

# modules/ importing from other modules - FORBIDDEN
from modules.rag import RAGManager  # ❌ If you're in modules/fetcher/
from modules.embedder import LiteLLMEmbedder  # ❌ If you're in modules/rag/

# modules/ importing from orchestrator or interfaces - FORBIDDEN
from orchestrator.planner import PlannerAgent  # ❌ NEVER
from interfaces.api import router  # ❌ NEVER

# Relative imports - FORBIDDEN EVERYWHERE
from ..core import models  # ❌ NEVER use relative imports
from ...modules import rag  # ❌ NEVER use relative imports
```

### ✅ CORRECT Solutions

**Problem:** Module needs functionality from another module

**Solution:** Use protocol-based DI via core layer:

```python
# Bad: Direct import between modules
from modules.embedder import LiteLLMEmbedder  # ❌

# Good: Use protocol + provider
from core.protocols import IEmbedder
from core.providers import get_embedder

embedder = get_embedder()  # Gets the implementation
```

**Problem:** Core needs to call higher-layer functionality

**Solution:** Define a Protocol in core, implement in higher layer, inject at startup:

```python
# 1. Define protocol in core/protocols/
class INotifier(Protocol):
    async def notify(self, message: str) -> None: ...

# 2. Implement in modules/ or orchestrator/
class SlackNotifier:
    async def notify(self, message: str) -> None:
        # Implementation
        ...

# 3. Inject at startup in interfaces/app.py
from core.providers import set_notifier
set_notifier(SlackNotifier())

# 4. Use in core via provider
from core.providers import get_notifier
notifier = get_notifier()
await notifier.notify("message")
```

## Validation Workflow

When reviewing or writing code:

### 1. Identify the Layer

Determine which layer the file belongs to:
- `services/agent/src/interfaces/` → Interfaces layer
- `services/agent/src/orchestrator/` → Orchestrator layer
- `services/agent/src/modules/` → Modules layer
- `services/agent/src/core/` → Core layer

### 2. Check Imports

For each import statement:
- Is it importing from a higher layer? (FORBIDDEN)
- Is it a module importing from another module? (FORBIDDEN)
- Is it a relative import? (FORBIDDEN)

### 3. Validate Protocol Usage

If adding new cross-layer dependencies:
- Is there a Protocol defined in `core/protocols/`?
- Is the implementation in `modules/` or `orchestrator/`?
- Is it injected via `core/providers.py`?
- Is it wired at startup in `interfaces/app.py`?

### 4. Check Module Isolation

If working in `modules/`:
- Does it only import from `core/`?
- Does it NOT import from other modules?
- Is it self-contained?

## Common Violations and Fixes

### Violation 1: Core Importing from Modules

**Bad:**
```python
# core/tools/rag_tool.py
from modules.rag.manager import RAGManager  # ❌ Circular dependency
```

**Good:**
```python
# core/protocols/rag.py
class IRAGManager(Protocol):
    async def query(self, text: str) -> list[str]: ...

# core/tools/rag_tool.py
from core.protocols import IRAGManager
from core.providers import get_rag_manager

rag = get_rag_manager()  # ✅ Injected at startup
```

### Violation 2: Module Importing from Another Module

**Bad:**
```python
# modules/rag/manager.py
from modules.embedder import LiteLLMEmbedder  # ❌ Module cross-dependency
```

**Good:**
```python
# modules/rag/manager.py
from core.protocols import IEmbedder
from core.providers import get_embedder

embedder = get_embedder()  # ✅ Via protocol + provider
```

### Violation 3: Relative Imports

**Bad:**
```python
from ..core import models  # ❌ Relative import
from ...modules.rag import RAGManager  # ❌ Relative import
```

**Good:**
```python
from core.models import Message  # ✅ Absolute import
from modules.rag.manager import RAGManager  # ✅ Absolute import (if allowed)
```

## Quick Reference: Dependency Matrix

| From ↓ / To → | core | modules | orchestrator | interfaces |
|---------------|------|---------|--------------|------------|
| **core**      | ✅   | ❌      | ❌           | ❌         |
| **modules**   | ✅   | ❌      | ❌           | ❌         |
| **orchestrator** | ✅ | ✅     | ✅           | ❌         |
| **interfaces**| ✅   | ✅      | ✅           | ✅         |

✅ = Allowed
❌ = Forbidden

## Tools for Validation

Use these tools to validate architecture:

```bash
# Search for imports from higher layers in core/
grep -r "from interfaces" services/agent/src/core/
grep -r "from orchestrator" services/agent/src/core/
grep -r "from modules" services/agent/src/core/

# Search for cross-module imports
grep -r "from modules\\.rag" services/agent/src/modules/fetcher/
grep -r "from modules\\.embedder" services/agent/src/modules/rag/

# Search for relative imports
grep -r "from \\.\\." services/agent/src/
```

## When to Update Architecture

If you need to violate these rules:
1. **STOP** - Do not proceed
2. **Inform the user** - Explain what you're trying to do
3. **Propose a Protocol** - Suggest adding a Protocol in `core/protocols/`
4. **Get approval** - Wait for user confirmation
5. **Implement via DI** - Use the provider pattern

## Remember

The architecture exists to:
- Prevent circular dependencies
- Enable testability (mock protocols)
- Allow layer replacement (swap implementations)
- Maintain clear boundaries

**When in doubt, add a Protocol.**

---

**After validating architecture:**
- Confirm all imports follow the dependency rules
- Verify protocols are used for cross-layer communication
- Ensure modules remain isolated
- Inform the user if any violations are found
