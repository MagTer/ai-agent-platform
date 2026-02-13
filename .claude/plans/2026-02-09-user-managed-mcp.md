# User-Managed MCP Connections

**Created:** 2026-02-09
**Author:** Architect (Opus)
**Status:** Ready for implementation

---

## 1. Feature Overview

### What
Allow users to self-service add, edit, and delete MCP (Model Context Protocol) server connections via the admin portal. Currently, MCP providers are hardcoded in `client_pool.py` (Context7 and Zapier), requiring code changes and redeployment for any new provider. Zapier will be removed as a hardcoded provider and become user-managed through this feature.

### Why
- Users need to connect to arbitrary MCP servers (company-internal, third-party SaaS, etc.)
- Current hardcoded approach does not scale beyond the two existing providers
- The platform already has full MCP client infrastructure -- it just lacks the DB-backed configuration layer

### Key Requirements
1. DB model for user-defined MCP server connections per context
2. Admin UI for CRUD on MCP servers
3. Auth support: no-auth, static bearer token, OAuth 2.0 / 2.1 (with PKCE)
4. `client_pool.py` reads user MCPs from DB alongside Context7 (only remaining hardcoded provider)
5. Connection status and error surfacing in admin UI
6. All credentials encrypted at rest (Fernet)

---

## 2. Architecture Decisions

### Layer Placement
- **DB model:** `core/db/models.py` (Layer 4 -- core)
- **Alembic migration:** `alembic/versions/` (database)
- **Client pool changes:** `core/mcp/client_pool.py` (Layer 4 -- core)
- **Admin UI + API:** `interfaces/http/admin_mcp.py` (Layer 1 -- interfaces)
- **OAuth flow reuse:** existing `core/auth/oauth_client.py` + `core/auth/token_manager.py`

### No New Protocols Needed
The existing `McpClient`, `McpClientPool`, `McpToolWrapper`, and `CredentialService` already provide all needed functionality. We only need:
- A new DB model to store connection configs
- Pool logic to read from DB instead of hardcoded env vars
- Admin CRUD endpoints

### Backward Compatibility
The existing hardcoded Context7 provider in `client_pool.py` will be **preserved** and continue working alongside user-defined connections. The Zapier hardcoded provider will be **removed** -- users who need Zapier MCP will add it as a user-managed server via the admin UI (this is the intended migration path for all non-Context7 providers).

### OAuth 2.1 Support
The OAuth implementation will follow OAuth 2.1 best practices:
- **PKCE required** for all authorization flows (no plain code exchange)
- **No implicit grant** -- only authorization code + PKCE
- **Strict redirect URI matching** -- exact string comparison, no wildcards
- **Refresh token rotation** -- each refresh issues a new refresh token (Phase 8)

---

## 3. Database Model

### New Table: `mcp_servers`

Add to `services/agent/src/core/db/models.py` after the `Workspace` class (around line 295):

```python
class McpServer(Base):
    """User-defined MCP server connection configuration.

    Stores connection details for Model Context Protocol servers that users
    configure via the admin portal. Each server is scoped to a context
    for multi-tenant isolation.

    Auth types:
    - none: No authentication
    - bearer: Static API key / bearer token (encrypted)
    - oauth: Full OAuth 2.0 flow (uses OAuthToken table)

    Attributes:
        id: Primary key
        context_id: Foreign key to Context (multi-tenant isolation)
        name: Human-friendly server name (e.g., "My Zapier MCP")
        url: MCP server endpoint URL
        transport: Transport protocol (auto, sse, streamable_http)
        auth_type: Authentication method (none, bearer, oauth)
        auth_token_encrypted: Encrypted static bearer token (for auth_type=bearer)
        oauth_provider_name: OAuth provider key in OAuthToken table (for auth_type=oauth)
        oauth_authorize_url: OAuth authorization endpoint (for auth_type=oauth)
        oauth_token_url: OAuth token endpoint (for auth_type=oauth)
        oauth_client_id: OAuth client ID (for auth_type=oauth)
        oauth_client_secret_encrypted: Encrypted OAuth client secret (for auth_type=oauth)
        oauth_scopes: Space-separated OAuth scopes (for auth_type=oauth)
        is_enabled: Whether this server is active
        status: Connection status (pending, connected, error, disabled)
        last_error: Last connection error message
        last_connected_at: Last successful connection timestamp
        tools_count: Number of tools discovered on last connection
        created_at: Record creation timestamp
        updated_at: Last update timestamp
    """

    __tablename__ = "mcp_servers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    context_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contexts.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String, index=True)
    url: Mapped[str] = mapped_column(String)
    transport: Mapped[str] = mapped_column(String, default="auto")  # auto, sse, streamable_http
    auth_type: Mapped[str] = mapped_column(String, default="none")  # none, bearer, oauth
    auth_token_encrypted: Mapped[str | None] = mapped_column(String, nullable=True)
    oauth_provider_name: Mapped[str | None] = mapped_column(String, nullable=True)
    oauth_authorize_url: Mapped[str | None] = mapped_column(String, nullable=True)
    oauth_token_url: Mapped[str | None] = mapped_column(String, nullable=True)
    oauth_client_id: Mapped[str | None] = mapped_column(String, nullable=True)
    oauth_client_secret_encrypted: Mapped[str | None] = mapped_column(String, nullable=True)
    oauth_scopes: Mapped[str | None] = mapped_column(String, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending, connected, error, disabled
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    tools_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    # Relationships
    context = relationship("Context")

    __table_args__ = (
        UniqueConstraint("context_id", "name", name="uq_context_mcp_name"),
    )
```

**Encryption helpers** for the model -- add two methods:

```python
    def set_auth_token(self, plaintext: str | None) -> None:
        """Encrypt and store a static bearer token."""
        if plaintext is None:
            self.auth_token_encrypted = None
        else:
            from core.db.oauth_models import encrypt_token
            self.auth_token_encrypted = encrypt_token(plaintext)

    def get_auth_token(self) -> str | None:
        """Decrypt the stored bearer token."""
        if self.auth_token_encrypted is None:
            return None
        from core.db.oauth_models import decrypt_token
        return decrypt_token(self.auth_token_encrypted)

    def set_oauth_client_secret(self, plaintext: str | None) -> None:
        """Encrypt and store OAuth client secret."""
        if plaintext is None:
            self.oauth_client_secret_encrypted = None
        else:
            from core.db.oauth_models import encrypt_token
            self.oauth_client_secret_encrypted = encrypt_token(plaintext)

    def get_oauth_client_secret(self) -> str | None:
        """Decrypt the stored OAuth client secret."""
        if self.oauth_client_secret_encrypted is None:
            return None
        from core.db.oauth_models import decrypt_token
        return decrypt_token(self.oauth_client_secret_encrypted)
```

