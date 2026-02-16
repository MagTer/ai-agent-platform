"""Admin endpoints for MCP integration management."""

# ruff: noqa: E501
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from shared.sanitize import sanitize_log
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import AsyncSessionLocal, get_db
from core.db.models import Context, McpServer
from core.db.oauth_models import OAuthToken
from core.tools.mcp_loader import get_mcp_client_pool, get_mcp_health, get_mcp_stats
from interfaces.http.admin_auth import AdminUser, require_admin_or_redirect, verify_admin_user
from interfaces.http.admin_shared import UTF8HTMLResponse, render_admin_page
from interfaces.http.csrf import require_csrf

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platformadmin/mcp",
    tags=["platform-admin", "mcp"],
)

VALID_TRANSPORTS = {"auto", "sse", "streamable_http"}
VALID_AUTH_TYPES = {"none", "bearer", "oauth"}

# SSRF Protection: Blocked internal Docker service hostnames
BLOCKED_HOSTNAMES = frozenset(
    {
        "postgres",
        "qdrant",
        "litellm",
        "redis",
        "searxng",
        "openwebui",
        "traefik",
        "webfetch",
        "embedder",
        "agent",
    }
)

# SSRF Protection: Private/reserved IP ranges
PRIVATE_IP_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

# Cache template at module level to avoid I/O on every request
_TEMPLATE_PATH = Path(__file__).parent / "templates" / "admin_mcp.html"
_TEMPLATE_PARTS = _TEMPLATE_PATH.read_text(encoding="utf-8").split("<!-- SECTION_SEPARATOR -->")


# -- SSRF Protection --


def _validate_mcp_server_url(url: str) -> None:
    """Validate MCP server URL to prevent SSRF attacks.

    Blocks:
    - Non-HTTP(S) schemes
    - Private/reserved IP ranges
    - Common internal Docker service hostnames

    Args:
        url: The URL to validate

    Raises:
        ValueError: If the URL is blocked
    """
    parsed = urlparse(url)

    # Only allow HTTP(S)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must use http or https scheme")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL missing hostname")

    # Block internal Docker service names
    if hostname.lower() in BLOCKED_HOSTNAMES:
        raise ValueError("Cannot connect to internal Docker services (blocked hostname: %s)" % hostname)

    # Resolve hostname to IP addresses
    try:
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ValueError("DNS resolution failed for %s: %s" % (hostname, e)) from e

    # Check all resolved IPs against private ranges
    for _family, _, _, _, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            ip_addr = ipaddress.ip_address(ip_str)
        except ValueError:
            LOGGER.debug("Invalid IP address from getaddrinfo: %s (unexpected)", ip_str, exc_info=True)
            continue

        # Check against private ranges
        for network in PRIVATE_IP_RANGES:
            if ip_addr in network:
                raise ValueError(
                    "Cannot connect to private/reserved IP addresses (blocked IP: %s from %s)"
                    % (ip_str, hostname)
                )


# -- Dashboard --


@router.get("/", response_class=UTF8HTMLResponse)
async def mcp_dashboard(admin: AdminUser = Depends(require_admin_or_redirect)) -> str:
    """MCP server management dashboard.

    Security:
        Requires admin role via Entra ID authentication.
    """
    # Use cached template parts instead of reading file on every request
    content = _TEMPLATE_PARTS[0] if len(_TEMPLATE_PARTS) > 0 else ""
    extra_css = _TEMPLATE_PARTS[1] if len(_TEMPLATE_PARTS) > 1 else ""
    extra_js = _TEMPLATE_PARTS[2] if len(_TEMPLATE_PARTS) > 2 else ""

    return render_admin_page(
        title="MCP Servers",
        active_page="/platformadmin/mcp/",
        content=content,
        user_name=admin.display_name or admin.email.split("@")[0],
        user_email=admin.email,
        breadcrumbs=[("MCP Servers", "#")],
        extra_css=extra_css,
        extra_js=extra_js,
    )


# -- Pydantic models for user-managed MCP servers --


class McpServerCreate(BaseModel):
    """Request to create an MCP server connection."""

    context_id: str
    name: str
    url: str
    transport: str = "auto"
    auth_type: str = "none"
    auth_token: str | None = None
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


# -- Server CRUD endpoints --


