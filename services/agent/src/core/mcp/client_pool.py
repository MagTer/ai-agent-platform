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
from collections import defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.core.config import Settings
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

                # Homey MCP
                if provider == "homey" and self._settings.homey_mcp_url:
                    try:
                        client = McpClient(
                            url=str(self._settings.homey_mcp_url),
                            context_id=context_id,
                            oauth_provider="homey",
                            name="Homey",
                            auto_reconnect=True,
                            max_retries=3,
                            cache_ttl_seconds=300,  # 5 minute cache
                        )
                        await client.connect()
                        clients.append(client)
                        LOGGER.info(
                            f"Connected Homey MCP for context {context_id} "
                            f"(discovered {len(client.tools)} tools)"
                        )
                    except Exception as e:
                        LOGGER.error(f"Failed to connect Homey MCP for context {context_id}: {e}")

                # Context7 MCP (future provider)
                elif provider == "context7" and self._settings.context7_mcp_url:
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
                    except Exception as e:
                        LOGGER.error(
                            f"Failed to connect Context7 MCP for context {context_id}: {e}"
                        )

                # Add more providers here as they're configured

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

        # Remove from pool
        del self._pools[context_id]

        if disconnect_errors:
            LOGGER.warning(
                f"Disconnected {len(clients)} clients for context {context_id} "
                f"with {len(disconnect_errors)} errors"
            )
        else:
            LOGGER.info(
                f"Successfully disconnected {len(clients)} clients for context {context_id}"
            )

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