### Required Imports

The file already imports `Boolean`, `DateTime`, `ForeignKey`, `String`, `UUID`, `JSONB`, `mapped_column`, `relationship`, `uuid`, `datetime`, `_utc_now`, and `UniqueConstraint`. No new imports needed.

---

## 4. Alembic Migration

### File: `services/agent/alembic/versions/20260209_add_mcp_servers.py`

```python
"""Add mcp_servers table for user-managed MCP connections.

Revision ID: 20260209_mcp_servers
Revises: 20260207_workspaces
Create Date: 2026-02-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20260209_mcp_servers"
down_revision: str | Sequence[str] | None = "20260207_workspaces"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create mcp_servers table."""
    op.create_table(
        "mcp_servers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "context_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contexts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("transport", sa.String(), nullable=False, server_default="auto"),
        sa.Column("auth_type", sa.String(), nullable=False, server_default="none"),
        sa.Column("auth_token_encrypted", sa.String(), nullable=True),
        sa.Column("oauth_provider_name", sa.String(), nullable=True),
        sa.Column("oauth_authorize_url", sa.String(), nullable=True),
        sa.Column("oauth_token_url", sa.String(), nullable=True),
        sa.Column("oauth_client_id", sa.String(), nullable=True),
        sa.Column("oauth_client_secret_encrypted", sa.String(), nullable=True),
        sa.Column("oauth_scopes", sa.String(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("last_connected_at", sa.DateTime(), nullable=True),
        sa.Column("tools_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # Indexes
    op.create_index("ix_mcp_servers_context_id", "mcp_servers", ["context_id"])
    op.create_index("ix_mcp_servers_name", "mcp_servers", ["name"])

    # Unique constraint: one name per context
    op.create_unique_constraint(
        "uq_context_mcp_name",
        "mcp_servers",
        ["context_id", "name"],
    )


def downgrade() -> None:
    """Drop mcp_servers table."""
    op.drop_table("mcp_servers")
```

---

## 5. Client Pool Changes

### File: `services/agent/src/core/mcp/client_pool.py`

**Goal:** After processing the remaining hardcoded provider (Context7), also load user-defined MCP servers from the `mcp_servers` table. The Zapier hardcoded provider is removed in this phase.

### Changes to `get_clients()` method

Add a new method `_load_user_mcp_servers()` and call it at the end of `get_clients()`, right before `# Store in cache` (line 271). The method:

1. Queries `McpServer` table for the given `context_id` where `is_enabled=True`
2. For each server, creates an `McpClient` with the appropriate auth
3. Connects and appends to the `clients` list
4. Updates the `McpServer` row with status/error/tools_count

**Add this new method to the `McpClientPool` class:**

```python
async def _load_user_mcp_servers(
    self,
    context_id: UUID,
    session: AsyncSession,
    clients: list[McpClient],
) -> bool:
    """Load user-defined MCP servers from database.

    Queries the mcp_servers table for enabled servers in this context,
    creates McpClient instances, and appends connected clients to the list.

    Args:
        context_id: Context UUID
        session: Database session
        clients: List to append connected clients to (mutated in place)

    Returns:
        True if any connection was attempted, False otherwise
    """
    from core.db.models import McpServer
    from core.mcp.client import McpTransport

    stmt = (
        select(McpServer)
        .where(
            McpServer.context_id == context_id,
            McpServer.is_enabled.is_(True),
        )
    )
    result = await session.execute(stmt)
    user_servers = result.scalars().all()

    if not user_servers:
        return False

    connection_attempted = False
    now_naive = datetime.now(UTC).replace(tzinfo=None)

    for server in user_servers:
        connection_attempted = True

        # Determine auth token
        auth_token: str | None = None
        oauth_provider: str | None = None

        if server.auth_type == "bearer":
            auth_token = server.get_auth_token()
        elif server.auth_type == "oauth" and server.oauth_provider_name:
            oauth_provider = server.oauth_provider_name

        # Map transport string to enum
        transport_map = {
            "auto": McpTransport.AUTO,
            "sse": McpTransport.SSE,
            "streamable_http": McpTransport.STREAMABLE_HTTP,
        }
        transport = transport_map.get(server.transport, McpTransport.AUTO)

        try:
            client = McpClient(
                url=server.url,
                auth_token=auth_token,
                context_id=context_id,
                oauth_provider=oauth_provider,
                name=server.name,
                auto_reconnect=True,
                max_retries=1,
                cache_ttl_seconds=300,
                transport=transport,
            )
            await asyncio.wait_for(client.connect(), timeout=10.0)
            clients.append(client)

            # Update server status in DB
            server.status = "connected"
            server.last_error = None
            server.last_connected_at = now_naive
            server.tools_count = len(client.tools)

            LOGGER.info(
                "Connected user MCP '%s' for context %s (%d tools)",
                server.name,
                context_id,
                len(client.tools),
            )
            session.add(
                DebugLog(
                    trace_id=str(context_id),
                    event_type="mcp_connect",
                    event_data={
                        "provider": server.name,
                        "tools_count": len(client.tools),
                        "transport": server.transport,
                        "source": "user_defined",
                    },
                )
            )

        except Exception as e:
            error_msg = str(e)[:500]
            server.status = "error"
            server.last_error = error_msg

            LOGGER.error(
                "Failed to connect user MCP '%s' for context %s: %s",
                server.name,
                context_id,
                error_msg,
            )
            session.add(
                DebugLog(
                    trace_id=str(context_id),
                    event_type="mcp_error",
                    event_data={
                        "provider": server.name,
                        "error": error_msg,
                        "source": "user_defined",
                    },
                )
            )

    return connection_attempted
```

**Add import at the top of the file:**

```python
from datetime import UTC, datetime
```

**Integration point:** In `get_clients()`, right before `# Store in cache` (line 271), add:

```python
            # Load user-defined MCP servers from database
            user_attempted = await self._load_user_mcp_servers(
                context_id, session, clients
            )
            if user_attempted:
                connection_attempted = True
```

---

## 6. Admin MCP Dashboard Rewrite

### File: `services/agent/src/interfaces/http/admin_mcp.py`

The existing `admin_mcp.py` is 369 lines. We will **extend** it by adding CRUD endpoints and enhancing the dashboard HTML with a management UI.