@router.get(
    "/servers",
    response_model=McpServerListResponse,
    dependencies=[Depends(verify_admin_user)],
)
async def list_mcp_servers(
    context_id: UUID | None = None,
    offset: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(50, ge=1, le=500, description="Max items to return"),
    session: AsyncSession = Depends(get_db),
) -> McpServerListResponse:
    """List all user-defined MCP servers across all contexts."""
    stmt = (
        select(McpServer, Context.name)
        .join(Context, McpServer.context_id == Context.id)
        .order_by(Context.name, McpServer.name)
    )
    if context_id:
        stmt = stmt.where(McpServer.context_id == context_id)

    # Apply pagination
    stmt = stmt.offset(offset).limit(limit)

    result = await session.execute(stmt)
    rows = result.all()

    servers = [
        McpServerInfo(
            id=str(server.id),
            context_id=str(server.context_id),
            context_name=ctx_name,
            name=server.name,
            url=server.url,
            transport=server.transport,
            auth_type=server.auth_type,
            is_enabled=server.is_enabled,
            status=server.status,
            last_error=server.last_error,
            last_connected_at=(
                server.last_connected_at.isoformat() if server.last_connected_at else None
            ),
            tools_count=server.tools_count,
            has_oauth_config=bool(server.oauth_client_id),
            created_at=server.created_at.isoformat(),
            updated_at=server.updated_at.isoformat(),
        )
        for server, ctx_name in rows
    ]

    return McpServerListResponse(servers=servers, total=len(servers))


@router.post(
    "/servers",
    dependencies=[Depends(require_csrf)],
)
async def create_mcp_server(
    request: McpServerCreate,
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, str | bool]:
    """Create a new user-defined MCP server connection."""
    # Validate context exists
    try:
        ctx_uuid = UUID(request.context_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid context_id format") from e

    ctx_result = await session.execute(select(Context).where(Context.id == ctx_uuid))
    if not ctx_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Context not found")

    # Validate transport
    if request.transport not in VALID_TRANSPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid transport. Must be one of: {', '.join(sorted(VALID_TRANSPORTS))}",
        )

    # Validate auth_type
    if request.auth_type not in VALID_AUTH_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid auth_type. Must be one of: {', '.join(sorted(VALID_AUTH_TYPES))}",
        )

    # Validate URL to prevent SSRF attacks
    try:
        _validate_mcp_server_url(request.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid URL: {e}") from e

    # Bearer requires auth_token
    if request.auth_type == "bearer" and not request.auth_token:
        raise HTTPException(status_code=400, detail="auth_token required for bearer auth")

    # OAuth requires required fields
    if request.auth_type == "oauth":
        if not all([request.oauth_authorize_url, request.oauth_token_url, request.oauth_client_id]):
            raise HTTPException(
                status_code=400,
                detail="oauth_authorize_url, oauth_token_url, and oauth_client_id required for OAuth auth",
            )

    # Check duplicate name within context
    dup_result = await session.execute(
        select(McpServer).where(McpServer.context_id == ctx_uuid, McpServer.name == request.name)
    )
    if dup_result.scalar_one_or_none():
        raise HTTPException(
            status_code=409, detail=f"MCP server '{request.name}' already exists in this context"
        )

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
        server.oauth_provider_name = f"mcp_{server.id}"
        server.oauth_authorize_url = request.oauth_authorize_url
        server.oauth_token_url = request.oauth_token_url
        server.oauth_client_id = request.oauth_client_id
        if request.oauth_client_secret:
            server.set_oauth_client_secret(request.oauth_client_secret)
        server.oauth_scopes = request.oauth_scopes

    session.add(server)
    await session.commit()

    # Invalidate MCP pool cache for this context
    try:
        pool = get_mcp_client_pool()
        await pool.disconnect_context(ctx_uuid)
    except RuntimeError:
        pass

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


@router.put(
    "/servers/{server_id}",
    dependencies=[Depends(require_csrf)],
)
async def update_mcp_server(
    server_id: UUID,
    request: McpServerUpdate,
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, str | bool]:
    """Update an existing MCP server connection."""
    result = await session.execute(select(McpServer).where(McpServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    if request.name is not None:
        # Check for duplicate name within context (excluding self)
        dup_result = await session.execute(
            select(McpServer).where(
                McpServer.context_id == server.context_id,
                McpServer.name == request.name,
                McpServer.id != server_id,
            )
        )
        if dup_result.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail=f"MCP server '{request.name}' already exists in this context",
            )
        server.name = request.name

    if request.url is not None:
        try:
            _validate_mcp_server_url(request.url)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid URL: {e}") from e
        server.url = request.url

    if request.transport is not None:
        if request.transport not in VALID_TRANSPORTS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid transport. Must be one of: {', '.join(sorted(VALID_TRANSPORTS))}",
            )
        server.transport = request.transport

    if request.auth_type is not None:
        if request.auth_type not in VALID_AUTH_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid auth_type. Must be one of: {', '.join(sorted(VALID_AUTH_TYPES))}",
            )
        server.auth_type = request.auth_type

    if request.auth_token is not None:
        server.set_auth_token(request.auth_token)

    if request.oauth_authorize_url is not None:
        server.oauth_authorize_url = request.oauth_authorize_url
    if request.oauth_token_url is not None:
        server.oauth_token_url = request.oauth_token_url
    if request.oauth_client_id is not None:
        server.oauth_client_id = request.oauth_client_id
    if request.oauth_client_secret is not None:
        server.set_oauth_client_secret(request.oauth_client_secret)
    if request.oauth_scopes is not None:
        server.oauth_scopes = request.oauth_scopes
    if request.is_enabled is not None:
        server.is_enabled = request.is_enabled

    # Set oauth_provider_name if switching to oauth
    if server.auth_type == "oauth" and not server.oauth_provider_name:
        server.oauth_provider_name = f"mcp_{server.id}"

    # Reset status on config change
    server.status = "pending"
    server.last_error = None

    await session.commit()

    # Invalidate MCP pool cache
    try:
        pool = get_mcp_client_pool()
        await pool.disconnect_context(server.context_id)
    except RuntimeError:
        pass

    LOGGER.info(
        "Admin %s updated MCP server '%s' (%s)",
        sanitize_log(admin.email),
        sanitize_log(server.name),
        sanitize_log(server_id),
    )

    return {"success": True, "message": f"MCP server '{server.name}' updated."}


