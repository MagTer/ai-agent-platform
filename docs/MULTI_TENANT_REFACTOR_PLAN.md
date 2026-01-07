# Multi-Tenant Architecture & Admin Dashboard Refactoring Plan

**Status:** Planning
**Created:** 2026-01-05
**Target Completion:** Q1 2026
**Estimated Effort:** 4-5 weeks

## Executive Summary

Transform the AI Agent Platform from a single-user system to a fully multi-tenant platform supporting 6+ product owners with:
- Context-isolated tool access and MCP connections
- Per-user OAuth token management
- Comprehensive admin dashboard for platform management
- Support for multiple adapters (OpenWebUI, Slack, event triggers)

**Current State:** 70% ready - database schema supports multi-tenancy, but runtime components (tools, MCP clients, memory) are global singletons.

**Target State:** Full context isolation with per-user tool registries, MCP client pools, and admin interface for management.

---

## Table of Contents

1. [Current Architecture Analysis](#current-architecture-analysis)
2. [Multi-Tenancy Blockers](#multi-tenancy-blockers)
3. [Implementation Phases](#implementation-phases)
4. [Admin Dashboard Design](#admin-dashboard-design)
5. [File Change Inventory](#file-change-inventory)
6. [Testing Strategy](#testing-strategy)
7. [Migration Path](#migration-path)
8. [Success Criteria](#success-criteria)

---

## Current Architecture Analysis

### ✅ What's Ready

**Database Schema:**
```
Context (tenant/workspace)
  ├─ Conversation (chat sessions)
  │   ├─ Session (agent execution)
  │   │   └─ Message (chat history)
  │   └─ platform/platform_id (adapter tracking)
  └─ OAuthToken (per-context tokens)
      └─ provider/access_token/refresh_token
```

**Files:**
- `src/core/db/models.py` - Full multi-tenant schema
- `src/core/db/oauth_models.py` - Context-isolated OAuth tokens
- `src/interfaces/http/oauth_webui.py` - Conversation→Context mapping

**Platform Adapter Pattern:**
- `src/interfaces/http/openwebui_adapter.py` - OpenWebUI integration
- `src/interfaces/telegram/adapter.py` - Telegram bot
- Base pattern supports unlimited adapters

### ❌ Critical Blockers

**1. Global Tool Registry** (`src/core/tools/registry.py`)
```python
# Current: Single instance shared by all users
tool_registry = load_tool_registry(settings.tools_config_path)  # Line 176
service_instance = AgentService(..., tool_registry=tool_registry)
app.state.service = service_instance  # Singleton
```

**Impact:** All users access identical tools. Can't restrict permissions or load user-specific MCP tools.

**2. Global MCP Clients** (`src/core/tools/mcp_loader.py:115`)
```python
_active_clients: list[McpClient] = []  # Global state

# Loaded once at startup
async def load_mcp_tools(settings, tool_registry):
    mcp_client = McpClient(url=settings.homey_mcp_url, auth_token=static_token)
    _active_clients.append(mcp_client)  # SHARED
```

**Impact:** All users share same MCP connections. OAuth infrastructure exists but unused.

**3. Shared Memory Store** (`src/core/core/memory.py`)
```python
# Single Qdrant collection, filters only by conversation_id
payload = {
    "conversation_id": record.conversation_id,  # No context_id!
    "text": record.text,
}
```

**Impact:** Potential memory leakage between contexts (low risk for 6 users, but bad practice).

---

## Multi-Tenancy Blockers

### Blocker #1: Global Tool Registry

**Problem:**
- Tools loaded once at application startup
- No per-context filtering or permissions
- MCP tools registered globally without context awareness

**Root Cause:**
```python
# app.py line 176-192
tool_registry = load_tool_registry(settings.tools_config_path)
delegate_tool = SkillDelegateTool(litellm_client, tool_registry)
tool_registry.register(delegate_tool)

service_instance = AgentService(
    settings=settings,
    litellm=litellm_client,
    memory=memory_store,
    tool_registry=tool_registry,  # SINGLETON
)
app.state.service = service_instance  # GLOBAL STATE
```

**Solution Required:**
- Per-request tool registry creation
- Context-aware tool filtering
- Dynamic MCP tool registration based on user's OAuth tokens

### Blocker #2: Global MCP Client Pool

**Problem:**
- MCP clients created at startup with static configuration
- All users share same client instances
- OAuth token infrastructure exists but not integrated with MCP loading

**Root Cause:**
```python
# mcp_loader.py lines 128-196
async def load_mcp_tools(settings: Settings, tool_registry: ToolRegistry):
    global _active_clients

    if settings.homey_mcp_url and settings.homey_api_token:
        mcp_client = McpClient(
            url=str(settings.homey_mcp_url),
            auth_token=settings.homey_api_token,  # STATIC
            # context_id NOT passed
            # oauth_provider NOT passed
        )
        await mcp_client.connect()
        _active_clients.append(mcp_client)

        for mcp_tool in mcp_client.tools:
            wrapper = McpToolWrapper(mcp_client, mcp_tool, "Homey")
            tool_registry.register(wrapper)  # GLOBAL REGISTRY
```

**Existing but Unused Infrastructure:**
```python
# mcp/client.py lines 125-150 - ALREADY IMPLEMENTED!
async def _get_auth_token(self) -> str | None:
    if self._context_id and self._oauth_provider:
        try:
            token_manager = get_token_manager()
            token = await token_manager.get_token(
                self._oauth_provider, self._context_id
            )
            if token:
                return token  # Per-user OAuth token!
        except Exception as e:
            LOGGER.warning("OAuth token fetch failed: %s", e)

    return self._static_token  # Fallback
```

**Solution Required:**
- Create per-context MCP client pools
- Pass context_id through dependency injection
- Use OAuth tokens from database instead of static config
- Implement client lifecycle management (creation, caching, cleanup)

### Blocker #3: Memory Store Context Isolation

**Problem:**
- Single Qdrant collection for all users
- Payloads only include conversation_id, not context_id
- Search filters by conversation but not context

**Root Cause:**
```python
# memory.py lines 95-102
PointStruct(
    id=uuid4().hex,
    vector=vector,
    payload={
        "conversation_id": record.conversation_id,  # Missing context_id
        "text": record.text,
    },
)

# memory.py lines 124-133
async def search(self, query: str, limit: int = 5, conversation_id: str | None = None):
    query_filter: Filter | None = None
    if conversation_id:
        query_filter = Filter(
            must=[
                FieldCondition(
                    key="conversation_id",
                    match=MatchValue(value=conversation_id),
                )
                # No context_id filter!
            ]
        )
```

**Solution Required:**
- Add context_id to Qdrant payloads
- Filter searches by context_id AND conversation_id
- Pass context_id through MemoryStore initialization

---

## Implementation Phases

### Phase 1: Foundation & Context Propagation (Week 1)

**Goal:** Establish context_id propagation through the entire request flow.

#### 1.1 Context Extraction from Adapters

**OpenWebUI Adapter** (`src/interfaces/http/openwebui_adapter.py`)

**Current:**
```python
@router.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    agent_service: AgentService = Depends(get_agent_service),
    session: AsyncSession = Depends(get_db),
):
```

**Target:**
```python
async def get_context_id(
    request: ChatCompletionRequest,
    session: AsyncSession = Depends(get_db),
) -> UUID:
    """Extract context_id from conversation_id in request."""
    conversation_id = request.conversation_id or request.metadata.get("conversation_id")

    if not conversation_id:
        # Create new context for new conversation
        context = Context(name=f"openwebui_{uuid4()}", type="virtual")
        session.add(context)
        await session.flush()
        return context.id

    # Look up existing
    stmt = select(Conversation).where(Conversation.id == UUID(conversation_id))
    result = await session.execute(stmt)
    conversation = result.scalar_one_or_none()

    if conversation:
        return conversation.context_id

    # Conversation doesn't exist yet - create default context
    context = Context(name=f"openwebui_{uuid4()}", type="virtual")
    session.add(context)
    await session.flush()
    return context.id

@router.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    context_id: UUID = Depends(get_context_id),
    agent_service: AgentService = Depends(get_agent_service),
    session: AsyncSession = Depends(get_db),
):
    # context_id now available for service creation
```

**Telegram Adapter** (`src/interfaces/telegram/adapter.py`)

**Current:**
```python
async def _handle_message(self, message: Message):
    session_id = f"telegram_{chat_id}"

    async for chunk in self.dispatcher.stream_message(
        session_id=session_id,
        message=text,
        platform="telegram",
        platform_id=chat_id,
        ...
    ):
```

**Target:**
```python
async def _get_or_create_context(self, chat_id: str, db: AsyncSession) -> UUID:
    """Get or create context for Telegram chat."""
    stmt = select(Conversation).where(
        Conversation.platform == "telegram",
        Conversation.platform_id == str(chat_id),
    )
    result = await db.execute(stmt)
    conversation = result.scalar_one_or_none()

    if conversation:
        return conversation.context_id

    # Create new context for this Telegram user
    context = Context(
        name=f"telegram_{chat_id}",
        type="virtual",
        config={"platform": "telegram", "chat_id": chat_id}
    )
    db.add(context)
    await db.flush()
    return context.id

async def _handle_message(self, message: Message):
    chat_id = message.chat.id

    async with AsyncSessionLocal() as db:
        context_id = await self._get_or_create_context(chat_id, db)

        # Pass context_id to dispatcher/service
        agent_service = await get_agent_service(context_id, db)

        async for chunk in self.dispatcher.stream_message(
            session_id=f"telegram_{chat_id}",
            message=text,
            platform="telegram",
            platform_id=chat_id,
            db_session=db,
            agent_service=agent_service,
            context_id=context_id,  # NEW
            ...
        ):
```

#### 1.2 Dependency Injection Refactor

**Create Context-Aware Service Factory** (`src/core/core/service_factory.py` - NEW)

```python
"""Factory for creating context-aware AgentService instances."""

from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from core.core.config import Settings
from core.core.litellm_client import LiteLLMClient
from core.core.memory import MemoryStore
from core.core.service import AgentService
from core.tools.registry import ToolRegistry
from core.tools.loader import load_tool_registry
from core.tools.mcp_loader import load_mcp_tools_for_context


class ServiceFactory:
    """Factory for creating context-scoped AgentService instances."""

    def __init__(
        self,
        settings: Settings,
        litellm_client: LiteLLMClient,
    ):
        self._settings = settings
        self._litellm = litellm_client

        # Cache base tool registry (native tools only)
        self._base_tool_registry = load_tool_registry(settings.tools_config_path)

    async def create_service(
        self,
        context_id: UUID,
        session: AsyncSession,
    ) -> AgentService:
        """Create an AgentService instance for a specific context.

        This creates:
        - Context-specific MemoryStore
        - Context-specific ToolRegistry (with MCP tools)
        - Properly scoped dependencies
        """
        # Clone base registry to avoid mutation
        tool_registry = self._base_tool_registry.clone()

        # Load MCP tools for this context (using OAuth tokens)
        await load_mcp_tools_for_context(
            context_id=context_id,
            tool_registry=tool_registry,
            session=session,
            settings=self._settings,
        )

        # Create context-scoped memory store
        memory_store = MemoryStore(self._settings, context_id=context_id)
        await memory_store.ainit()

        # Create service
        return AgentService(
            settings=self._settings,
            litellm=self._litellm,
            memory=memory_store,
            tool_registry=tool_registry,
            context_id=context_id,
        )
```

**Update app.py** (`src/core/core/app.py`)

```python
# Remove global service singleton
# app.state.service = service_instance  # DELETE THIS

# Add service factory
from core.core.service_factory import ServiceFactory

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # --- STARTUP ---

    # ... existing provider registration ...

    # Create service factory (singleton)
    service_factory = ServiceFactory(
        settings=settings,
        litellm_client=litellm_client,
    )
    app.state.service_factory = service_factory

    # Remove MCP loading from startup
    # asyncio.create_task(load_mcp_tools(...))  # DELETE - now per-context

    yield

    # --- SHUTDOWN ---
    await shutdown_all_mcp_clients()  # NEW - cleanup all context clients
    await litellm_client.aclose()
    await token_manager.shutdown()

# Update dependency
def get_service_factory() -> ServiceFactory:
    return app.state.service_factory

async def get_agent_service(
    context_id: UUID,
    session: AsyncSession = Depends(get_db),
    factory: ServiceFactory = Depends(get_service_factory),
) -> AgentService:
    """Create context-scoped agent service."""
    return await factory.create_service(context_id, session)
```

#### 1.3 Memory Store Context Isolation

**Update MemoryStore** (`src/core/core/memory.py`)

```python
class MemoryStore:
    def __init__(self, settings: Settings, context_id: UUID | None = None):
        self._settings = settings
        self._context_id = context_id  # NEW
        self._vector_size = settings.qdrant_vector_size
        self._embedder = EmbedderClient(str(settings.embedder_url))

    async def store(self, record: MemoryRecord) -> None:
        """Store a memory with context isolation."""
        vector = await self._embedder.embed(record.text)

        payload = {
            "context_id": str(self._context_id),  # NEW - context isolation
            "conversation_id": record.conversation_id,
            "text": record.text,
        }

        point = PointStruct(
            id=uuid4().hex,
            vector=vector,
            payload=payload,
        )

        await self._client.upsert(
            collection_name=self._settings.qdrant_collection_name,
            points=[point],
        )

    async def search(
        self,
        query: str,
        limit: int = 5,
        conversation_id: str | None = None,
    ) -> list[MemoryRecord]:
        """Search memories with context isolation."""
        query_vector = await self._embedder.embed(query)

        # Build filter with context isolation
        filters = [
            FieldCondition(
                key="context_id",
                match=MatchValue(value=str(self._context_id)),
            )
        ]

        if conversation_id:
            filters.append(
                FieldCondition(
                    key="conversation_id",
                    match=MatchValue(value=conversation_id),
                )
            )

        query_filter = Filter(must=filters)

        results = await self._client.search(
            collection_name=self._settings.qdrant_collection_name,
            query_vector=query_vector,
            query_filter=query_filter,
            limit=limit,
        )

        return [
            MemoryRecord(
                conversation_id=hit.payload["conversation_id"],
                text=hit.payload["text"],
            )
            for hit in results
        ]
```

**Migration Script for Existing Data** (`scripts/migrate_memory_context.py` - NEW)

```python
"""Add context_id to existing Qdrant points."""

import asyncio
from uuid import UUID
from qdrant_client import QdrantClient
from sqlalchemy import select

from core.core.config import get_settings
from core.db.engine import AsyncSessionLocal
from core.db.models import Conversation


async def migrate_memory_contexts():
    """Add context_id to existing memory points."""
    settings = get_settings()

    # Connect to Qdrant
    client = QdrantClient(
        url=str(settings.qdrant_url),
        api_key=settings.qdrant_api_key,
    )

    # Get all points
    points, _ = client.scroll(
        collection_name=settings.qdrant_collection_name,
        limit=10000,
        with_payload=True,
        with_vectors=False,
    )

    print(f"Found {len(points)} memory points to migrate")

    # Map conversation_id → context_id
    conversation_to_context = {}

    async with AsyncSessionLocal() as session:
        stmt = select(Conversation)
        result = await session.execute(stmt)
        conversations = result.scalars().all()

        for conv in conversations:
            conversation_to_context[str(conv.id)] = str(conv.context_id)

    # Update points with context_id
    updated_points = []
    for point in points:
        conversation_id = point.payload.get("conversation_id")

        if conversation_id in conversation_to_context:
            # Add context_id to payload
            point.payload["context_id"] = conversation_to_context[conversation_id]
            updated_points.append(point)
        else:
            print(f"Warning: No conversation found for {conversation_id}")

    # Batch update
    if updated_points:
        client.upsert(
            collection_name=settings.qdrant_collection_name,
            points=updated_points,
        )
        print(f"Migrated {len(updated_points)} points with context_id")

    print("Migration complete!")


if __name__ == "__main__":
    asyncio.run(migrate_memory_contexts())
```

**Deliverables:**
- ✅ Context extraction from all adapters
- ✅ ServiceFactory for per-context service creation
- ✅ MemoryStore context isolation
- ✅ Memory migration script
- ✅ Updated dependency injection throughout

**Testing:**
```bash
# Test memory isolation
pytest tests/core/test_memory_context_isolation.py

# Test service factory
pytest tests/core/test_service_factory.py

# Test adapter context extraction
pytest tests/interfaces/test_context_extraction.py
```

---

### Phase 2: Tool Registry Isolation (Week 2)

**Goal:** Make tool registries context-aware with per-user permissions.

#### 2.1 Cloneable Tool Registry

**Update ToolRegistry** (`src/core/tools/registry.py`)

```python
class ToolRegistry:
    """Registry for managing available tools with cloning support."""

    def clone(self) -> "ToolRegistry":
        """Create a shallow copy of this registry.

        Used to create per-context registries without duplicating tool instances.
        """
        cloned = ToolRegistry()
        cloned._tools = self._tools.copy()  # Shallow copy of dict
        return cloned

    def filter_by_permissions(
        self,
        context_id: UUID,
        permissions: dict[str, bool],
    ) -> None:
        """Remove tools not allowed for this context.

        Args:
            context_id: Context to filter for
            permissions: Tool name → allowed mapping
        """
        filtered_tools = {
            name: tool
            for name, tool in self._tools.items()
            if permissions.get(name, True)  # Default allow
        }
        self._tools = filtered_tools
```

#### 2.2 Tool Permissions System

**Create Tool Permissions Model** (`src/core/db/models.py` - UPDATE)

```python
class ToolPermission(Base):
    """Per-context tool access permissions."""

    __tablename__ = "tool_permissions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    context_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contexts.id", ondelete="CASCADE"),
        index=True,
    )
    tool_name: Mapped[str] = mapped_column(String, index=True)
    allowed: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("context_id", "tool_name", name="uq_context_tool"),
    )
```

**Migration** (`alembic/versions/xxx_add_tool_permissions.py`)

```python
def upgrade() -> None:
    op.create_table(
        "tool_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("context_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("allowed", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["context_id"], ["contexts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("context_id", "tool_name", name="uq_context_tool"),
    )
    op.create_index(op.f("ix_tool_permissions_context_id"), "tool_permissions", ["context_id"])
    op.create_index(op.f("ix_tool_permissions_tool_name"), "tool_permissions", ["tool_name"])
```

#### 2.3 Permission Loading in ServiceFactory

**Update ServiceFactory** (`src/core/core/service_factory.py`)

```python
from sqlalchemy import select
from core.db.models import ToolPermission

class ServiceFactory:
    async def create_service(
        self,
        context_id: UUID,
        session: AsyncSession,
    ) -> AgentService:
        # Clone base registry
        tool_registry = self._base_tool_registry.clone()

        # Load permissions for this context
        stmt = select(ToolPermission).where(ToolPermission.context_id == context_id)
        result = await session.execute(stmt)
        permissions = {
            perm.tool_name: perm.allowed
            for perm in result.scalars().all()
        }

        # Filter tools by permissions
        if permissions:
            tool_registry.filter_by_permissions(context_id, permissions)

        # Load MCP tools for this context
        await load_mcp_tools_for_context(
            context_id=context_id,
            tool_registry=tool_registry,
            session=session,
            settings=self._settings,
        )

        # ... rest of service creation
```

**Deliverables:**
- ✅ Cloneable ToolRegistry
- ✅ ToolPermission database model
- ✅ Permission filtering in ServiceFactory
- ✅ Default allow-all policy (backward compatible)

**Testing:**
```bash
pytest tests/core/tools/test_registry_cloning.py
pytest tests/core/tools/test_permissions.py
```

---

### Phase 3: Context-Aware MCP Client Pool (Week 3)

**Goal:** Replace global MCP clients with per-context client pools using OAuth tokens.

#### 3.1 MCP Client Pool Manager

**Create MCP Client Pool** (`src/core/mcp/client_pool.py` - NEW)

```python
"""Per-context MCP client pool manager."""

import asyncio
import logging
from collections import defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.core.config import Settings
from core.db.oauth_models import OAuthToken
from core.mcp.client import McpClient
from core.providers import get_token_manager


LOGGER = logging.getLogger(__name__)


class McpClientPool:
    """Manages MCP clients per context with OAuth token support."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._pools: dict[UUID, list[McpClient]] = defaultdict(list)
        self._locks: dict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def get_clients(
        self,
        context_id: UUID,
        session: AsyncSession,
    ) -> list[McpClient]:
        """Get or create MCP clients for a context.

        This method:
        1. Checks cache for existing clients
        2. Loads OAuth tokens for this context
        3. Creates clients for each authorized provider
        4. Caches clients for reuse
        """
        # Check cache first
        if context_id in self._pools and self._pools[context_id]:
            # Verify clients are still connected
            valid_clients = []
            for client in self._pools[context_id]:
                if client.is_connected and await client.ping():
                    valid_clients.append(client)
                else:
                    LOGGER.warning(f"Client {client.name} disconnected, removing from pool")
                    try:
                        await client.disconnect()
                    except Exception as e:
                        LOGGER.error(f"Error disconnecting stale client: {e}")

            if valid_clients:
                self._pools[context_id] = valid_clients
                return valid_clients

        # Need to create new clients - lock to prevent duplicates
        async with self._locks[context_id]:
            # Double-check after acquiring lock
            if context_id in self._pools and self._pools[context_id]:
                return self._pools[context_id]

            # Load OAuth tokens for this context
            stmt = select(OAuthToken).where(OAuthToken.context_id == context_id)
            result = await session.execute(stmt)
            tokens = result.scalars().all()

            clients = []

            # Create clients for each authorized provider
            for token in tokens:
                provider = token.provider.lower()

                # Homey
                if provider == "homey" and self._settings.homey_mcp_url:
                    try:
                        client = McpClient(
                            url=str(self._settings.homey_mcp_url),
                            context_id=context_id,
                            oauth_provider="homey",  # Uses OAuth!
                            name="Homey",
                            auto_reconnect=True,
                            max_retries=3,
                            cache_ttl_seconds=300,
                        )
                        await client.connect()
                        clients.append(client)
                        LOGGER.info(f"Connected Homey MCP for context {context_id}")
                    except Exception as e:
                        LOGGER.error(f"Failed to connect Homey MCP for context {context_id}: {e}")

                # Context7 (future)
                elif provider == "context7" and self._settings.context7_mcp_url:
                    # Similar pattern
                    pass

            # Store in cache
            self._pools[context_id] = clients
            return clients

    async def disconnect_context(self, context_id: UUID) -> None:
        """Disconnect all clients for a context."""
        if context_id in self._pools:
            for client in self._pools[context_id]:
                try:
                    await client.disconnect()
                except Exception as e:
                    LOGGER.warning(f"Error disconnecting client {client.name}: {e}")

            del self._pools[context_id]
            LOGGER.info(f"Disconnected all MCP clients for context {context_id}")

    async def shutdown(self) -> None:
        """Disconnect all clients across all contexts."""
        for context_id in list(self._pools.keys()):
            await self.disconnect_context(context_id)

        LOGGER.info("All MCP clients disconnected")

    def get_health_status(self) -> dict[str, dict]:
        """Get health status of all client pools."""
        health = {}

        for context_id, clients in self._pools.items():
            context_health = []
            for client in clients:
                context_health.append({
                    "name": client.name,
                    "connected": client.is_connected,
                    "state": client.state.name,
                    "tools_count": len(client.tools),
                })

            health[str(context_id)] = {
                "clients": context_health,
                "total_clients": len(clients),
            }

        return health
```

#### 3.2 Context-Aware MCP Tool Loading

**Update MCP Loader** (`src/core/tools/mcp_loader.py`)

```python
# Remove global _active_clients
# _active_clients: list[McpClient] = []  # DELETE

from core.mcp.client_pool import McpClientPool

# Add module-level pool (singleton)
_client_pool: McpClientPool | None = None


def set_mcp_client_pool(pool: McpClientPool) -> None:
    """Register the global MCP client pool."""
    global _client_pool
    _client_pool = pool


def get_mcp_client_pool() -> McpClientPool:
    """Get the global MCP client pool."""
    if _client_pool is None:
        raise RuntimeError("MCP client pool not initialized")
    return _client_pool


async def load_mcp_tools_for_context(
    context_id: UUID,
    tool_registry: ToolRegistry,
    session: AsyncSession,
    settings: Settings,
) -> None:
    """Load MCP tools for a specific context using OAuth tokens.

    This replaces the global load_mcp_tools() function.
    """
    pool = get_mcp_client_pool()

    # Get clients for this context (creates if needed)
    clients = await pool.get_clients(context_id, session)

    if not clients:
        LOGGER.info(f"No MCP clients available for context {context_id}")
        return

    # Register tools from each client
    for client in clients:
        for mcp_tool in client.tools:
            wrapper = McpToolWrapper(
                mcp_client=client,
                mcp_tool=mcp_tool,
                server_name=client.name,
                prefix_name=True,
            )
            tool_registry.register(wrapper)
            LOGGER.debug(f"Registered {wrapper.name} for context {context_id}")

        LOGGER.info(
            f"Loaded {len(client.tools)} tools from {client.name} "
            f"for context {context_id}"
        )


async def shutdown_all_mcp_clients() -> None:
    """Shutdown all MCP clients across all contexts."""
    if _client_pool:
        await _client_pool.shutdown()
    LOGGER.info("All MCP client pools shut down")
```

#### 3.3 Initialize Client Pool in App Startup

**Update app.py** (`src/core/core/app.py`)

```python
from core.mcp.client_pool import McpClientPool
from core.tools.mcp_loader import set_mcp_client_pool, shutdown_all_mcp_clients

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # --- STARTUP ---

    # ... existing provider registration ...

    # Initialize MCP client pool
    mcp_pool = McpClientPool(settings)
    set_mcp_client_pool(mcp_pool)
    LOGGER.info("MCP client pool initialized")

    # Remove global MCP loading
    # asyncio.create_task(load_mcp_tools(...))  # DELETE

    # Create service factory
    service_factory = ServiceFactory(
        settings=settings,
        litellm_client=litellm_client,
    )
    app.state.service_factory = service_factory

    yield

    # --- SHUTDOWN ---
    await shutdown_all_mcp_clients()  # Uses new pool shutdown
    await litellm_client.aclose()
    await token_manager.shutdown()
```

**Deliverables:**
- ✅ McpClientPool for per-context client management
- ✅ OAuth token-based MCP client creation
- ✅ Client caching and health checking
- ✅ Graceful shutdown of all client pools
- ✅ Context-aware tool registration

**Testing:**
```bash
pytest tests/core/mcp/test_client_pool.py
pytest tests/core/mcp/test_context_oauth_integration.py
```

---

### Phase 4: Admin Dashboard Expansion (Week 4)

**Goal:** Transform diagnostics dashboard into comprehensive admin interface.

#### 4.1 Dashboard Architecture

**Current:**
```
/diagnostics/
  - Dashboard (overview)
  - Tools (tool listing)
  - Traces (recent traces)
```

**Target:**
```
/admin/
  - Overview (system health, request stats)
  - OAuth Connections (per-context OAuth management)
  - MCP Servers (health, tools, per-context)
  - Tools (tool listing with permissions)
  - Traces (detailed trace viewer)
  - Statistics (usage metrics)
  - Contexts (user/workspace management)
```

#### 4.2 Authentication & Authorization

**Admin API Key Auth** (`src/interfaces/http/auth.py` - NEW)

```python
"""Admin dashboard authentication."""

from fastapi import Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession

from core.core.config import Settings, get_settings
from core.db.engine import get_db


async def verify_admin_key(
    x_admin_key: str | None = Header(None),
    settings: Settings = Depends(get_settings),
) -> str:
    """Verify admin API key from header.

    Raises:
        HTTPException: If key is missing or invalid

    Returns:
        str: "admin" role
    """
    if not x_admin_key:
        raise HTTPException(
            status_code=401,
            detail="Missing X-Admin-Key header",
        )

    expected_key = settings.admin_api_key
    if not expected_key:
        raise HTTPException(
            status_code=503,
            detail="Admin API key not configured (set AGENT_ADMIN_API_KEY)",
        )

    if x_admin_key != expected_key:
        raise HTTPException(
            status_code=403,
            detail="Invalid admin API key",
        )

    return "admin"
```

**Add to Settings** (`src/core/core/config.py`)

```python
class Settings(BaseSettings):
    # ... existing settings ...

    # Admin Dashboard
    admin_api_key: str | None = Field(
        default=None,
        description="API key for admin dashboard access (generate with: openssl rand -hex 32)",
    )
```

**Update .env.template**

```bash
# Admin Dashboard
AGENT_ADMIN_API_KEY=<generate-with-openssl-rand-hex-32>
```

#### 4.3 OAuth Connections Management

**OAuth Admin Endpoints** (`src/interfaces/http/admin/oauth.py` - NEW)

```python
"""Admin endpoints for OAuth connection management."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.db.models import Context
from core.db.oauth_models import OAuthToken
from core.providers import get_token_manager
from interfaces.http.auth import verify_admin_key


router = APIRouter(prefix="/admin/oauth", tags=["admin-oauth"])


class OAuthConnectionInfo(BaseModel):
    """OAuth connection information for admin view."""

    id: str
    context_id: str
    context_name: str
    provider: str
    token_type: str
    expires_at: str
    scope: str | None
    created_at: str
    last_validated: str | None
    is_valid: bool


class OAuthConnectionsResponse(BaseModel):
    """List of OAuth connections with context info."""

    connections: list[OAuthConnectionInfo]
    total: int


@router.get("/connections", response_model=OAuthConnectionsResponse)
async def list_oauth_connections(
    context_id: UUID | None = None,
    provider: str | None = None,
    session: AsyncSession = Depends(get_db),
    _admin: str = Depends(verify_admin_key),
) -> OAuthConnectionsResponse:
    """List all OAuth connections across contexts (admin only).

    Query params:
        context_id: Filter by context
        provider: Filter by provider (homey, github, etc.)
    """
    # Build query with filters
    stmt = select(OAuthToken, Context).join(
        Context, OAuthToken.context_id == Context.id
    )

    if context_id:
        stmt = stmt.where(OAuthToken.context_id == context_id)
    if provider:
        stmt = stmt.where(OAuthToken.provider == provider.lower())

    result = await session.execute(stmt)
    rows = result.all()

    # Build response with validation status
    token_manager = get_token_manager()
    connections = []

    for token, context in rows:
        # Try to validate token
        is_valid = False
        try:
            access_token = await token_manager.get_token(
                token.provider, token.context_id
            )
            is_valid = access_token is not None
        except Exception:
            pass

        connections.append(
            OAuthConnectionInfo(
                id=str(token.id),
                context_id=str(token.context_id),
                context_name=context.name,
                provider=token.provider,
                token_type=token.token_type,
                expires_at=token.expires_at.isoformat(),
                scope=token.scope,
                created_at=token.created_at.isoformat(),
                last_validated=None,  # TODO: Track validation times
                is_valid=is_valid,
            )
        )

    return OAuthConnectionsResponse(
        connections=connections,
        total=len(connections),
    )


@router.delete("/connections/{token_id}")
async def revoke_oauth_connection(
    token_id: UUID,
    session: AsyncSession = Depends(get_db),
    _admin: str = Depends(verify_admin_key),
) -> dict:
    """Revoke an OAuth connection (admin only)."""
    stmt = select(OAuthToken).where(OAuthToken.id == token_id)
    result = await session.execute(stmt)
    token = result.scalar_one_or_none()

    if not token:
        raise HTTPException(status_code=404, detail="Token not found")

    # Delete token from database
    await session.delete(token)
    await session.commit()

    # Invalidate MCP client cache for this context
    from core.tools.mcp_loader import get_mcp_client_pool

    pool = get_mcp_client_pool()
    await pool.disconnect_context(token.context_id)

    return {
        "status": "revoked",
        "token_id": str(token_id),
        "context_id": str(token.context_id),
        "provider": token.provider,
    }


@router.post("/connections/{token_id}/validate")
async def validate_oauth_connection(
    token_id: UUID,
    session: AsyncSession = Depends(get_db),
    _admin: str = Depends(verify_admin_key),
) -> dict:
    """Validate an OAuth connection by testing the token."""
    stmt = select(OAuthToken).where(OAuthToken.id == token_id)
    result = await session.execute(stmt)
    token = result.scalar_one_or_none()

    if not token:
        raise HTTPException(status_code=404, detail="Token not found")

    # Try to get token (triggers refresh if needed)
    token_manager = get_token_manager()

    try:
        access_token = await token_manager.get_token(
            token.provider, token.context_id
        )

        if access_token:
            return {
                "status": "valid",
                "token_id": str(token_id),
                "provider": token.provider,
                "expires_at": token.expires_at.isoformat(),
            }
        else:
            return {
                "status": "invalid",
                "token_id": str(token_id),
                "provider": token.provider,
                "error": "Token validation failed",
            }
    except Exception as e:
        return {
            "status": "error",
            "token_id": str(token_id),
            "provider": token.provider,
            "error": str(e),
        }
```

#### 4.4 MCP Server Health Dashboard

**MCP Admin Endpoints** (`src/interfaces/http/admin/mcp.py` - NEW)

```python
"""Admin endpoints for MCP server management."""

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.tools.mcp_loader import get_mcp_client_pool
from interfaces.http.auth import verify_admin_key


router = APIRouter(prefix="/admin/mcp", tags=["admin-mcp"])


class McpClientInfo(BaseModel):
    """MCP client information."""

    name: str
    connected: bool
    state: str
    tools_count: int
    resources_count: int
    prompts_count: int
    cache_stale: bool


class McpContextHealth(BaseModel):
    """MCP health for a specific context."""

    context_id: str
    clients: list[McpClientInfo]
    total_clients: int


class McpHealthResponse(BaseModel):
    """Overall MCP health across all contexts."""

    contexts: dict[str, McpContextHealth]
    total_contexts: int
    total_clients: int


@router.get("/health", response_model=McpHealthResponse)
async def get_mcp_health(
    context_id: UUID | None = None,
    _admin: str = Depends(verify_admin_key),
) -> McpHealthResponse:
    """Get MCP health status across all contexts (admin only)."""
    pool = get_mcp_client_pool()
    health_data = pool.get_health_status()

    # Filter by context if specified
    if context_id:
        context_str = str(context_id)
        if context_str in health_data:
            health_data = {context_str: health_data[context_str]}
        else:
            health_data = {}

    # Build response
    contexts = {}
    total_clients = 0

    for ctx_id, ctx_health in health_data.items():
        client_infos = []
        for client_data in ctx_health["clients"]:
            client_infos.append(
                McpClientInfo(
                    name=client_data["name"],
                    connected=client_data["connected"],
                    state=client_data["state"],
                    tools_count=client_data["tools_count"],
                    resources_count=client_data.get("resources_count", 0),
                    prompts_count=client_data.get("prompts_count", 0),
                    cache_stale=client_data.get("cache_stale", False),
                )
            )

        contexts[ctx_id] = McpContextHealth(
            context_id=ctx_id,
            clients=client_infos,
            total_clients=len(client_infos),
        )
        total_clients += len(client_infos)

    return McpHealthResponse(
        contexts=contexts,
        total_contexts=len(contexts),
        total_clients=total_clients,
    )


@router.post("/contexts/{context_id}/reconnect")
async def reconnect_mcp_context(
    context_id: UUID,
    session: AsyncSession = Depends(get_db),
    _admin: str = Depends(verify_admin_key),
) -> dict:
    """Force reconnect all MCP clients for a context (admin only)."""
    pool = get_mcp_client_pool()

    # Disconnect existing clients
    await pool.disconnect_context(context_id)

    # Get new clients (triggers reconnect)
    clients = await pool.get_clients(context_id, session)

    return {
        "status": "reconnected",
        "context_id": str(context_id),
        "clients_count": len(clients),
        "clients": [
            {"name": client.name, "connected": client.is_connected}
            for client in clients
        ],
    }
```

#### 4.5 Context Management

**Context Admin Endpoints** (`src/interfaces/http/admin/contexts.py` - NEW)

```python
"""Admin endpoints for context/workspace management."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.db.models import Context, Conversation
from interfaces.http.auth import verify_admin_key


router = APIRouter(prefix="/admin/contexts", tags=["admin-contexts"])


class ContextInfo(BaseModel):
    """Context information for admin view."""

    id: str
    name: str
    type: str
    config: dict
    conversations_count: int
    created_at: str


class ContextsListResponse(BaseModel):
    """List of contexts."""

    contexts: list[ContextInfo]
    total: int


@router.get("", response_model=ContextsListResponse)
async def list_contexts(
    session: AsyncSession = Depends(get_db),
    _admin: str = Depends(verify_admin_key),
) -> ContextsListResponse:
    """List all contexts with conversation counts (admin only)."""
    # Get contexts with conversation counts
    stmt = (
        select(
            Context,
            func.count(Conversation.id).label("conv_count"),
        )
        .outerjoin(Conversation, Conversation.context_id == Context.id)
        .group_by(Context.id)
    )

    result = await session.execute(stmt)
    rows = result.all()

    contexts = []
    for context, conv_count in rows:
        contexts.append(
            ContextInfo(
                id=str(context.id),
                name=context.name,
                type=context.type,
                config=context.config or {},
                conversations_count=conv_count,
                created_at=context.created_at.isoformat(),
            )
        )

    return ContextsListResponse(
        contexts=contexts,
        total=len(contexts),
    )


@router.get("/{context_id}", response_model=ContextInfo)
async def get_context(
    context_id: UUID,
    session: AsyncSession = Depends(get_db),
    _admin: str = Depends(verify_admin_key),
) -> ContextInfo:
    """Get detailed context information (admin only)."""
    stmt = (
        select(
            Context,
            func.count(Conversation.id).label("conv_count"),
        )
        .outerjoin(Conversation, Conversation.context_id == Context.id)
        .where(Context.id == context_id)
        .group_by(Context.id)
    )

    result = await session.execute(stmt)
    row = result.one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail="Context not found")

    context, conv_count = row

    return ContextInfo(
        id=str(context.id),
        name=context.name,
        type=context.type,
        config=context.config or {},
        conversations_count=conv_count,
        created_at=context.created_at.isoformat(),
    )
```

#### 4.6 Rename Diagnostics to Admin

**Rename Router** (`src/interfaces/http/diagnostics.py` → `src/interfaces/http/admin/diagnostics.py`)

```python
# Update router prefix
router = APIRouter(prefix="/admin/diagnostics", tags=["admin-diagnostics"])

# Add auth dependency to all endpoints
@router.get("/dashboard")
async def dashboard(
    _admin: str = Depends(verify_admin_key),
):
    # ... existing code
```

**Create Admin Router Module** (`src/interfaces/http/admin/__init__.py`)

```python
"""Admin dashboard router aggregation."""

from fastapi import APIRouter

from .contexts import router as contexts_router
from .diagnostics import router as diagnostics_router
from .mcp import router as mcp_router
from .oauth import router as oauth_router


# Aggregate all admin routers
admin_router = APIRouter(prefix="/admin")

admin_router.include_router(diagnostics_router)
admin_router.include_router(oauth_router)
admin_router.include_router(mcp_router)
admin_router.include_router(contexts_router)


__all__ = ["admin_router"]
```

**Update app.py**

```python
from interfaces.http.admin import admin_router

# Replace diagnostics router
# app.include_router(diagnostics_router)  # DELETE
app.include_router(admin_router)  # NEW
```

**Deliverables:**
- ✅ Admin API key authentication
- ✅ OAuth connections management UI
- ✅ MCP health monitoring per context
- ✅ Context/workspace management
- ✅ Renamed diagnostics → admin
- ✅ Protected all admin endpoints

**Testing:**
```bash
pytest tests/interfaces/admin/test_auth.py
pytest tests/interfaces/admin/test_oauth.py
pytest tests/interfaces/admin/test_mcp.py
pytest tests/interfaces/admin/test_contexts.py
```

---

### Phase 5: Integration & Polish (Week 5)

**Goal:** End-to-end testing, documentation, and onboarding.

#### 5.1 End-to-End Testing

**Multi-User Scenario Tests** (`tests/integration/test_multi_user.py` - NEW)

```python
"""End-to-end multi-user isolation tests."""

import pytest
from uuid import uuid4

from core.db.models import Context
from core.db.oauth_models import OAuthToken


@pytest.mark.asyncio
async def test_user_tool_isolation(client, db_session):
    """Test that users can't access each other's MCP tools."""
    # Create two contexts
    context_a = Context(name="user_a", type="virtual")
    context_b = Context(name="user_b", type="virtual")
    db_session.add_all([context_a, context_b])
    await db_session.flush()

    # User A connects Homey
    token_a = OAuthToken(
        context_id=context_a.id,
        provider="homey",
        access_token="token_a",
        token_type="Bearer",
        expires_at=...,
    )
    db_session.add(token_a)
    await db_session.commit()

    # User A requests should see Homey tools
    response_a = await client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": str(uuid4()),
            "metadata": {"context_id": str(context_a.id)},
            "messages": [{"role": "user", "content": "List available tools"}],
        },
    )

    # User B requests should NOT see Homey tools
    response_b = await client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": str(uuid4()),
            "metadata": {"context_id": str(context_b.id)},
            "messages": [{"role": "user", "content": "List available tools"}],
        },
    )

    # Assert tool isolation
    # ... detailed assertions


@pytest.mark.asyncio
async def test_memory_isolation(client, db_session):
    """Test that users can't access each other's memories."""
    # Create contexts and conversations
    # Store memories for context A
    # Search from context B
    # Assert no leakage


@pytest.mark.asyncio
async def test_oauth_token_isolation(client, db_session):
    """Test that OAuth tokens are properly scoped to contexts."""
    # ...
```

#### 5.2 User Onboarding Script

**Context Creation Script** (`scripts/create_user_context.py` - NEW)

```python
"""Create a new user context for multi-tenant deployment."""

import asyncio
import sys
from uuid import uuid4

from sqlalchemy import select

from core.db.engine import AsyncSessionLocal
from core.db.models import Context


async def create_user_context(
    name: str,
    context_type: str = "virtual",
    config: dict | None = None,
) -> str:
    """Create a new user context.

    Args:
        name: Context name (e.g., "magnus", "product_owner_2")
        context_type: Type (virtual, git, local)
        config: Optional config dict

    Returns:
        Context UUID
    """
    async with AsyncSessionLocal() as session:
        # Check if context exists
        stmt = select(Context).where(Context.name == name)
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            print(f"❌ Context '{name}' already exists (ID: {existing.id})")
            return str(existing.id)

        # Create new context
        context = Context(
            name=name,
            type=context_type,
            config=config or {},
        )
        session.add(context)
        await session.commit()
        await session.refresh(context)

        print(f"✅ Created context '{name}' (ID: {context.id})")
        print(f"\nUser can now:")
        print(f"1. Access via OpenWebUI (conversations auto-create)")
        print(f"2. Authorize OAuth: /webui/oauth/status/<conversation_id>/homey")
        print(f"3. View in admin: /admin/contexts/{context.id}")

        return str(context.id)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/create_user_context.py <name> [type] [config_json]")
        print("\nExample:")
        print("  python scripts/create_user_context.py magnus")
        print("  python scripts/create_user_context.py product_owner_2 virtual")
        sys.exit(1)

    name = sys.argv[1]
    context_type = sys.argv[2] if len(sys.argv) > 2 else "virtual"

    context_id = asyncio.run(create_user_context(name, context_type))
    print(f"\n📋 Context ID: {context_id}")
```

**Usage:**
```bash
cd services/agent
poetry run python scripts/create_user_context.py magnus
poetry run python scripts/create_user_context.py product_owner_2
poetry run python scripts/create_user_context.py product_owner_3
# ... repeat for all 6 product owners
```

#### 5.3 Documentation Updates

**Update OAUTH_SETUP.md**
- Add multi-user OAuth flow
- Document context isolation
- Admin dashboard OAuth management

**Create MULTI_USER_GUIDE.md** (`docs/MULTI_USER_GUIDE.md` - NEW)

```markdown
# Multi-User Platform Guide

## Overview

The AI Agent Platform supports multiple users (contexts) with full isolation:
- Separate OAuth tokens per user
- Context-specific MCP connections
- Isolated conversation histories and memories
- Per-user tool permissions

## Architecture

```
User A (Context A)           User B (Context B)
    ↓                             ↓
OpenWebUI Conversation        Telegram Chat
    ↓                             ↓
Context A Services            Context B Services
- ToolRegistry A              - ToolRegistry B
- MCP Clients A               - MCP Clients B
- MemoryStore A               - MemoryStore B
    ↓                             ↓
Isolated Resources            Isolated Resources
```

## User Onboarding

### 1. Create Context
```bash
poetry run python scripts/create_user_context.py <username>
```

### 2. User Authorizes OAuth (via WebUI)
- User starts conversation in OpenWebUI
- Tries to use Homey tool → Auth required message
- Clicks authorization link
- Completes OAuth flow
- Tools now work

### 3. Admin Monitors (optional)
- Admin dashboard: `/admin/contexts`
- View OAuth connections: `/admin/oauth/connections`
- Check MCP health: `/admin/mcp/health`

## For Product Owners

### Accessing the Platform
1. Go to OpenWebUI: https://your-domain.com
2. Start a new conversation
3. Ask: "What tools do you have available?"
4. Authorize any required services (OAuth links provided)

### Connecting Azure DevOps
[Instructions for Azure DevOps OAuth setup]

### Connecting Homey
[Instructions for Homey OAuth setup]

## For Platform Administrators

### Adding New Users
```bash
poetry run python scripts/create_user_context.py new_user_name
```

### Viewing OAuth Connections
```bash
curl -H "X-Admin-Key: $ADMIN_API_KEY" \
  https://your-domain.com/admin/oauth/connections
```

### Revoking Access
```bash
curl -X DELETE \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  https://your-domain.com/admin/oauth/connections/<token_id>
```

### Monitoring MCP Health
```bash
curl -H "X-Admin-Key: $ADMIN_API_KEY" \
  https://your-domain.com/admin/mcp/health
```

## Troubleshooting

### User Can't Access Tools
1. Check OAuth status in admin dashboard
2. Verify context exists: `/admin/contexts`
3. Check MCP health: `/admin/mcp/health?context_id=<id>`
4. Review logs: `docker compose logs agent | grep <context_id>`

### Memory Not Working
1. Verify context_id in Qdrant payloads
2. Run migration: `poetry run python scripts/migrate_memory_context.py`
3. Check Qdrant collection schema

## Security Considerations

- OAuth tokens are encrypted at rest (PostgreSQL)
- Context isolation prevents data leakage
- Admin API key required for management endpoints
- HTTPS mandatory for production
```

**Deliverables:**
- ✅ End-to-end integration tests
- ✅ User onboarding script
- ✅ Comprehensive documentation
- ✅ Admin guide
- ✅ Troubleshooting guide

---

## File Change Inventory

### New Files (20)

| File | Purpose | Lines |
|------|---------|-------|
| `src/core/core/service_factory.py` | Per-context service creation | ~150 |
| `src/core/mcp/client_pool.py` | MCP client pool manager | ~250 |
| `src/interfaces/http/auth.py` | Admin authentication | ~50 |
| `src/interfaces/http/admin/__init__.py` | Admin router aggregation | ~20 |
| `src/interfaces/http/admin/oauth.py` | OAuth admin endpoints | ~200 |
| `src/interfaces/http/admin/mcp.py` | MCP admin endpoints | ~150 |
| `src/interfaces/http/admin/contexts.py` | Context management | ~150 |
| `alembic/versions/xxx_add_tool_permissions.py` | Tool permissions migration | ~40 |
| `scripts/create_user_context.py` | User onboarding script | ~80 |
| `scripts/migrate_memory_context.py` | Memory migration script | ~100 |
| `tests/core/test_service_factory.py` | Service factory tests | ~200 |
| `tests/core/test_memory_context_isolation.py` | Memory isolation tests | ~150 |
| `tests/core/mcp/test_client_pool.py` | Client pool tests | ~300 |
| `tests/interfaces/test_context_extraction.py` | Adapter context tests | ~150 |
| `tests/interfaces/admin/test_auth.py` | Admin auth tests | ~100 |
| `tests/interfaces/admin/test_oauth.py` | OAuth admin tests | ~200 |
| `tests/interfaces/admin/test_mcp.py` | MCP admin tests | ~150 |
| `tests/interfaces/admin/test_contexts.py` | Context admin tests | ~150 |
| `tests/integration/test_multi_user.py` | Multi-user e2e tests | ~400 |
| `docs/MULTI_USER_GUIDE.md` | User documentation | ~300 |

**Total New Code: ~3,290 lines**

### Modified Files (15)

| File | Changes | Impact |
|------|---------|--------|
| `src/core/core/app.py` | Remove global service, add factory | Major |
| `src/core/core/config.py` | Add admin_api_key setting | Minor |
| `src/core/core/service.py` | Add context_id parameter | Minor |
| `src/core/core/memory.py` | Add context isolation | Medium |
| `src/core/tools/registry.py` | Add clone() and filtering | Medium |
| `src/core/tools/mcp_loader.py` | Replace global with pool | Major |
| `src/core/db/models.py` | Add ToolPermission model | Medium |
| `src/interfaces/http/openwebui_adapter.py` | Extract context_id | Medium |
| `src/interfaces/telegram/adapter.py` | Extract context_id | Medium |
| `src/interfaces/http/diagnostics.py` | Move to admin/, add auth | Medium |
| `.env.template` | Add AGENT_ADMIN_API_KEY | Minor |
| `docs/OAUTH_SETUP.md` | Add multi-user flow | Minor |
| `docs/ARCHITECTURE.md` | Update with context flow | Medium |
| `README.md` | Add multi-user info | Minor |
| `pyproject.toml` | Add admin script entry point | Minor |

**Total Modified: ~1,500 lines changed**

---

## Testing Strategy

### Unit Tests (Per Phase)

**Phase 1:**
- Context extraction from adapters
- ServiceFactory creation
- Memory context filtering

**Phase 2:**
- ToolRegistry cloning
- Permission filtering
- ToolPermission CRUD

**Phase 3:**
- McpClientPool management
- OAuth token-based client creation
- Client health checking

**Phase 4:**
- Admin authentication
- OAuth admin endpoints
- MCP admin endpoints
- Context admin endpoints

### Integration Tests

**Multi-User Scenarios:**
```python
# Test user A can't access user B's tools
# Test user A can't search user B's memories
# Test OAuth tokens are context-isolated
# Test MCP clients are context-isolated
# Test concurrent requests from different users
```

### Manual Testing Checklist

```markdown
- [ ] Create 2 test contexts
- [ ] User A connects Homey via OAuth
- [ ] User B starts conversation (no Homey tools visible)
- [ ] User A uses Homey tool successfully
- [ ] User B tries Homey tool (fails with auth message)
- [ ] User B connects their own Homey
- [ ] Both users have separate Homey connections
- [ ] Admin dashboard shows both contexts
- [ ] Admin can view each user's OAuth tokens
- [ ] Admin can revoke User A's token
- [ ] User A's tools stop working
- [ ] Memory search returns only own context results
```

### Performance Testing

**Load Tests:**
```python
# 6 concurrent users
# Each making requests every 2 seconds
# Monitor:
# - MCP client pool size
# - Memory usage
# - Request latency
# - Database connection pool
```

**Expected Performance:**
- Request latency: <500ms (cached MCP clients)
- Memory overhead: ~50MB per context
- Database connections: <20 active
- MCP client reconnects: <1 per hour

---

## Migration Path

### Pre-Migration Checklist

```markdown
- [ ] Backup production database
- [ ] Test migration on staging environment
- [ ] Document rollback procedure
- [ ] Notify users of maintenance window
- [ ] Prepare admin API key
```

### Migration Steps (Production)

**1. Deploy Code (Zero Downtime)**
```bash
# Pull latest code
git pull origin main

# Install dependencies
cd services/agent
poetry install

# Build new Docker image
docker compose build agent

# Deploy with rolling update
docker compose up -d agent
```

**2. Run Database Migrations**
```bash
# Tool permissions table
poetry run alembic upgrade head

# Verify schema
poetry run alembic current
```

**3. Migrate Memory Context Data**
```bash
# Add context_id to existing Qdrant points
poetry run python scripts/migrate_memory_context.py

# Verify migration
# Check Qdrant UI for context_id in payloads
```

**4. Create User Contexts**
```bash
# For each existing user (if any)
poetry run python scripts/create_user_context.py magnus
poetry run python scripts/create_user_context.py product_owner_2
# ... etc
```

**5. Configure Admin Access**
```bash
# Generate admin API key
openssl rand -hex 32

# Add to .env
echo "AGENT_ADMIN_API_KEY=<generated_key>" >> .env

# Restart agent
docker compose restart agent
```

**6. Verify Multi-Tenancy**
```bash
# Test admin dashboard
curl -H "X-Admin-Key: $ADMIN_API_KEY" \
  http://localhost:8000/admin/contexts

# Test context isolation (manual)
# Create 2 test conversations
# Verify separate tool access
```

**7. Monitor**
```bash
# Watch logs
docker compose logs -f agent

# Check MCP health
curl -H "X-Admin-Key: $ADMIN_API_KEY" \
  http://localhost:8000/admin/mcp/health

# Monitor metrics
docker compose logs agent | grep -i "context\|mcp\|oauth"
```

### Rollback Procedure

**If Issues Arise:**
```bash
# 1. Revert code
git revert <commit_hash>
docker compose build agent
docker compose up -d agent

# 2. Rollback database
poetry run alembic downgrade -1

# 3. Restore Qdrant backup (if needed)
# ... provider-specific restore procedure

# 4. Verify system health
curl http://localhost:8000/healthz
```

---

## Success Criteria

### Functional Requirements

- ✅ **Context Isolation**: Users cannot access each other's tools, memories, or OAuth tokens
- ✅ **OAuth Per-Context**: Each user can connect their own OAuth providers
- ✅ **MCP Per-Context**: MCP clients use context-specific OAuth tokens
- ✅ **Memory Isolation**: Vector search respects context boundaries
- ✅ **Admin Dashboard**: Full visibility into contexts, OAuth, MCP health
- ✅ **Multi-Adapter Support**: Works with OpenWebUI, Telegram, future Slack

### Performance Requirements

- ✅ **Request Latency**: <500ms for cached MCP clients
- ✅ **Concurrent Users**: Support 6+ users simultaneously
- ✅ **Memory Overhead**: <100MB per active context
- ✅ **MCP Client Pooling**: Reuse connections, max 3 reconnects/hour

### Security Requirements

- ✅ **Token Isolation**: OAuth tokens scoped to context
- ✅ **Admin Authentication**: API key required for admin endpoints
- ✅ **Data Encryption**: PostgreSQL encryption at rest
- ✅ **HTTPS**: Required for production deployment
- ✅ **No Token Leakage**: Tokens never exposed in logs or responses

### Testing Requirements

- ✅ **Unit Test Coverage**: >80% for new code
- ✅ **Integration Tests**: Multi-user scenarios passing
- ✅ **Manual Testing**: Checklist completed
- ✅ **Load Testing**: 6 concurrent users sustained

### Documentation Requirements

- ✅ **User Guide**: Multi-user guide complete
- ✅ **Admin Guide**: Context/OAuth management documented
- ✅ **API Docs**: Admin endpoints documented
- ✅ **Troubleshooting**: Common issues covered

---

## Future Enhancements (Post-MVP)

### Phase 6: Advanced Features (Future)

**1. User Self-Service**
- Web UI for users to create their own contexts
- OAuth connection management in user settings
- Tool permission requests/approvals

**2. Slack Adapter**
- Slack workspace → context mapping
- Per-channel conversations
- OAuth via Slack app

**3. Event Triggers**
- Azure DevOps webhook integration
- Scheduled skill execution
- Cross-context notifications (opt-in)

**4. Enhanced Statistics**
- Per-context usage dashboards
- Cost attribution (OpenRouter API costs)
- Tool usage analytics
- Performance metrics

**5. Tool Marketplace**
- Shared tools across contexts (opt-in)
- Tool versioning and updates
- Dependency management

**6. Advanced Memory**
- Separate Qdrant collections per context (scale)
- Cross-context memory sharing (explicit consent)
- Memory retention policies per context

**7. Audit Logging**
- All admin actions logged
- OAuth authorization events tracked
- Tool usage audit trail
- Compliance reporting

---

## Risk Assessment

### High Risk

**1. MCP Client Pool Complexity**
- **Risk**: Concurrent access, connection leaks, state management
- **Mitigation**: Extensive testing, health monitoring, automatic cleanup
- **Fallback**: Disable MCP pooling, fall back to global clients

**2. Memory Migration**
- **Risk**: Data loss during Qdrant payload updates
- **Mitigation**: Backup before migration, verify counts
- **Fallback**: Restore from backup, defer migration

### Medium Risk

**3. Performance Degradation**
- **Risk**: Per-request service creation overhead
- **Mitigation**: Profile performance, optimize hot paths, cache where possible
- **Fallback**: Introduce service caching layer

**4. OAuth Token Race Conditions**
- **Risk**: Concurrent refresh attempts
- **Mitigation**: Database locks, refresh token handling in TokenManager
- **Fallback**: User re-authorizes (acceptable UX)

### Low Risk

**5. Admin Dashboard Auth**
- **Risk**: API key exposure
- **Mitigation**: Environment variable, never commit, rotate regularly
- **Fallback**: Disable admin routes temporarily

**6. Tool Permission Complexity**
- **Risk**: Users confused by missing tools
- **Mitigation**: Clear error messages, admin visibility
- **Fallback**: Default allow-all (current behavior)

---

## Timeline

| Phase | Duration | Deliverables |
|-------|----------|-------------|
| **Phase 1: Foundation** | Week 1 | Context propagation, memory isolation |
| **Phase 2: Tool Registry** | Week 2 | Cloning, permissions, filtering |
| **Phase 3: MCP Client Pool** | Week 3 | Per-context MCP clients, OAuth integration |
| **Phase 4: Admin Dashboard** | Week 4 | OAuth/MCP/Context management UI |
| **Phase 5: Integration** | Week 5 | Testing, documentation, onboarding |

**Total Timeline: 5 weeks (full-time)**

**Parallel Work Possible:**
- Phase 1 + Phase 2 can overlap (different files)
- Admin UI (Phase 4) can start during Phase 3
- Documentation can be written throughout

**Realistic Timeline: 3-4 weeks with focus**

---

## Resources Required

### Development
- **Backend Developer**: 1 FTE for 4-5 weeks
- **Testing**: 0.5 FTE for weeks 4-5

### Infrastructure
- **Database**: PostgreSQL (existing)
- **Vector Store**: Qdrant (existing)
- **Monitoring**: Logs + admin dashboard
- **Secrets**: Admin API key generation

### Documentation
- **User Guide**: 2 days
- **Admin Guide**: 1 day
- **API Docs**: 1 day

---

## Open Questions

1. **Context Naming Strategy**
   - Manual names (e.g., "magnus", "product_owner_2")?
   - Auto-generated (e.g., "user_<uuid>")?
   - Email-based (e.g., "magnus@company.com")?

2. **Default Tool Permissions**
   - Allow-all by default (easier onboarding)?
   - Deny-by-default (more secure)?
   - Per-tool defaults in config?

3. **Memory Retention**
   - Same retention policy for all contexts?
   - Per-context configurable retention?
   - Admin-set retention limits?

4. **Admin Dashboard Access**
   - Single admin API key for everyone?
   - Per-admin API keys with audit trail?
   - Role-based admin access (super admin vs context admin)?

5. **MCP Client Caching**
   - Cache duration: 5 minutes? 1 hour? Configurable?
   - Cache invalidation triggers?
   - Max clients per context?

---

## Appendix: Key Design Decisions

### Decision 1: Per-Request Service Creation

**Options Considered:**
- A) Global singleton service (current)
- B) Per-request service creation (chosen)
- C) Per-context service singleton with cache

**Decision: B**
- **Pros**: Clean isolation, no shared state, easy to reason about
- **Cons**: Performance overhead (mitigated by component reuse)
- **Rationale**: Correctness over performance, can optimize later

### Decision 2: MCP Client Pool

**Options Considered:**
- A) Global clients with context parameter (leaky abstraction)
- B) Per-context client pool (chosen)
- C) Per-request client creation (too slow)

**Decision: B**
- **Pros**: True isolation, OAuth integration, health management
- **Cons**: Complexity in lifecycle management
- **Rationale**: Necessary for OAuth, aligns with architecture

### Decision 3: Admin API Key Auth

**Options Considered:**
- A) No auth (localhost only)
- B) Simple API key (chosen)
- C) OAuth for admins
- D) Session-based auth

**Decision: B**
- **Pros**: Simple, stateless, works with curl/scripts
- **Cons**: Single key shared (can add rotation later)
- **Rationale**: Pragmatic for MVP, can enhance later

### Decision 4: Memory Context Migration

**Options Considered:**
- A) Separate Qdrant collections per context
- B) Add context_id to payloads (chosen)
- C) Rebuild memory from scratch

**Decision: B**
- **Pros**: Minimal disruption, preserves data, filter-based isolation
- **Cons**: All contexts in one collection (scaling limit ~100k contexts)
- **Rationale**: Sufficient for 6-50 users, can migrate to collections later

---

## Version History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-01-05 | Claude | Initial plan created |

---

**END OF PLAN**