Since the HTML/JS will exceed 500 lines when combined with the existing code, extract the HTML template into `services/agent/src/interfaces/http/templates/admin_mcp.html`.

### New Pydantic Models (add to admin_mcp.py)

```python
class McpServerCreate(BaseModel):
    """Request to create an MCP server connection."""
    context_id: str
    name: str
    url: str
    transport: str = "auto"  # auto, sse, streamable_http
    auth_type: str = "none"  # none, bearer, oauth
    auth_token: str | None = None  # For bearer auth
    oauth_authorize_url: str | None = None
    oauth_token_url: str | None = None
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    oauth_scopes: str | None = None


class McpServerUpdate(BaseModel):
    """Request to update an MCP server connection."""
    name: str | None = None
    url: str | None = None
    transport: str | None = None
    auth_type: str | None = None
    auth_token: str | None = None
    oauth_authorize_url: str | None = None
    oauth_token_url: str | None = None
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    oauth_scopes: str | None = None
    is_enabled: bool | None = None


class McpServerInfo(BaseModel):
    """MCP server information for API responses."""
    id: str
    context_id: str
    context_name: str | None
    name: str
    url: str
    transport: str
    auth_type: str
    is_enabled: bool
    status: str
    last_error: str | None
    last_connected_at: str | None
    tools_count: int
    has_oauth_config: bool
    created_at: str
    updated_at: str


class McpServerListResponse(BaseModel):
    """Response listing MCP servers."""
    servers: list[McpServerInfo]
    total: int
```

### New API Endpoints

**List servers for all contexts (admin view):**
```
GET /platformadmin/mcp/servers
Response: McpServerListResponse
Auth: Depends(verify_admin_user)
```

**Create server:**
```
POST /platformadmin/mcp/servers
Body: McpServerCreate
Response: {"success": true, "server_id": "...", "message": "..."}
Auth: Depends(verify_admin_user), Depends(require_csrf)
```

**Update server:**
```
PUT /platformadmin/mcp/servers/{server_id}
Body: McpServerUpdate
Response: {"success": true, "message": "..."}
Auth: Depends(verify_admin_user), Depends(require_csrf)
```

**Delete server:**
```
DELETE /platformadmin/mcp/servers/{server_id}
Response: {"success": true, "message": "..."}
Auth: Depends(verify_admin_user), Depends(require_csrf)
```

**Test connection (ping):**
```
POST /platformadmin/mcp/servers/{server_id}/test
Response: {"success": true/false, "tools_count": N, "error": "..."}
Auth: Depends(verify_admin_user), Depends(require_csrf)
```

**Start OAuth flow for an MCP server:**
```
POST /platformadmin/mcp/servers/{server_id}/oauth/start
Response: {"authorization_url": "...", "message": "..."}
Auth: Depends(verify_admin_user), Depends(require_csrf)
```

### Endpoint Implementation Details

#### Create Server Endpoint

```python
@router.post(
    "/servers",
    dependencies=[Depends(require_csrf)],
)
async def create_mcp_server(
    request: McpServerCreate,
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Create a new user-defined MCP server connection.

    Validates inputs, encrypts secrets, and stores in database.
    Does NOT connect immediately -- connection happens on next agent request.
    """
    # Validate context exists
    ctx_uuid = UUID(request.context_id)
    ctx_stmt = select(Context).where(Context.id == ctx_uuid)
    ctx_result = await session.execute(ctx_stmt)
    context = ctx_result.scalar_one_or_none()
    if not context:
        raise HTTPException(status_code=404, detail="Context not found")

    # Validate transport
    valid_transports = {"auto", "sse", "streamable_http"}
    if request.transport not in valid_transports:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid transport. Must be one of: {', '.join(valid_transports)}",
        )

    # Validate auth_type
    valid_auth_types = {"none", "bearer", "oauth"}
    if request.auth_type not in valid_auth_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid auth_type. Must be one of: {', '.join(valid_auth_types)}",
        )

    # Validate URL scheme (SSRF protection)
    from urllib.parse import urlparse
    parsed = urlparse(request.url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="URL must use http or https scheme")

    # Validate bearer token provided if auth_type is bearer
    if request.auth_type == "bearer" and not request.auth_token:
        raise HTTPException(status_code=400, detail="auth_token required for bearer auth")

    # Validate OAuth fields if auth_type is oauth
    if request.auth_type == "oauth":
        if not all([request.oauth_authorize_url, request.oauth_token_url, request.oauth_client_id]):
            raise HTTPException(
                status_code=400,
                detail="oauth_authorize_url, oauth_token_url, and oauth_client_id required for OAuth auth",
            )

    # Check duplicate name within context
    dup_stmt = select(McpServer).where(
        McpServer.context_id == ctx_uuid,
        McpServer.name == request.name,
    )
    dup_result = await session.execute(dup_stmt)
    if dup_result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"MCP server '{request.name}' already exists in this context")

    # Create server record
    server = McpServer(
        context_id=ctx_uuid,
        name=request.name,
        url=request.url,
        transport=request.transport,
        auth_type=request.auth_type,
        status="pending",
    )

    # Encrypt and store secrets
    if request.auth_type == "bearer" and request.auth_token:
        server.set_auth_token(request.auth_token)

    if request.auth_type == "oauth":
        # Generate a unique oauth_provider_name for this server
        server.oauth_provider_name = f"mcp_{server.id}"
        server.oauth_authorize_url = request.oauth_authorize_url
        server.oauth_token_url = request.oauth_token_url
        server.oauth_client_id = request.oauth_client_id
        if request.oauth_client_secret:
            server.set_oauth_client_secret(request.oauth_client_secret)
        server.oauth_scopes = request.oauth_scopes

    session.add(server)
    await session.commit()

    # Invalidate MCP pool cache for this context so next request picks up new server
    try:
        pool = get_mcp_client_pool()
        await pool.disconnect_context(ctx_uuid)
    except RuntimeError:
        pass  # Pool not initialized

    LOGGER.info(
        "Admin %s created MCP server '%s' for context %s",
        sanitize_log(admin.email),
        sanitize_log(request.name),
        sanitize_log(ctx_uuid),
    )

    return {
        "success": True,
        "server_id": str(server.id),
        "message": f"MCP server '{request.name}' created. It will connect on next agent request.",
    }
```

#### Test Connection Endpoint