@router.delete(
    "/servers/{server_id}",
    dependencies=[Depends(require_csrf)],
)
async def delete_mcp_server(
    server_id: UUID,
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, str | bool]:
    """Delete an MCP server connection."""
    result = await session.execute(select(McpServer).where(McpServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    server_name = server.name
    context_id = server.context_id

    await session.delete(server)
    await session.commit()

    # Invalidate MCP pool cache
    try:
        pool = get_mcp_client_pool()
        await pool.disconnect_context(context_id)
    except RuntimeError:
        pass

    LOGGER.info(
        "Admin %s deleted MCP server '%s' (%s)",
        sanitize_log(admin.email),
        sanitize_log(server_name),
        sanitize_log(server_id),
    )

    return {"success": True, "message": f"MCP server '{server_name}' deleted."}


@router.post(
    "/servers/{server_id}/test",
    dependencies=[Depends(require_csrf)],
)
async def test_mcp_server(
    server_id: UUID,
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Test connectivity to a user-defined MCP server."""
    from core.mcp.client import McpClient, McpTransport

    result = await session.execute(select(McpServer).where(McpServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    # Determine auth
    auth_token: str | None = None
    if server.auth_type == "bearer":
        auth_token = server.get_auth_token()

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


@router.post(
    "/servers/{server_id}/oauth/start",
    dependencies=[Depends(require_csrf)],
)
async def start_mcp_oauth(
    server_id: UUID,
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Start OAuth authorization flow for an MCP server."""
    from core.auth.models import OAuthProviderConfig
    from core.auth.oauth_client import OAuthClient
    from core.providers import get_token_manager
    from core.runtime.config import get_settings

    result = await session.execute(select(McpServer).where(McpServer.id == server_id))
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
    from pydantic import HttpUrl

    provider_config = OAuthProviderConfig(
        provider_name=server.oauth_provider_name,
        authorization_url=HttpUrl(server.oauth_authorize_url or ""),
        token_url=HttpUrl(server.oauth_token_url or ""),
        client_id=server.oauth_client_id or "",
        client_secret=server.get_oauth_client_secret(),
        scopes=server.oauth_scopes,
        redirect_uri=settings.oauth_redirect_uri,
    )

    # Register with global TokenManager so the callback can find the config
    token_manager = get_token_manager()
    token_manager.register_dynamic_provider(server.oauth_provider_name, provider_config)

    # Create temporary OAuth client for this provider
    oauth_client = OAuthClient(
        session_factory=AsyncSessionLocal,
        provider_configs={server.oauth_provider_name: provider_config},
    )

    # Generate authorization URL (with mandatory PKCE)
    auth_url, state_value = await oauth_client.get_authorization_url(
        provider=server.oauth_provider_name,
        context_id=server.context_id,
        user_id=admin.user_id,
    )

    await session.commit()

    return {
        "authorization_url": auth_url,
        "state": state_value,
        "message": f"Visit the URL to authorize '{server.name}'. After authorization, the token will be stored automatically.",
    }


# -- Legacy integration views (kept for backward compatibility) --


class MCPIntegrationItem(BaseModel):
    """Single MCP integration entry."""

    context_id: str
    context_name: str | None
    provider: str
    type: str
    status: str
    updated_at: str | None


class MCPIntegrationsResponse(BaseModel):
    """List of configured MCP integrations."""

    integrations: list[MCPIntegrationItem]


class MCPActivityEvent(BaseModel):
    """Single MCP activity event."""

    timestamp: str
    context_id: str | None
    provider: str | None
    result: str
    tools_count: int | None
    error: str | None
    transport: str | None


class MCPActivityResponse(BaseModel):
    """List of recent MCP activity events."""

    events: list[MCPActivityEvent]


@router.get(
    "/integrations",
    response_model=MCPIntegrationsResponse,
    dependencies=[Depends(verify_admin_user)],
)
async def get_mcp_integrations(
    session: AsyncSession = Depends(get_db),
) -> MCPIntegrationsResponse:
    """Get configured MCP integrations per context (legacy + user-defined)."""
    integrations: list[MCPIntegrationItem] = []

    # OAuth-based integrations from OAuthToken table
    oauth_stmt = (
        select(OAuthToken, Context.name)
        .join(Context, OAuthToken.context_id == Context.id)
        .order_by(Context.name, OAuthToken.provider)
    )
    oauth_result = await session.execute(oauth_stmt)

    now = datetime.now(UTC).replace(tzinfo=None)
    for token, context_name in oauth_result.all():
        is_expired = token.expires_at < now
        integrations.append(
            MCPIntegrationItem(
                context_id=str(token.context_id),
                context_name=context_name,
                provider=token.provider.capitalize(),
                type="oauth",
                status="expired" if is_expired else "active",
                updated_at=token.updated_at.isoformat() if token.updated_at else None,
            )
        )

    # User-defined MCP servers
    mcp_stmt = (
        select(McpServer, Context.name)
        .join(Context, McpServer.context_id == Context.id)
        .order_by(Context.name, McpServer.name)
    )
    mcp_result = await session.execute(mcp_stmt)
    for server, context_name in mcp_result.all():
        integrations.append(
            MCPIntegrationItem(
                context_id=str(server.context_id),
                context_name=context_name,
                provider=server.name,
                type=server.auth_type,
                status=server.status,
                updated_at=server.updated_at.isoformat() if server.updated_at else None,
            )
        )

    return MCPIntegrationsResponse(integrations=integrations)


@router.get(
    "/activity",
    response_model=MCPActivityResponse,
    dependencies=[Depends(verify_admin_user)],
)
async def get_mcp_activity(
    session: AsyncSession = Depends(get_db),
) -> MCPActivityResponse:
    """Get recent MCP connection events from debug logs (JSONL)."""
    from core.observability.debug_logger import read_debug_logs

    # Read MCP-related events from JSONL
    logs = await read_debug_logs(limit=200)
    mcp_logs = [log for log in logs if log.get("event_type") in ("mcp_connect", "mcp_error")][:50]

    events: list[MCPActivityEvent] = []
    for log in mcp_logs:
        data = log.get("event_data", {})
        is_error = log.get("event_type") == "mcp_error"
        events.append(
            MCPActivityEvent(
                timestamp=log.get("timestamp", ""),
                context_id=log.get("trace_id", ""),
                provider=data.get("provider"),
                result="error" if is_error else "connected",
                tools_count=data.get("tools_count"),
                error=data.get("error") if is_error else None,
                transport=data.get("transport"),
            )
        )

    return MCPActivityResponse(events=events)


# -- Health / Stats / Disconnect endpoints --


class MCPHealthResponse(BaseModel):
    """MCP client health status response."""

    health: dict[str, Any]


class MCPStatsResponse(BaseModel):
    """MCP client pool statistics response."""

    stats: dict[str, Any]


class DisconnectResponse(BaseModel):
    """Response after disconnecting MCP clients."""

    success: bool
    message: str
    context_id: UUID


@router.get("/health", response_model=MCPHealthResponse, dependencies=[Depends(verify_admin_user)])
async def get_mcp_client_health() -> MCPHealthResponse:
    """Get health status of all MCP client pools across all contexts."""
    health = await get_mcp_health()
    return MCPHealthResponse(health=health)


@router.get("/stats", response_model=MCPStatsResponse, dependencies=[Depends(verify_admin_user)])
async def get_mcp_client_stats() -> MCPStatsResponse:
    """Get overall MCP client pool statistics."""
    stats = get_mcp_stats()
    return MCPStatsResponse(stats=stats)


@router.post(
    "/disconnect/{context_id}",
    response_model=DisconnectResponse,
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def disconnect_mcp_clients(context_id: UUID) -> DisconnectResponse:
    """Force disconnect all MCP clients for a context."""
    try:
        pool = get_mcp_client_pool()
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"MCP client pool not initialized: {e}",
        ) from e

    await pool.disconnect_context(context_id)

    LOGGER.info("Admin disconnected MCP clients for context %s", sanitize_log(context_id))

    return DisconnectResponse(
        success=True,
        message=f"Disconnected all MCP clients for context {context_id}",
        context_id=context_id,
    )


__all__ = ["router"]
