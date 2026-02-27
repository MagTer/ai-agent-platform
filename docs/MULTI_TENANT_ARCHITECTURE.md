

# Multi-Tenant Architecture

This document describes the multi-tenant architecture implemented in the AI Agent Platform, enabling isolation of contexts, conversations, tools, and data across multiple users or workspaces.

## Table of Contents

- [Overview](#overview)
- [Core Concepts](#core-concepts)
- [Architecture Components](#architecture-components)
- [Context Isolation](#context-isolation)
- [Request Flow](#request-flow)
- [Security Model](#security-model)
- [Admin API](#admin-api)
- [Migration Guide](#migration-guide)

---

## Overview

The AI Agent Platform implements a **context-based multi-tenant architecture** where each tenant (user, workspace, or project) operates within an isolated context. Contexts provide:

- **Data Isolation**: Conversations, messages, and memories are scoped to contexts
- **OAuth Isolation**: Each context has its own OAuth tokens for MCP integrations
- **Tool Permissions**: Contexts can have custom tool access policies
- **Resource Isolation**: MCP clients and tool registries are created per-request per-context

### Design Goals

1. **Security**: Complete isolation between contexts (no data leakage)
2. **Performance**: Efficient caching and reuse of shared resources
3. **Scalability**: Support hundreds of concurrent contexts
4. **Flexibility**: Allow per-context customization (tools, permissions, integrations)

---

## Core Concepts

### User

A `User` represents an authenticated user in the system. Users are auto-provisioned on first login.

**Properties:**
- `id` (int): Auto-increment primary key
- `email` (string): Unique email address (case-insensitive, normalized to lowercase)
- `name` (string): Display name from Entra ID
- `role` (string): User role ("user" or "admin")
- `created_at` (datetime): Account creation timestamp
- `updated_at` (datetime): Last update timestamp

**Relationships:**
- `contexts`: Contexts this user has access to (many-to-many via `user_contexts`)
- `credentials`: Encrypted credentials owned by this user

### Context

A `Context` represents an isolated tenant environment. Think of it as a workspace or project.

**Properties:**
- `id` (UUID): Unique identifier
- `name` (string): Human-readable name (unique)
- `type` (string): Context type (e.g., "virtual", "git_repo", "devops")
- `config` (JSONB): Arbitrary configuration data
- `default_cwd` (string): Default working directory

**Relationships:**
- `users`: Users who have access to this context (many-to-many via `user_contexts`)
- `conversations`: All conversations in this context
- `oauth_tokens`: OAuth tokens for MCP integrations
- `tool_permissions`: Tool access controls

### UserCredential

A `UserCredential` stores encrypted credentials for external integrations on a per-user basis.

**Properties:**
- `id` (int): Auto-increment primary key
- `user_id` (int): Foreign key to User
- `credential_type` (string): Type of credential (e.g., "azure_devops_pat", "github_token")
- `encrypted_value` (bytes): Fernet-encrypted credential value
- `created_at` (datetime): Creation timestamp
- `updated_at` (datetime): Last update timestamp

**Unique Constraint:** `(user_id, credential_type)` - one credential per type per user

**Supported Types:**
- `azure_devops_pat`: Azure DevOps Personal Access Token
- `github_token`: GitHub Personal Access Token
- `gitlab_token`: GitLab Personal Access Token
- `jira_api_token`: Jira API Token

### Conversation

A `Conversation` belongs to a single context and represents a chat session.

**Properties:**
- `id` (UUID): Unique identifier
- `platform` (string): Source platform (e.g., "openwebui", "telegram")
- `platform_id` (string): Platform-specific conversation ID
- `context_id` (UUID): Foreign key to Context
- `current_cwd` (string): Current working directory
- `metadata` (JSONB): Platform-specific metadata

### Service Factory Pattern

The `ServiceFactory` creates isolated `AgentService` instances per request:

```python
class ServiceFactory:
    def __init__(self, settings, litellm_client):
        # Load base tool registry once (shared template)
        self._base_tool_registry = load_tool_registry(settings.tools_config_path)
        self._settings = settings
        self._litellm = litellm_client

    async def create_service(self, context_id: UUID, session: AsyncSession):
        # 1. Clone base tool registry (avoid mutation)
        tool_registry = self._base_tool_registry.clone()

        # 2. Load and apply tool permissions for this context
        permissions = await load_permissions(context_id, session)
        tool_registry.filter_by_permissions(permissions)

        # 3. Load MCP tools for this context (OAuth-authenticated)
        await load_mcp_tools_for_context(context_id, tool_registry, session)

        # 4. Create context-scoped memory store
        memory = MemoryStore(settings, context_id=context_id)
        await memory.ainit()

        # 5. Return fully configured service
        return AgentService(settings, litellm_client, memory, tool_registry)
```

**Key Points:**
- Per-request service creation (no global singleton)
- Base registry cached and cloned (efficient)
- Each service has isolated tool registry and memory store

---

## Architecture Components

### 1. ServiceFactory (`core/core/service_factory.py`)

**Responsibilities:**
- Cache base tool registry (loaded once on init)
- Create context-scoped `AgentService` instances
- Clone tool registries per context
- Load and apply tool permissions
- Initialize MCP clients with OAuth tokens
- Create context-filtered memory stores

**Lifecycle:**
- Created during app startup (singleton)
- Accessed via FastAPI dependency injection
- Lives for application lifetime

### 2. McpClientPool (`core/mcp/client_pool.py`)

**Responsibilities:**
- Manage MCP clients per context
- Cache clients for reuse
- Handle OAuth token resolution
- Monitor client health
- Support concurrent access with locks

**Features:**
- **Caching**: Clients cached per context, validated on access
- **Health Monitoring**: Ping clients before returning from cache
- **OAuth Integration**: Automatically loads OAuth tokens from database
- **Concurrent Safety**: Locks prevent duplicate client creation

**Example:**
```python
pool = McpClientPool(settings)

# Get clients for a context (creates if needed, caches otherwise)
clients = await pool.get_clients(context_id, session)

# Disconnect all clients for a context
await pool.disconnect_context(context_id)

# Get health status
health = pool.get_health_status()
```

### 3. Tool Registry Isolation

**Clone Pattern:**
```python
# Base registry (shared, immutable after load)
base_registry = load_tool_registry("config/tools.yaml")

# Per-context registry (cloned, mutable)
context_registry = base_registry.clone()

# Apply permissions
context_registry.filter_by_permissions({
    "bash": False,  # Deny bash
    "python": True,  # Allow python (explicit)
    # All other tools allowed by default
})
```

**Permission Model:**
- No permissions defined = allow all tools
- Explicit `allowed=False` = deny tool
- Explicit `allowed=True` or not mentioned = allow tool

### 4. Memory Store Context Filtering

**Qdrant Integration:**
```python
# Create context-scoped memory store
memory = MemoryStore(settings, context_id=context_id)

# Store memory (automatically adds context_id to payload)
await memory.store(MemoryRecord(
    conversation_id="conv_123",
    text="User wants to deploy to production",
    metadata={},
))

# Search (automatically filters by context_id)
results = await memory.search("deploy production", limit=5)
# Returns only memories from this context
```

**Filtering Logic:**
- Store: Adds `context_id` to Qdrant payload
- Search: Applies `FieldCondition(key="context_id", match=...)` filter
- Isolation: Contexts never see each other's memories

---

## Context Isolation

### Database-Level Isolation

**Foreign Keys with Cascade Delete:**
```sql
-- contexts table
CREATE TABLE contexts (
    id UUID PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    ...
);

-- conversations cascade delete when context deleted
CREATE TABLE conversations (
    id UUID PRIMARY KEY,
    context_id UUID NOT NULL REFERENCES contexts(id) ON DELETE CASCADE,
    ...
);

-- oauth_tokens cascade delete
CREATE TABLE oauth_tokens (
    id UUID PRIMARY KEY,
    context_id UUID NOT NULL REFERENCES contexts(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    ...
    UNIQUE(context_id, provider)
);

-- tool_permissions cascade delete
CREATE TABLE tool_permissions (
    id UUID PRIMARY KEY,
    context_id UUID NOT NULL REFERENCES contexts(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    allowed BOOLEAN NOT NULL,
    ...
    UNIQUE(context_id, tool_name)
);
```

### Vector Database Isolation

**Qdrant Filtering:**
```python
# Every memory point has context_id in payload
{
    "id": "memory_123",
    "vector": [0.1, 0.2, ...],
    "payload": {
        "context_id": "abc-123-def",  # ← Isolation key
        "conversation_id": "conv_456",
        "text": "Deploy to staging",
        ...
    }
}

# Search with context filter
from qdrant_client.models import Filter, FieldCondition, MatchValue

query_filter = Filter(
    must=[
        FieldCondition(
            key="context_id",
            match=MatchValue(value=str(context_id))
        )
    ]
)
```

### MCP Client Isolation

**Per-Context OAuth Tokens:**
```python
# Each context has separate OAuth tokens
context_a:
  - provider: "homey", access_token: "token_a", expires_at: ...

context_b:
  - provider: "homey", access_token: "token_b", expires_at: ...

# MCP clients created with context-specific tokens
client_a = McpClient(
    url="https://mcp.athom.com/sse",
    context_id=context_a_id,
    oauth_provider="homey",  # Uses token_a
)

client_b = McpClient(
    url="https://mcp.athom.com/sse",
    context_id=context_b_id,
    oauth_provider="homey",  # Uses token_b
)
```

---

## Request Flow

### OpenWebUI Request Flow

```
1. User sends message in OpenWebUI
   ↓
2. OpenWebUI → POST /v1/chat/completions with headers:
   - X-OpenWebUI-User-Email: user@example.com
   - X-OpenWebUI-User-Name: John Doe
   - X-OpenWebUI-User-Role: user
   ↓
3. Extract user info from headers
   - Normalize email to lowercase
   - Get or create User record in database
   ↓
4. OpenWebUI Adapter extracts conversation_id
   ↓
5. Adapter queries database for conversation → context_id
   ↓
6. If new conversation:
   - Create Context (name="openwebui_{uuid}")
   - Create Conversation (context_id=new_context.id)
   - Link User to Context via user_contexts table
   ↓
7. ServiceFactory.create_service(context_id, session)
   ↓
8. ServiceFactory:
   - Clones base tool registry
   - Loads tool permissions for context_id
   - Filters tools by permissions
   - Loads MCP tools (with OAuth tokens for context_id)
   - Creates MemoryStore(context_id=context_id)
   ↓
9. AgentService.handle_request(request)
   - Searches memory (filtered by context_id)
   - Calls tools (from context-specific registry)
   - Uses MCP clients (authenticated with context OAuth)
   - Can access user's encrypted credentials if needed
   ↓
10. Response returned to user
```

### /v1/agent Endpoint Flow

```
1. Client → POST /v1/agent {"prompt": "...", "conversation_id": "..."}
   ↓
2. Extract/create context_id from conversation_id
   ↓
3. ServiceFactory.create_service(context_id, session)
   ↓
4. Process request with context-scoped service
   ↓
5. Return response
```

---

## Security Model

### Authentication Layers

**User Layer (OpenWebUI):**
- Entra ID authentication via Open WebUI
- User information forwarded via headers:
  - `X-OpenWebUI-User-Email`: User's email (normalized to lowercase)
  - `X-OpenWebUI-User-Name`: User's display name
  - `X-OpenWebUI-User-Id`: Open WebUI internal user ID
  - `X-OpenWebUI-User-Role`: User's role claim (informational only)
- Auto-provisioning on first login
- Maps user → conversation → context

**SECURITY: Header Role Trust Policy:**
- Header role (`X-OpenWebUI-User-Role`) is ONLY used for initial user creation
- After first login, database role is authoritative - headers are IGNORED for role
- This prevents privilege escalation via header spoofing if upstream proxy is bypassed
- Role changes MUST be done through the admin portal
- Admin endpoints (`/platformadmin/*`) use direct Entra ID OAuth, not header forwarding
- Traefik strips untrusted headers on admin routes for defense-in-depth

**Admin Layer:**
- Direct Entra ID OAuth authentication (not header forwarding)
- Database role must be "admin" (header claims ignored)
- Access to all contexts and credentials (for management)
- JWT session cookie after successful OAuth flow

### Authorization

**Tool Permissions:**
- Per-context allow/deny lists
- Default: allow all tools
- Explicit deny takes precedence

**OAuth Scopes:**
- Per-context OAuth tokens
- Provider-specific scopes
- Refresh tokens stored securely

### Data Access Rules

1. **Users**: Auto-created on first login, identified by email
2. **Conversations**: Can only be accessed by their context
3. **Memories**: Automatically filtered by context_id
4. **OAuth Tokens**: Unique per (context, provider)
5. **User Credentials**: Encrypted per user, isolated by user_id
6. **MCP Clients**: Isolated by context, use context OAuth tokens

### Credential Security

**Encryption:**
- Fernet symmetric encryption (AES-128 in CBC mode)
- Encryption key from `AGENT_CREDENTIAL_ENCRYPTION_KEY` environment variable
- Credentials encrypted at rest in database
- Decrypted only when needed for tool execution

**Access Control:**
- Users can only access their own credentials
- Admin role required for credential management endpoints
- Credentials scoped per user (not per context)
- Credentials never exposed in API responses

**Key Management:**
```bash
# Generate encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Add to .env
AGENT_CREDENTIAL_ENCRYPTION_KEY=your_generated_key_here

# Store in secrets manager (production)
export AGENT_CREDENTIAL_ENCRYPTION_KEY=$(vault read -field=key secret/credential_key)
```

---

## Admin API

See [ADMIN_API.md](./ADMIN_API.md) for complete reference.

### Quick Examples

**List all contexts:**
```bash
curl -H "X-API-Key: $ADMIN_KEY" http://localhost:8000/admin/contexts
```

**Get context details:**
```bash
curl -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/contexts/{context_id}
```

**Create context:**
```bash
curl -X POST -H "X-API-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "production", "type": "devops", "default_cwd": "/app"}' \
  http://localhost:8000/admin/contexts
```

**Revoke OAuth token:**
```bash
curl -X DELETE -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/oauth/tokens/{token_id}
```

**Disconnect MCP clients:**
```bash
curl -X POST -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/mcp/disconnect/{context_id}
```

---

## Migration Guide

### From Single-Tenant to Multi-Tenant

**1. Database Migration**

```bash
cd services/agent

# Run Alembic migrations
poetry run alembic upgrade head
```

This creates:
- `tool_permissions` table
- Foreign keys and cascade rules

**2. Memory Migration (Qdrant)**

```bash
# Migrate existing memories to include context_id
poetry run python scripts/migrate_memory_context.py

# Options:
# - Maps conversation_id → context_id
# - Creates default contexts for orphaned data
```

**3. Environment Variables**

Add to `.env`:
```bash
# Generate credential encryption key
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
AGENT_CREDENTIAL_ENCRYPTION_KEY=your_generated_key_here

# Open WebUI must forward user headers
ENABLE_FORWARD_USER_INFO_HEADERS=true  # In Open WebUI .env
```

**4. Code Changes**

**Before (global service):**
```python
# app.py
service = AgentService(...)
app.state.service = service

# endpoint
service = Depends(get_service)
```

**After (service factory):**
```python
# app.py
factory = ServiceFactory(settings, litellm_client)
app.state.service_factory = factory

# endpoint
factory = Depends(get_service_factory)
context_id = extract_context_id(...)
service = await factory.create_service(context_id, session)
```

### Backward Compatibility

**Legacy Endpoints:**
- `/v1/chat/completions` - Auto-creates contexts
- `/v1/agent` - Works with conversation_id

**Default Behavior:**
- No context specified = creates virtual context
- No tool permissions = allows all tools
- No OAuth tokens = static tokens from env vars (fallback)

---

## Performance Considerations

### Caching Strategy

**What's Cached:**
- ✅ Base tool registry (singleton, loaded once)
- ✅ MCP clients (per context, validated on access)
- ❌ AgentService instances (created per request)
- ❌ Tool registry clones (created per request)
- ❌ Memory stores (created per request)

**Why Not Cache Services?**
- Tool permissions may change
- OAuth tokens may be revoked/refreshed
- Memory context must match request context
- Stateless services are easier to reason about

### Optimization Tips

**1. Tool Registry Cloning:**
```python
# Efficient: Shallow copy of dict (tools are references)
cloned = registry.clone()  # O(n) where n = number of tools
```

**2. MCP Client Reuse:**
```python
# Clients cached per context
# Health check: quick ping (2s timeout)
# Reconnect only if unhealthy
```

**3. Qdrant Filtering:**
```python
# Indexed field for fast filtering
# context_id added to payload, indexed on collection creation
```

---

## Troubleshooting

### Context Isolation Not Working

**Symptoms:**
- Users seeing each other's data
- Memories from wrong context

**Checks:**
1. Verify context_id passed to ServiceFactory
2. Check MemoryStore has correct context_id
3. Verify Qdrant filter applied in search

**Debug:**
```python
# Add logging
LOGGER.info(f"Creating service for context {context_id}")
LOGGER.info(f"Memory store context: {memory._context_id}")
```

### MCP Clients Not Authenticating

**Symptoms:**
- 401 errors from MCP calls
- OAuth prompts shown repeatedly

**Checks:**
1. Verify OAuth token exists in database
2. Check token not expired
3. Verify McpClientPool initialized
4. Check MCP URL configured

**Debug:**
```bash
# Check OAuth tokens
curl -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/oauth/status/{context_id}

# Check MCP health
curl -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8000/admin/mcp/health
```

### Tool Permissions Not Applied

**Symptoms:**
- Denied tools still accessible
- All tools available regardless of permissions

**Checks:**
1. Verify permissions exist in database
2. Check ServiceFactory loads permissions
3. Verify `filter_by_permissions()` called

**Debug:**
```python
# Check permissions in database
SELECT * FROM tool_permissions WHERE context_id = '...';

# Log in ServiceFactory
LOGGER.debug(f"Loaded permissions: {permissions}")
LOGGER.debug(f"Tools before filter: {registry.list_tools()}")
LOGGER.debug(f"Tools after filter: {filtered_registry.list_tools()}")
```

---

## References

- [Admin API Documentation](./ADMIN_API.md)
- [OAuth Setup Guide](./OAUTH_SETUP.md)
- [Architecture Overview](./ARCHITECTURE.md)
