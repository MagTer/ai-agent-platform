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
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.credential_service import CredentialService
from core.core.config import Settings
from core.db.models import DebugLog, UserContext
from core.db.oauth_models import OAuthToken
from core.mcp.client import McpClient

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
        self._eviction_task: asyncio.Task[None] | None = None

    async def get_clients(
        self,
        context_id: UUID,
        session: AsyncSession,
    ) -> list[McpClient]:
        """Get or create MCP clients for a context.

        This method:
        1. Checks cache for existing clients
        2. Validates cached clients are still connected
        3. Loads OAuth tokens for this context
        4. Creates clients for each authorized provider
        5. Caches clients for reuse

        Args:
            context_id: Context UUID
            session: Database session for loading OAuth tokens

        Returns:
            List of MCP clients for this context (may be empty)
        """
        # Check cache first and validate existing clients
        if context_id in self._pools and self._pools[context_id]:
            valid_clients = []
            for client in self._pools[context_id]:
                # Check if client is still connected and healthy
                if client.is_connected:
                    try:
                        # Quick ping to verify connection
                        is_healthy = await asyncio.wait_for(client.ping(), timeout=2.0)
                        if is_healthy:
                            valid_clients.append(client)
                        else:
                            LOGGER.warning(
                                f"Client {client.name} failed health check, removing from pool"
                            )
                            try:
                                await client.disconnect()
                            except Exception as e:
                                LOGGER.error(f"Error disconnecting unhealthy client: {e}")
                    except TimeoutError:
                        LOGGER.warning(f"Client {client.name} ping timeout, removing from pool")
                        try:
                            await client.disconnect()
                        except Exception as e:
                            LOGGER.error(f"Error disconnecting timed-out client: {e}")
                else:
                    LOGGER.warning(f"Client {client.name} disconnected, removing from pool")
                    try:
                        await client.disconnect()
                    except Exception as e:
                        LOGGER.error(f"Error disconnecting stale client: {e}")

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

            # Create clients for each authorized provider
            for token in tokens:
                provider = token.provider.lower()

                # Context7 MCP (future provider)
                if provider == "context7" and self._settings.context7_mcp_url:
                    try:
                        client = McpClient(
                            url=str(self._settings.context7_mcp_url),
                            context_id=context_id,
                            oauth_provider="context7",
                            name="Context7",
                            auto_reconnect=True,
                            max_retries=3,
                            cache_ttl_seconds=300,
                        )
                        await client.connect()
                        clients.append(client)
                        LOGGER.info(
                            f"Connected Context7 MCP for context {context_id} "
                            f"(discovered {len(client.tools)} tools)"
                        )
                        session.add(
                            DebugLog(
                                trace_id=str(context_id),
                                event_type="mcp_connect",
                                event_data={
                                    "provider": "Context7",
                                    "tools_count": len(client.tools),
                                    "transport": "streamable_http",
                                },
                            )
                        )
                    except Exception as e:
                        LOGGER.error(
                            f"Failed to connect Context7 MCP for context {context_id}: {e}"
                        )
                        session.add(
                            DebugLog(
                                trace_id=str(context_id),
                                event_type="mcp_error",
                                event_data={
                                    "provider": "Context7",
                                    "error": str(e),
                                },
                            )
                        )

                # Add more providers here as they're configured

            # Zapier MCP - credential-based (URL contains API key)
            if self._settings.credential_encryption_key:
                try:
                    # Find user IDs linked to this context
                    uc_stmt = select(UserContext.user_id).where(
                        UserContext.context_id == context_id
                    )
                    uc_result = await session.execute(uc_stmt)
                    user_ids = [row[0] for row in uc_result.all()]

                    if user_ids:
                        cred_service = CredentialService(self._settings.credential_encryption_key)
                        for uid in user_ids:
                            zapier_url = await cred_service.get_credential(
                                uid, "zapier_mcp_url", session
                            )
                            if zapier_url:
                                try:
                                    client = McpClient(
                                        url=zapier_url,
                                        context_id=context_id,
                                        auth_token=None,
                                        name="Zapier",
                                        auto_reconnect=True,
                                        max_retries=3,
                                        cache_ttl_seconds=300,
                                    )
                                    await client.connect()
                                    clients.append(client)
                                    LOGGER.info(
                                        f"Connected Zapier MCP for context {context_id} "
                                        f"(discovered {len(client.tools)} tools)"
                                    )
                                    session.add(
                                        DebugLog(
                                            trace_id=str(context_id),
                                            event_type="mcp_connect",
                                            event_data={
                                                "provider": "Zapier",
                                                "tools_count": len(client.tools),
                                                "transport": "streamable_http",
                                            },
                                        )
                                    )
                                except Exception as e:
                                    LOGGER.error(
                                        f"Failed to connect Zapier MCP for context "
                                        f"{context_id}: {e}"
                                    )
                                    session.add(
                                        DebugLog(
                                            trace_id=str(context_id),
                                            event_type="mcp_error",
                                            event_data={
                                                "provider": "Zapier",
                                                "error": str(e),
                                            },
                                        )
                                    )
                                break  # One Zapier connection per context
                except Exception as e:
                    LOGGER.error(f"Error loading Zapier credentials for context {context_id}: {e}")
                    session.add(
                        DebugLog(
                            trace_id=str(context_id),
                            event_type="mcp_error",
                            event_data={
                                "provider": "Zapier",
                                "error": f"Credential lookup failed: {e}",
                            },
                        )
                    )

            # Store in cache
            self._pools[context_id] = clients

            if clients:
                LOGGER.info(f"Created {len(clients)} MCP clients for context {context_id}")
            else:
                LOGGER.debug(
                    f"No MCP clients created for context {context_id} "
                    f"(no matching OAuth tokens or providers not configured)"
                )

            return clients

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

        # Remove from pool and timestamps
        del self._pools[context_id]
        self._timestamps.pop(context_id, None)

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