```python
@router.post(
    "/servers/{server_id}/test",
    dependencies=[Depends(require_csrf)],
)
async def test_mcp_server(
    server_id: UUID,
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Test connectivity to a user-defined MCP server.

    Creates a temporary McpClient, attempts to connect, and returns results.
    Updates the server status in the database.
    """
    from core.mcp.client import McpClient, McpTransport

    stmt = select(McpServer).where(McpServer.id == server_id)
    result = await session.execute(stmt)
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    # Determine auth
    auth_token: str | None = None
    if server.auth_type == "bearer":
        auth_token = server.get_auth_token()
    # OAuth tokens would come from the OAuthToken table via the client

    transport_map = {
        "auto": McpTransport.AUTO,
        "sse": McpTransport.SSE,
        "streamable_http": McpTransport.STREAMABLE_HTTP,
    }

    try:
        client = McpClient(
            url=server.url,
            auth_token=auth_token,
            context_id=server.context_id,
            oauth_provider=server.oauth_provider_name if server.auth_type == "oauth" else None,
            name=server.name,
            auto_reconnect=False,
            max_retries=1,
            transport=transport_map.get(server.transport, McpTransport.AUTO),
        )
        await asyncio.wait_for(client.connect(), timeout=15.0)

        tools_count = len(client.tools)

        # Update server status
        server.status = "connected"
        server.last_error = None
        server.last_connected_at = datetime.now(UTC).replace(tzinfo=None)
        server.tools_count = tools_count
        await session.commit()

        await client.disconnect()

        return {
            "success": True,
            "tools_count": tools_count,
            "tools": [{"name": t.name, "description": t.description} for t in client.tools[:20]],
            "message": f"Connected successfully. Discovered {tools_count} tools.",
        }

    except Exception as e:
        error_msg = str(e)[:500]
        server.status = "error"
        server.last_error = error_msg
        await session.commit()

        return {
            "success": False,
            "tools_count": 0,
            "error": error_msg,
            "message": f"Connection failed: {error_msg}",
        }
```

---

## 7. OAuth Flow for User-Defined MCP Servers

When `auth_type="oauth"`, the user-defined MCP server has its own OAuth configuration stored in the `mcp_servers` row. We need to dynamically register this as an OAuth provider so the existing `OAuthClient` and `TokenManager` can handle the flow.

### Approach: Dynamic OAuthProviderConfig

Instead of modifying the singleton `TokenManager`, create a **dedicated endpoint** that generates an authorization URL using the server's stored OAuth config and the existing `OAuthClient` mechanics.

**In the `/servers/{server_id}/oauth/start` endpoint:**

1. Read the `McpServer` row to get OAuth config
2. Create a temporary `OAuthProviderConfig` from the stored fields
3. Create a temporary `OAuthClient` with just this one provider
4. Generate authorization URL with **mandatory PKCE** (S256 code challenge + state stored in `oauth_states` table) -- OAuth 2.1 compliance
5. Set `oauth_provider_name` on the `McpServer` so `McpClient._get_auth_token()` can fetch it later
6. Return the URL to the admin

**OAuth callback:** The existing `/auth/oauth/callback` endpoint handles the exchange automatically because the `OAuthState` row contains the provider name and context_id. The token gets stored in `oauth_tokens` with the dynamic provider name (e.g., `mcp_<server_id>`).

**Token retrieval at connection time:** In `_load_user_mcp_servers()`, when `auth_type="oauth"`, we pass `oauth_provider=server.oauth_provider_name` to `McpClient`. The client's `_get_auth_token()` method already fetches tokens from `oauth_tokens` via `get_token_manager().get_token()`.

**Important:** The `TokenManager._oauth_client` has a fixed set of `_provider_configs`. For user-defined OAuth providers, we need the `TokenManager.get_token()` method to work with dynamic providers. Looking at the code, `get_token()` delegates to `OAuthClient.get_token()` which only does a DB query -- it does NOT need the provider config. The provider config is only needed for `get_authorization_url()` and `_refresh_token()`.

**For refresh:** Token refresh for dynamic providers is handled in Phase 8 (OAuth Auto-Refresh). Until Phase 8 is implemented, if a token expires, the server status will show "error" and the user re-authorizes manually via the admin UI.

### OAuth Start Endpoint

```python
@router.post(
    "/servers/{server_id}/oauth/start",
    dependencies=[Depends(require_csrf)],
)
async def start_mcp_oauth(
    server_id: UUID,
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Start OAuth authorization flow for an MCP server.

    Generates an authorization URL using the server's stored OAuth config.
    The user visits this URL to authorize, and the standard /auth/oauth/callback
    handles the token exchange.
    """
    from core.auth.models import OAuthProviderConfig
    from core.auth.oauth_client import OAuthClient
    from core.runtime.config import get_settings
    from core.db.engine import AsyncSessionLocal

    stmt = select(McpServer).where(McpServer.id == server_id)
    result = await session.execute(stmt)
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    if server.auth_type != "oauth":
        raise HTTPException(status_code=400, detail="Server does not use OAuth authentication")

    if not all([server.oauth_authorize_url, server.oauth_token_url, server.oauth_client_id]):
        raise HTTPException(status_code=400, detail="OAuth configuration incomplete")

    settings = get_settings()
    if not settings.oauth_redirect_uri:
        raise HTTPException(
            status_code=500,
            detail="AGENT_OAUTH_REDIRECT_URI not configured on the server",
        )

    # Ensure oauth_provider_name is set
    if not server.oauth_provider_name:
        server.oauth_provider_name = f"mcp_{server.id}"
        await session.flush()

    # Create temporary OAuth provider config
    provider_config = OAuthProviderConfig(
        provider_name=server.oauth_provider_name,
        authorization_url=server.oauth_authorize_url,
        token_url=server.oauth_token_url,
        client_id=server.oauth_client_id,
        client_secret=server.get_oauth_client_secret(),
        scopes=server.oauth_scopes,
        redirect_uri=settings.oauth_redirect_uri,
    )

    # Create temporary OAuth client for this provider
    oauth_client = OAuthClient(
        session_factory=AsyncSessionLocal,
        provider_configs={server.oauth_provider_name: provider_config},
    )

    # Generate authorization URL
    auth_url, state = await oauth_client.get_authorization_url(
        provider=server.oauth_provider_name,
        context_id=server.context_id,
        user_id=admin.user_id,
    )

    await session.commit()

    return {
        "authorization_url": auth_url,
        "state": state,
        "message": f"Visit the URL to authorize '{server.name}'. After authorization, the token will be stored automatically.",
    }
```

**IMPORTANT:** For the OAuth callback to successfully exchange the code, the `TokenManager` needs to know the provider config for the dynamic provider. Since `exchange_code_for_token()` requires the provider config for the token URL and client credentials, we need to handle this.

