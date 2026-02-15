"""Per-context MCP client pool manager.

This module manages MCP clients on a per-context basis, enabling:
- OAuth token-based authentication (per-context isolation)
- Client caching and reuse
- Automatic reconnection on connection loss
- Health monitoring
- Graceful shutdown
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.oauth_models import OAuthToken
from core.mcp.client import McpClient
from core.runtime.config import Settings

LOGGER = logging.getLogger(__name__)


class McpClientPool:
    """Manages MCP clients per context with OAuth token support.

    Each context can have multiple MCP clients (one per provider).
    Clients are created on-demand and cached for reuse.
    """

    def __init__(self, settings: Settings):
        """Initialize the MCP client pool.

        Args:
            settings: Application settings
        """
        self._settings = settings
        self._pools: dict[UUID, list[McpClient]] = defaultdict(list)
        self._locks: dict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._timestamps: dict[UUID, float] = {}
        self._cache_ttl = 300  # 5 minutes
        self._negative_cache: dict[UUID, float] = {}  # context_id → failure timestamp
        self._negative_cache_ttl = 300  # Don't retry failed connections for 5 minutes
        self._eviction_task: asyncio.Task[None] | None = None

    async def get_clients(
        self,
        context_id: UUID,
        session: AsyncSession,
    ) -> list[McpClient]:
        """Get or create MCP clients for a context.

        This method:
        1. Checks negative cache (skip if recently failed)
        2. Checks cache for existing clients
        3. Validates cached clients are still connected
        4. Loads OAuth tokens for this context
        5. Creates clients for each authorized provider
        6. Caches clients for reuse

        Args:
            context_id: Context UUID
            session: Database session for loading OAuth tokens

        Returns:
            List of MCP clients for this context (may be empty)
        """
        # Check negative cache - don't retry recently failed connections
        if context_id in self._negative_cache:
            elapsed = time.monotonic() - self._negative_cache[context_id]
            if elapsed < self._negative_cache_ttl:
                LOGGER.debug(
                    "Skipping MCP for context %s (failed %.0fs ago, retry in %.0fs)",
                    context_id,
                    elapsed,
                    self._negative_cache_ttl - elapsed,
                )
                return []
            # TTL expired - clear negative cache and retry
            del self._negative_cache[context_id]

        # Check cache first and validate existing clients
        if context_id in self._pools and self._pools[context_id]:
            # Parallelize health checks for all cached clients
            async def check_client_health(client: McpClient) -> McpClient | None:
                """Check if a client is healthy. Returns client if healthy, None otherwise."""
                if not client.is_connected:
                    LOGGER.warning(f"Client {client.name} disconnected, removing from pool")
                    try:
                        await client.disconnect()
                    except Exception as e:
                        LOGGER.error(f"Error disconnecting stale client: {e}")
                    return None

                try:
                    # Quick ping to verify connection
                    is_healthy = await asyncio.wait_for(client.ping(), timeout=2.0)
                    if is_healthy:
                        return client
                    else:
                        LOGGER.warning(
                            f"Client {client.name} failed health check, removing from pool"
                        )
                        try:
                            await client.disconnect()
                        except Exception as e:
                            LOGGER.error(f"Error disconnecting unhealthy client: {e}")
                        return None
                except TimeoutError:
                    LOGGER.warning(f"Client {client.name} ping timeout, removing from pool")
                    try:
                        await client.disconnect()
                    except Exception as e:
                        LOGGER.error(f"Error disconnecting timed-out client: {e}")
                    return None

            # Check all clients in parallel
            health_results = await asyncio.gather(
                *[check_client_health(client) for client in self._pools[context_id]],
                return_exceptions=True,
            )

            # Filter out None results and exceptions
            valid_clients: list[McpClient] = []
            for health_check_result in health_results:
                if isinstance(health_check_result, McpClient):
                    valid_clients.append(health_check_result)
                elif isinstance(health_check_result, Exception):
                    LOGGER.error(f"Error during health check: {health_check_result}")
                # else: health_check_result is None, skip

            if valid_clients:
                self._pools[context_id] = valid_clients
                LOGGER.debug(
                    f"Using {len(valid_clients)} cached MCP clients for context {context_id}"
                )
                return valid_clients

        # Need to create new clients - acquire lock to prevent duplicates
        async with self._locks[context_id]:
            # Double-check after acquiring lock
            if context_id in self._pools and self._pools[context_id]:
                LOGGER.debug(f"Found clients after acquiring lock for context {context_id}")
                return self._pools[context_id]

            # Load OAuth tokens for this context
            stmt = select(OAuthToken).where(OAuthToken.context_id == context_id)
            result = await session.execute(stmt)
            tokens = result.scalars().all()

            LOGGER.debug(f"Found {len(tokens)} OAuth tokens for context {context_id}")

            clients = []
            connection_attempted = False

            # Create clients for each authorized provider
            for token in tokens:
                provider = token.provider.lower()

                # Context7 MCP (future provider)
                if provider == "context7" and self._settings.context7_mcp_url:
                    connection_attempted = True
                    try:
                        client = McpClient(
                            url=str(self._settings.context7_mcp_url),
                            context_id=context_id,
                            oauth_provider="context7",
                            name="Context7",
                            auto_reconnect=True,
                            max_retries=1,
                            cache_ttl_seconds=300,
                        )
                        await asyncio.wait_for(client.connect(), timeout=5.0)
                        clients.append(client)
                        LOGGER.info(
                            f"Connected Context7 MCP for context {context_id} "
                            f"(discovered {len(client.tools)} tools)"
                        )
                    except Exception as e:
                        LOGGER.error(
                            f"Failed to connect Context7 MCP for context {context_id}: {e}"
                        )

            # Load user-defined MCP servers from database
            user_attempted = await self._load_user_mcp_servers(context_id, session, clients)
            if user_attempted:
                connection_attempted = True

            # Store in cache with timestamp for eviction
            self._pools[context_id] = clients
            self._timestamps[context_id] = time.monotonic()

            if clients:
                LOGGER.info(f"Created {len(clients)} MCP clients for context {context_id}")
                # Clear negative cache on success
                self._negative_cache.pop(context_id, None)
            elif connection_attempted:
                # Had credentials but failed to connect - negative cache to avoid retry storms
                self._negative_cache[context_id] = time.monotonic()
                LOGGER.info(
                    f"MCP connection failed for context {context_id}, "
                    f"will retry in {self._negative_cache_ttl}s"
                )
            else:
                LOGGER.debug(
                    f"No MCP clients created for context {context_id} "
                    f"(no matching OAuth tokens or providers not configured)"
                )

            return clients

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

        stmt = select(McpServer).where(
            McpServer.context_id == context_id,
            McpServer.is_enabled.is_(True),
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

            # Register dynamic OAuth provider for token resolution
            if server.auth_type == "oauth" and server.oauth_provider_name:
                try:
                    from core.auth.models import OAuthProviderConfig
                    from core.providers import get_token_manager

                    if self._settings.oauth_redirect_uri:
                        from pydantic import HttpUrl

                        config = OAuthProviderConfig(
                            provider_name=server.oauth_provider_name,
                            authorization_url=HttpUrl(
                                server.oauth_authorize_url or "https://placeholder"
                            ),
                            token_url=HttpUrl(server.oauth_token_url or "https://placeholder"),
                            client_id=server.oauth_client_id or "",
                            client_secret=server.get_oauth_client_secret(),
                            scopes=server.oauth_scopes,
                            redirect_uri=self._settings.oauth_redirect_uri,
                        )
                        get_token_manager().register_dynamic_provider(
                            server.oauth_provider_name, config
                        )
                except Exception:
                    LOGGER.warning(
                        "Could not register dynamic OAuth provider for %s",
                        server.name,
                    )

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

        return connection_attempted

    async def disconnect_context(self, context_id: UUID) -> None:
        """Disconnect all clients for a context.

        Useful when revoking OAuth tokens or resetting a context.

        Args:
            context_id: Context UUID
        """
        if context_id not in self._pools:
            LOGGER.debug(f"No clients to disconnect for context {context_id}")
            return

        clients = self._pools[context_id]
        disconnect_errors = []

        for client in clients:
            try:
                await client.disconnect()
                LOGGER.debug(f"Disconnected {client.name} for context {context_id}")
            except Exception as e:
                disconnect_errors.append((client.name, e))
                LOGGER.warning(f"Error disconnecting {client.name}: {e}")

        # Remove from pool, timestamps, and locks
        del self._pools[context_id]
        self._timestamps.pop(context_id, None)
        self._locks.pop(context_id, None)

        if disconnect_errors:
            LOGGER.warning(
                f"Disconnected {len(clients)} clients for context {context_id} "
                f"with {len(disconnect_errors)} errors"
            )
        else:
            LOGGER.info(
                f"Successfully disconnected {len(clients)} clients for context {context_id}"
            )

    async def _eviction_loop(self) -> None:
        """Periodically remove stale MCP clients."""
        while True:
            await asyncio.sleep(self._cache_ttl)
            await self._evict_stale_clients()

    async def _evict_stale_clients(self) -> None:
        """Disconnect and remove clients that have exceeded TTL."""
        now = time.monotonic()
        stale_contexts: list[UUID] = []

        for context_id, timestamp in list(self._timestamps.items()):
            if now - timestamp > self._cache_ttl:
                stale_contexts.append(context_id)

        for context_id in stale_contexts:
            LOGGER.info("Evicting stale MCP clients for context %s", context_id)
            await self.disconnect_context(context_id)

    def start_eviction(self) -> None:
        """Start background eviction loop."""
        if self._eviction_task is None:
            self._eviction_task = asyncio.create_task(self._eviction_loop())

    async def stop(self) -> None:
        """Stop eviction and disconnect all clients."""
        if self._eviction_task is not None:
            self._eviction_task.cancel()
            try:
                await self._eviction_task
            except asyncio.CancelledError:
                pass
            self._eviction_task = None
        await self.shutdown()

    async def shutdown(self) -> None:
        """Disconnect all clients across all contexts.

        Called during application shutdown.
        """
        LOGGER.info(f"Shutting down MCP client pool ({len(self._pools)} contexts)")

        total_clients = sum(len(clients) for clients in self._pools.values())
        disconnect_errors = []

        for context_id in list(self._pools.keys()):
            try:
                await self.disconnect_context(context_id)
            except Exception as e:
                disconnect_errors.append((context_id, e))
                LOGGER.error(f"Error disconnecting context {context_id}: {e}")

        if disconnect_errors:
            LOGGER.warning(
                f"Shutdown complete with {len(disconnect_errors)} errors "
                f"({total_clients} total clients)"
            )
        else:
            LOGGER.info(f"All {total_clients} MCP clients shut down successfully")

    def get_health_status(self) -> dict[str, dict[str, Any]]:
        """Get health status of all client pools.

        Returns:
            Dict mapping context_id (str) → health info
        """
        health: dict[str, dict[str, Any]] = {}

        for context_id, clients in self._pools.items():
            context_health = []
            for client in clients:
                context_health.append(
                    {
                        "name": client.name,
                        "connected": client.is_connected,
                        "state": client.state.name,
                        "tools_count": len(client.tools),
                        "resources_count": len(client.resources),
                        "prompts_count": len(client.prompts),
                        "cache_stale": client.is_cache_stale(),
                    }
                )

            health[str(context_id)] = {
                "clients": context_health,
                "total_clients": len(clients),
            }

        return health

    def get_stats(self) -> dict[str, Any]:
        """Get overall pool statistics.

        Returns:
            Statistics about the client pool
        """
        total_contexts = len(self._pools)
        total_clients = sum(len(clients) for clients in self._pools.values())
        connected_clients = sum(
            1 for clients in self._pools.values() for client in clients if client.is_connected
        )

        return {
            "total_contexts": total_contexts,
            "total_clients": total_clients,
            "connected_clients": connected_clients,
            "disconnected_clients": total_clients - connected_clients,
        }


__all__ = ["McpClientPool"]
