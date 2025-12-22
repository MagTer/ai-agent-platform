# Refactoring Plan v2: Modular Monolith Transition

## 1. Executive Summary
This plan outlines the architectural pivot of the `ai-agent-platform` from a distributed microservices set to a consolidated **Modular Monolith**. The goal is to reduce operational overhead (Docker containers), strictly type session management, and introduce a file-based command system.

## 2. Infrastructure Collapse

### 2.1 Module Consolidation
We will dismantle the following microservices and integrate them as internal modules within `services/agent`.

#### Fetcher Service (`services/fetcher`) -> `services/agent/src/modules/fetcher`
- **Source:** `services/fetcher/app.py`
- **Destination:** `services/agent/src/modules/fetcher/`
- **Actions:**
    - Create `services/agent/src/modules/fetcher/__init__.py` exposing `WebFetcher` class.
    - Refactor `fetch_and_extract` and `research` logic into this class.
    - **Note:** Remove the FastAPI layer; the Agent will call the Python class directly.
    - **Dependencies:** Add `trafilatura`, `httpx`, `beautifulsoup4` (if needed) to Agent's `pyproject.toml`.

#### RAG Proxy (`services/ragproxy`) -> `services/agent/src/modules/rag`
- **Source:** `services/ragproxy/app.py`
- **Destination:** `services/agent/src/modules/rag/`
- **Actions:**
    - Create `services/agent/src/modules/rag/__init__.py`.
    - Implement `RAGManager` class containing `qdrant_retrieve` and `embed_texts` (calling internal Embedder) logic.
    - **Note:** Remove `get_client()` calls that hit external services; use internal method calls.

#### Embedder Service (`services/embedder`) -> `services/agent/src/modules/embedder`
- **Source:** `services/embedder/app.py`
- **Destination:** `services/agent/src/modules/embedder/`
- **Actions:**
    - Create `services/agent/src/modules/embedder/__init__.py`.
    - Implement `Embedder` class that wraps `SentenceTransformer`.
    - **Dependencies:** Add `sentence-transformers` and `torch` (CPU version) to Agent's `pyproject.toml`.
    - **Optimization:** Load model once on Agent startup (singleton pattern).

### 2.2 Docker Composition
Refactor `docker-compose.yml`:
- **Remove:** `fetcher`, `ragproxy`, `embedder` services.
- **Keep:** `agent`, `postgres`, `qdrant`, `searxng`.
- **Note:** `litellm` service is also removed; the Agent will use the `litellm` Python library directly for all calls.

## 3. Database Schema Implementation (The "State Engine")

We will use **SQLAlchemy** (Async) with **PostgreSQL**.

### 3.1 Tables

#### `contexts`
Represents a boundary or environment for the agent.
- `id`: UUID (Primary Key)
- `name`: String (Unique, e.g., "Main Repo", "Home Automation")
- `type`: String (Enum: `git_repo`, `im_platform`, `home_assistant`)
- `config`: JSONB (Stores root paths, API URLs, specific env vars)
- `default_cwd`: String (Default working directory for this context)

#### `conversations`
Represents a continuous thread of interaction on a specific platform.
- `id`: UUID (Primary Key)
- `platform`: String (e.g., 'openwebui', 'slack', 'terminal')
- `platform_id`: String (External ID, e.g., Slack Thread ID, Discord Channel ID)
- `context_id`: UUID (ForeignKey to `contexts.id`)
- `current_cwd`: String (Stateful CWD for this specific conversation)
- `created_at`: DateTime
- `updated_at`: DateTime

#### `sessions`
Represents a specific unit of work or an active engagement loop.
- `id`: UUID (Primary Key)
- `conversation_id`: UUID (ForeignKey to `conversations.id`)
- `active`: Boolean (Is this session currently processing?)
- `metadata`: JSONB (Stores `last_command`, `accumulated_context`, `scratchpad`)

### 3.2 Persistence Layer
- Create `services/agent/src/core/db/`
- `engine.py`: Async engine setup.
- `models.py`: SQLAlchemy declarative models.
- `repository.py`: Data access patterns (Repository Pattern) to decouple logic from SQL.

## 4. Interface Abstraction

### 4.1 Protocols (`services/agent/src/interfaces/protocols.py`)

```python
from typing import Protocol, AsyncGenerator, Any

class IPlatformAdapter(Protocol):
    """
    Standard interface for any external platform (Slack, WebUI, CLI).
    """
    async def send_message(self, conversation_id: str, content: str) -> None:
        ...

    async def get_streaming_mode(self) -> bool:
        ...

    async def listen(self) -> AsyncGenerator[Any, None]:
        ...

class IAssistantClient(Protocol):
    """
    Wrapper around LLM inference (LiteLLM).
    """
    async def chat_stream(self, messages: list[dict], model: str) -> AsyncGenerator[str, None]:
        ...
        
    async def chat_complete(self, messages: list[dict], model: str) -> str:
        ...
```

## 5. Command System (File-Based Skills)

Moves away from hardcoded Python tools for prompt-heavy logic.

### 5.1 Structure
- Directory: `services/agent/skills/`
- Format: Markdown Frontmatter + Body

**Example: `services/agent/skills/summarize_git_diff.md`**
```markdown
---
name: summarize_git_diff
description: Summarizes the changes in the current git repository.
variables:
  - diff_output
---
You are a senior code reviewer. Analyze the following git diff and summarize the changes.

Diff:
${diff_output}
```

### 5.2 Orchestrator Logic
- **Module:** `services/agent/src/core/command_loader.py`
- **Function:** `load_command(name: str, args: dict)`
- **Process:**
    1. Read `skills/{name}.md`.
    2. Parse Frontmatter (YAML).
    3. Validate `args` against `variables` list.
    4. Perform substitution (Template engine, e.g., `string.Template` or `jinja2`) on the body using `args`.
    5. Return the fully rendered system/user prompt to be sent to the LLM.

## 6. Migration Steps (Phase 1 Execution)
1.  **Dependencies**: Add `asyncpg`, `sqlalchemy`, `alembic`, `trafilatura`, `sentence-transformers`, `litellm` (python lib).
2.  **Scaffolding**: Create module directories (`modules/fetcher`, `modules/rag`, `modules/embedder`).
3.  **Porting**: Copy and refactor code from services to modules.
4.  **Database**: Initialize Alembic, create migration script for new tables.
5.  **Cleanup**: Update `docker-compose.yml` and delete old service directories.