**Solution:** Modify `exchange_code_for_token()` in `OAuthClient` to fall back to reading config from `McpServer` when the provider is not in the static configs. Add a helper:

```python
# In OAuthClient._get_provider_config(), add fallback:
async def _get_provider_config_dynamic(self, provider: str, session: AsyncSession) -> OAuthProviderConfig:
    """Get provider config, falling back to McpServer table for dynamic providers."""
    # Try static config first
    config = self._provider_configs.get(provider)
    if config:
        return config

    # Fall back to McpServer table for dynamic providers (mcp_*)
    if provider.startswith("mcp_"):
        from core.db.models import McpServer
        from core.runtime.config import get_settings

        stmt = select(McpServer).where(McpServer.oauth_provider_name == provider)
        result = await session.execute(stmt)
        server = result.scalar_one_or_none()
        if server and server.auth_type == "oauth":
            settings = get_settings()
            return OAuthProviderConfig(
                provider_name=provider,
                authorization_url=server.oauth_authorize_url,
                token_url=server.oauth_token_url,
                client_id=server.oauth_client_id,
                client_secret=server.get_oauth_client_secret(),
                scopes=server.oauth_scopes,
                redirect_uri=settings.oauth_redirect_uri or "",
            )

    raise ValueError(f"OAuth provider '{provider}' not configured")
```

**HOWEVER**, this would require modifying the core `OAuthClient` which uses a sync `_get_provider_config()`. To minimize risk, a cleaner approach:

**Alternative (chosen):** In the `exchange_code_for_token()` flow, the token exchange already happens in the `/oauth/start` endpoint's `OAuthClient` instance. But the callback goes through the global `TokenManager`. We need the global `TokenManager` to be able to resolve dynamic providers.

**Simplest fix:** Add a `register_dynamic_provider()` method to `TokenManager` that temporarily adds a provider config. When the OAuth start endpoint creates the flow, it also registers the provider config with the global `TokenManager`. When the callback arrives, the `TokenManager` can find the config.

Add to `services/agent/src/core/auth/token_manager.py`:

```python
def register_dynamic_provider(self, provider_name: str, config: OAuthProviderConfig) -> None:
    """Register a dynamic OAuth provider config (e.g., from user-defined MCP servers).

    This enables the standard OAuth callback to handle token exchange
    for providers not statically configured at startup.

    Args:
        provider_name: Unique provider name (e.g., 'mcp_<uuid>')
        config: OAuth provider configuration
    """
    self._oauth_client._provider_configs[provider_name] = config
    LOGGER.info("Registered dynamic OAuth provider: %s", provider_name)

def unregister_dynamic_provider(self, provider_name: str) -> None:
    """Remove a dynamic OAuth provider config.

    Args:
        provider_name: Provider name to remove
    """
    self._oauth_client._provider_configs.pop(provider_name, None)
    LOGGER.debug("Unregistered dynamic OAuth provider: %s", provider_name)
```

**Updated OAuth start endpoint** adds one line after creating `provider_config`:

```python
    # Register with global TokenManager so the callback can find the config
    from core.providers import get_token_manager
    token_manager = get_token_manager()
    token_manager.register_dynamic_provider(server.oauth_provider_name, provider_config)
```

**On server startup / pool reconnection**, any `McpServer` with `auth_type=oauth` should register its provider config with the `TokenManager`. This ensures that if the server restarts, existing OAuth tokens can still be refreshed. Do this in `_load_user_mcp_servers()`:

```python
# Before creating the McpClient, register dynamic provider for token refresh
if server.auth_type == "oauth" and server.oauth_provider_name:
    try:
        from core.providers import get_token_manager
        from core.auth.models import OAuthProviderConfig
        from core.runtime.config import get_settings

        settings = self._settings
        if settings.oauth_redirect_uri:
            config = OAuthProviderConfig(
                provider_name=server.oauth_provider_name,
                authorization_url=server.oauth_authorize_url or "",
                token_url=server.oauth_token_url or "",
                client_id=server.oauth_client_id or "",
                client_secret=server.get_oauth_client_secret(),
                scopes=server.oauth_scopes,
                redirect_uri=settings.oauth_redirect_uri,
            )
            get_token_manager().register_dynamic_provider(
                server.oauth_provider_name, config
            )
    except Exception:
        LOGGER.warning("Could not register dynamic OAuth provider for %s", server.name)
```

---

## 8. Admin UI

### Dashboard Enhancement

The main MCP dashboard at `/platformadmin/mcp/` will be enhanced with:

1. **Stats grid** -- Total servers, Connected, Error, Pending
2. **Server list table** -- Name, URL, Context, Auth Type, Status, Tools, Actions
3. **Add Server modal** -- Form with fields for name, URL, context, transport, auth type
4. **Edit Server modal** -- Pre-filled form for editing existing servers
5. **Test Connection button** -- Per-server test with inline result display
6. **OAuth Authorize button** -- For servers with `auth_type=oauth`, opens OAuth flow

Since the HTML/JS will be substantial, create a template file.

### Template File: `services/agent/src/interfaces/http/templates/admin_mcp.html`

The template should follow the patterns from `admin_credentials.py`:
- Stats grid at top
- Card with table of servers
- Modal forms for add/edit
- Toast notifications
- JavaScript fetch() calls to API endpoints
- CSRF token automatically included via the shared `render_admin_page()` function

**Key UI elements:**

```
+---------------------------------------------------------------+
| MCP Server Management                                         |
+---------------------------------------------------------------+
| [4] Total Servers  [2] Connected  [1] Error  [1] Pending     |
+---------------------------------------------------------------+
| All MCP Servers                    [+ Add Server] [Refresh]   |
|---------------------------------------------------------------|
| Name    | URL          | Context | Auth   | Status | Actions  |
|---------|------------- |---------|--------|--------|----------|
| My API  | https://... | Dev     | Bearer | OK     | Test Edit Del |
| Zapier  | https://... | Prod    | None   | Error  | Test Edit Del |
| Corp    | https://... | Dev     | OAuth  | Pending| Auth Test Edit Del |
+---------------------------------------------------------------+
```

**Add Server Form fields:**
- Context (dropdown, fetched from /platformadmin/contexts/list)
- Name (text input)
- URL (text input, validated)
- Transport (select: Auto, SSE, Streamable HTTP)
- Auth Type (select: None, Bearer Token, OAuth 2.0)
  - If Bearer: Token (password input)
  - If OAuth: Authorize URL, Token URL, Client ID, Client Secret, Scopes

---

## 9. Implementation Roadmap

### Phase 1: DB Model + Migration (Step 1)

**Engineer tasks:**
1. Add `McpServer` class to `services/agent/src/core/db/models.py` (after `Workspace` class, around line 295)
   - Include all columns as specified in Section 3
   - Include the 4 encryption helper methods
   - Add `Integer` to the imports from `sqlalchemy` (line 5)
2. Create migration file `services/agent/alembic/versions/20260209_add_mcp_servers.py` as specified in Section 4

**Ops tasks (after Engineer completes):**
- Run `./stack check` to verify
- Commit: "feat: Add McpServer DB model for user-managed MCP connections"

**Files affected:**
- `services/agent/src/core/db/models.py` (modify -- add McpServer class)
- `services/agent/alembic/versions/20260209_add_mcp_servers.py` (create)

---

### Phase 2: Token Manager Dynamic Provider Support (Step 2)

**Engineer tasks:**
1. Add `register_dynamic_provider()` and `unregister_dynamic_provider()` methods to `TokenManager` in `services/agent/src/core/auth/token_manager.py` (after `revoke_token()`, around line 115)

**Ops tasks:**
- Run `./stack check`
- Commit: "feat: Add dynamic OAuth provider registration to TokenManager"

**Files affected:**
- `services/agent/src/core/auth/token_manager.py` (modify)

---

### Phase 3: Client Pool User MCP Loading + Zapier Removal (Step 3)

**Engineer tasks:**
1. Add `from datetime import UTC, datetime` to imports in `services/agent/src/core/mcp/client_pool.py` (line 7 area)
2. **Remove the Zapier hardcoded provider block** from `get_clients()`. Find the block that creates the Zapier `McpClient` from env vars and remove it entirely. Context7 stays.
3. Add `_load_user_mcp_servers()` method to `McpClientPool` class as specified in Section 5
4. In `get_clients()`, add the integration call right before `# Store in cache` comment (currently line 271):
   ```python
               # Load user-defined MCP servers from database
               user_attempted = await self._load_user_mcp_servers(
                   context_id, session, clients
               )
               if user_attempted:
                   connection_attempted = True
   ```

**Important:** The existing Context7 code stays UNTOUCHED. The Zapier hardcoded provider block will be **removed** from `get_clients()` -- Zapier becomes user-managed. The new code is otherwise purely additive.

**Ops tasks:**
- Run `./stack check`
- Commit: "feat: Remove Zapier hardcoded provider, load user-defined MCP servers from DB"

**Files affected:**
- `services/agent/src/core/mcp/client_pool.py` (modify)

---

### Phase 4: Admin API Endpoints (Step 4)

**Engineer tasks:**
1. Add Pydantic models to `services/agent/src/interfaces/http/admin_mcp.py` as specified in Section 6
2. Add CRUD endpoints:
   - `GET /platformadmin/mcp/servers` -- list all servers
   - `POST /platformadmin/mcp/servers` -- create server
   - `PUT /platformadmin/mcp/servers/{server_id}` -- update server
   - `DELETE /platformadmin/mcp/servers/{server_id}` -- delete server
   - `POST /platformadmin/mcp/servers/{server_id}/test` -- test connection
   - `POST /platformadmin/mcp/servers/{server_id}/oauth/start` -- start OAuth flow
3. Add required imports:
   - `import asyncio` (top of file)
   - `from core.db.models import Context, McpServer` (add McpServer to existing import)

**Key validation rules for create/update endpoints:**
- URL must use `http` or `https` scheme (SSRF protection)
- `transport` must be one of: `auto`, `sse`, `streamable_http`
- `auth_type` must be one of: `none`, `bearer`, `oauth`
- Bearer requires `auth_token`
- OAuth requires `oauth_authorize_url`, `oauth_token_url`, `oauth_client_id`
- Name must be unique per context
- All secrets encrypted using `McpServer.set_auth_token()` / `set_oauth_client_secret()`

**Ops tasks:**
- Run `./stack check`
- Commit: "feat: Add MCP server CRUD and OAuth API endpoints"

**Files affected:**
- `services/agent/src/interfaces/http/admin_mcp.py` (modify)

---

### Phase 5: Admin UI Template (Step 5)

**Engineer tasks:**
1. Create `services/agent/src/interfaces/http/templates/admin_mcp.html` with:
   - Stats grid (total, connected, error, pending counts)
   - Server list table with status badges and action buttons
   - Add Server modal with dynamic auth fields
   - Edit Server modal (pre-filled)
   - Test Connection button with inline results
   - OAuth Authorize button for OAuth servers
   - Toast notifications
   - JavaScript for all CRUD operations

2. Update the `mcp_dashboard()` endpoint in `admin_mcp.py` to load the template:
   ```python
   from pathlib import Path

   @router.get("/", response_class=UTF8HTMLResponse)
   async def mcp_dashboard(admin: AdminUser = Depends(require_admin_or_redirect)) -> str:
       template_path = Path(__file__).parent / "templates" / "admin_mcp.html"
       template_content = template_path.read_text(encoding="utf-8")
       # The template contains {content}, {extra_css}, {extra_js} sections
       # Or load as one piece and inject into render_admin_page
       return render_admin_page(
           title="MCP Integrations",
           active_page="/platformadmin/mcp/",
           content=template_content,
           user_name=admin.display_name or admin.email.split("@")[0],
           user_email=admin.email,
           breadcrumbs=[("MCP Integrations", "#")],
       )
   ```

**Pattern reference:** Follow `admin_credentials.py` which has:
- Stats grid → `loadCredentials()` populates counts
- Table with data → `renderCredentials()` builds rows
- Modal form → `openAddModal()`, `submitCredential()`
- CSRF auto-injected by `render_admin_page()`'s shared JS

**Ops tasks:**
- Run `./stack check`
- Commit: "feat: Add MCP server management UI template"

**Files affected:**
- `services/agent/src/interfaces/http/templates/admin_mcp.html` (create)
- `services/agent/src/interfaces/http/admin_mcp.py` (modify -- dashboard endpoint)

---

### Phase 6: Tests (Step 6)

**Engineer tasks:**

Create `services/agent/src/core/tests/test_mcp_servers.py` with tests:

1. **Model tests:**
   - `test_mcp_server_model_defaults()` -- verify default values
   - `test_mcp_server_encrypt_decrypt_auth_token()` -- roundtrip encryption
   - `test_mcp_server_encrypt_decrypt_oauth_secret()` -- roundtrip encryption
   - `test_mcp_server_set_auth_token_none()` -- None handling

2. **Client pool tests:**
   - `test_load_user_mcp_servers_no_servers()` -- empty DB returns no clients
   - `test_load_user_mcp_servers_disabled_skipped()` -- disabled servers not loaded
   - `test_load_user_mcp_servers_bearer_auth()` -- bearer token passed to McpClient
   - `test_load_user_mcp_servers_connection_error_updates_status()` -- error handling

3. **API endpoint tests (using TestClient):**
   - `test_create_mcp_server_success()`
   - `test_create_mcp_server_duplicate_name_rejected()`
   - `test_create_mcp_server_invalid_url_rejected()`
   - `test_create_mcp_server_bearer_without_token_rejected()`
   - `test_list_mcp_servers()`
   - `test_delete_mcp_server()`
   - `test_test_mcp_server_connection()`

4. **TokenManager tests:**
   - `test_register_dynamic_provider()`
   - `test_unregister_dynamic_provider()`

**Test patterns to follow:**
- Use `@pytest.mark.asyncio` for all async tests
- Mock `McpClient.connect()` and `McpClient.disconnect()`
- Mock database session using `AsyncMock` and `MagicMock`
- Follow naming: `test_<action>_<scenario>_<expected>`
- See existing `test_credential_service.py` and `test_oauth_client.py` for patterns

**Ops tasks:**
- Run `./stack check`
- Commit: "test: Add tests for user-managed MCP server functionality"

**Files affected:**
- `services/agent/src/core/tests/test_mcp_servers.py` (create)

---

### Phase 7: Documentation Updates (Step 7)

**Engineer tasks (or delegate to Simple Tasks Haiku):**

1. **Update CLAUDE.md** -- Add MCP server management to the "Admin Portal" section and "Skills & Tools Architecture" section. Add the new admin module pattern.

2. **.env.template** -- No new env vars needed. User MCP config is stored in DB, not env vars. The existing `AGENT_OAUTH_REDIRECT_URI` and `AGENT_CREDENTIAL_ENCRYPTION_KEY` are already documented and are prerequisites.

**Ops tasks:**
- Run `./stack check`
- Final commit: "docs: Document user-managed MCP server feature"
- Create PR

**Files affected:**
- `CLAUDE.md` (modify -- add MCP management section)

---

### Phase 8: OAuth Auto-Refresh for Dynamic Providers (Step 8)

**Why:** Without auto-refresh, users must manually re-authorize OAuth MCP servers every time tokens expire. This is a poor UX for servers with short-lived tokens (1h is common).

**Engineer tasks:**

1. **Store refresh tokens** -- The `OAuthToken` model already stores `refresh_token`. Verify that the OAuth callback exchange stores the refresh token from the token response.

2. **Add `refresh_token_for_dynamic_provider()` to `OAuthClient`:**
   ```python
   async def refresh_token_for_dynamic_provider(
       self,
       provider_name: str,
       context_id: UUID,
   ) -> str | None:
       """Refresh an expired OAuth token for a dynamic MCP provider.

       Looks up the McpServer's OAuth config from DB, uses the stored
       refresh_token to get a new access_token. Implements refresh token
       rotation (OAuth 2.1): the new refresh_token replaces the old one.

       Returns the new access_token, or None if refresh fails.
       """
   ```
   - Query `McpServer` by `oauth_provider_name` to get OAuth config (token_url, client_id, client_secret)
   - Query `OAuthToken` for the existing token (by provider + context_id)
   - POST to token_url with `grant_type=refresh_token`
   - Store new access_token + new refresh_token (rotation)
   - Return new access_token

3. **Hook into `McpClient._get_auth_token()`** -- When token fetch returns an expired/invalid token:
   - Attempt refresh via `refresh_token_for_dynamic_provider()`
   - If refresh succeeds, use new token
   - If refresh fails (e.g., refresh token also expired), update `McpServer.status = "error"` and `McpServer.last_error = "OAuth token expired, re-authorization required"`

4. **Add refresh token rotation** -- When a new refresh_token is received in the refresh response, replace the stored one. This is OAuth 2.1 compliance (prevents replay of old refresh tokens).

**Tests:**
- `test_refresh_dynamic_provider_success()` -- mock token endpoint, verify new tokens stored
- `test_refresh_dynamic_provider_expired_refresh_token()` -- verify graceful fallback to error status
- `test_refresh_token_rotation()` -- verify old refresh token replaced

**Ops tasks:**
- Run `./stack check`
- Commit: "feat: Add OAuth auto-refresh for user-defined MCP servers"

**Files affected:**
- `services/agent/src/core/auth/oauth_client.py` (modify -- add refresh method)
- `services/agent/src/core/mcp/client.py` (modify -- refresh on expired token)
- `services/agent/src/core/tests/test_mcp_servers.py` (modify -- add refresh tests)

---

### Phase 9: OAuth 2.1 Standards for OAuth-Enabled Servers (Step 9)

**Why:** OAuth 2.1 (RFC draft) tightens security over OAuth 2.0. Since we are building a new OAuth integration from scratch, we should follow 2.1 from day one rather than retrofitting later. This only applies when users choose `auth_type=oauth` -- servers with `auth_type=none` or `auth_type=bearer` are unaffected.

**Engineer tasks:**

1. **Enforce PKCE on all dynamic provider flows:**
   - In `start_mcp_oauth()`, verify that the `OAuthClient.get_authorization_url()` always generates a `code_verifier` + `code_challenge` (S256 method)
   - Store `code_verifier` in the `OAuthState` row (it already has a `code_verifier` field if PKCE is used)
   - In the callback exchange, always send `code_verifier` -- reject flows without it
   - If the existing `OAuthClient` supports optional PKCE, make it **mandatory** for providers with names starting with `mcp_`

2. **Block implicit grant:**
   - Validate that `response_type` is always `code` (never `token`)
   - The current implementation already uses authorization code flow, but add explicit validation

3. **Strict redirect URI matching:**
   - In the OAuth state validation, compare redirect URI as exact string match (no pattern/wildcard)
   - The existing implementation likely already does this via PKCE state binding, but verify and add explicit check

4. **Document OAuth 2.1 requirements** in the admin UI:
   - When auth_type=oauth is selected, show a note: "OAuth 2.1: PKCE is required. The authorization server must support S256 code challenge method."
   - If a user's OAuth server doesn't support PKCE, they should use bearer token auth instead

**Tests:**
- `test_oauth_start_always_includes_pkce()` -- verify code_challenge in authorization URL
- `test_oauth_callback_rejects_without_code_verifier()` -- verify PKCE enforcement
- `test_oauth_redirect_uri_exact_match()` -- verify no wildcard matching

**Ops tasks:**
- Run `./stack check`
- Commit: "feat: Enforce OAuth 2.1 (mandatory PKCE, strict redirect URI) for MCP OAuth"

**Files affected:**
- `services/agent/src/core/auth/oauth_client.py` (modify -- enforce PKCE for mcp_ providers)
- `services/agent/src/interfaces/http/admin_mcp.py` (modify -- validation + UI note)
- `services/agent/src/core/tests/test_mcp_servers.py` (modify -- add OAuth 2.1 tests)

---

## 10. Testing Strategy

### Unit Tests
- Model encryption/decryption roundtrip
- Client pool loading logic with mocked DB and McpClient
- API endpoint validation (bad URLs, missing fields, duplicates)
- TokenManager dynamic provider registration

### Integration Tests (manual)
- Create MCP server via admin UI
- Test connection button works
- Agent chat request picks up new MCP tools
- Delete server removes tools from next request
- OAuth flow for MCP server (requires a real OAuth provider)

### Quality Gate
```bash
./stack check
```
Must pass: Ruff, Black, Mypy, Pytest

---

## 11. Security Considerations

### OWASP Top 10 Review

| # | Vulnerability | Mitigation |
|---|---|---|
| 1 | SQL Injection | SQLAlchemy ORM with parameterized queries |
| 2 | Auth bypass | All endpoints require `verify_admin_user` or `verify_user` |
| 3 | Input validation | Pydantic models validate all inputs; URL scheme whitelist |
| 4 | XSS | `html.escape()` on all user data; CSP headers via middleware |
| 5 | CSRF | `require_csrf` dependency on all POST/PUT/DELETE endpoints |
| 6 | Security headers | Handled by existing middleware in app.py |
| 7 | SSRF | URL scheme validation (http/https only); no internal URL resolution |
| 8 | Sensitive data | All tokens/secrets encrypted with Fernet before DB storage |
| 9 | Error handling | Generic messages to users; detailed errors only in logs |

### Platform-Specific Security

- **Multi-tenant isolation:** All MCP servers scoped to `context_id`; users can only see/modify servers in their own context
- **Credential encryption:** Bearer tokens and OAuth client secrets encrypted at rest using existing Fernet infrastructure
- **Connection timeouts:** All MCP connections have 10s timeout to prevent resource exhaustion
- **Admin-only access:** All CRUD endpoints require admin role (DB-verified, not header-trusted)
- **Pool cache invalidation:** Creating/deleting a server invalidates the MCP client pool cache for that context

---

## 12. Success Criteria

1. Admin can create MCP server connections via `/platformadmin/mcp/`
2. Admin can test connectivity and see tools discovered
3. Agent requests automatically pick up user-defined MCP tools
4. Bearer token auth works end-to-end
5. OAuth 2.0 auth flow works for MCP servers that require it
6. Connection errors are surfaced in admin dashboard
7. Deleting a server removes its tools from subsequent requests
8. All secrets encrypted at rest
9. `./stack check` passes (Ruff + Black + Mypy + Pytest)
10. Existing Context7 hardcoded provider continues working
11. OAuth flows use PKCE (OAuth 2.1 compliance)
12. OAuth auto-refresh works for user-defined MCP servers (Phase 8)

---

## 13. Agent Delegation

### Engineer (Sonnet) - Implementation
- Write all new code (model, migration, pool changes, endpoints, UI template, tests)
- Debug Mypy type errors
- Handle complex integration logic

### Ops (Haiku - 10x cheaper) - Quality and Deployment
- Run `./stack check` after each phase
- Fix simple lint/format errors (auto-fixable)
- Git commit after each phase
- Create PR when all phases complete

### Simple Tasks (Haiku) - Documentation
- Update CLAUDE.md documentation section (Phase 7)

### Cost Optimization
Each implementation step follows this pattern:
1. Engineer writes/modifies code
2. Ops runs quality check: `./stack check`
3. Ops commits if passing (or escalates to Engineer for complex errors)
4. Repeat for next step

---

## 14. Risk Assessment

| Risk | Impact | Likelihood | Mitigation |
|------|--------|-----------|------------|
| MCP connection hangs block agent requests | High | Medium | Background connection in `service_factory.py` already handles this; user MCPs follow same pattern |
| OAuth callback fails for dynamic providers | Medium | Low | `register_dynamic_provider()` ensures config is available; fallback is manual re-authorization |
| Migration breaks existing DB | High | Very Low | Purely additive migration (new table only, no existing table changes) |
| Large number of user MCPs slows startup | Medium | Low | Negative cache prevents retry storms; connections are lazy (only on first request per context) |
| Refresh token rotation breaks concurrent requests | Low | Low | Serialize refresh with a per-provider lock; only one refresh at a time |
| OAuth server doesn't support PKCE | Medium | Medium | Document requirement in UI; fallback is bearer token auth |
| Removing Zapier hardcoded breaks existing users | Medium | Low | Users re-add Zapier as user-managed server; migration guide in docs |

---

## 15. Files Summary

### New Files
| File | Description |
|------|-------------|
| `services/agent/alembic/versions/20260209_add_mcp_servers.py` | Alembic migration for `mcp_servers` table |
| `services/agent/src/interfaces/http/templates/admin_mcp.html` | MCP management UI template |
| `services/agent/src/core/tests/test_mcp_servers.py` | Unit tests for MCP server feature |

### Modified Files
| File | Changes |
|------|---------|
| `services/agent/src/core/db/models.py` | Add `McpServer` model class + encryption helpers |
| `services/agent/src/core/mcp/client_pool.py` | Add `_load_user_mcp_servers()` + integration in `get_clients()` |
| `services/agent/src/core/auth/token_manager.py` | Add `register_dynamic_provider()` / `unregister_dynamic_provider()` |
| `services/agent/src/interfaces/http/admin_mcp.py` | Add CRUD + OAuth endpoints, Pydantic models, update dashboard, OAuth 2.1 notes |
| `services/agent/src/core/auth/oauth_client.py` | Add `refresh_token_for_dynamic_provider()`, enforce mandatory PKCE for mcp_ providers |
| `services/agent/src/core/mcp/client.py` | Hook refresh on expired token in `_get_auth_token()` |
| `CLAUDE.md` | Document MCP server management feature |
