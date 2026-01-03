"""Centralized MCP connection manager."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from ..models.mcp import McpPrompt, McpResource, McpTool
from .client import McpClient, McpConnectionState

LOGGER = logging.getLogger(__name__)


@dataclass
class McpServerConfig:
    """Configuration for a single MCP server."""

    name: str
    url: str
    auth_token: str | None = None
    prefix_tools: bool = True  # Prefix tool names with server name
    auto_reconnect: bool = True
    reconnect_interval: float = 30.0  # seconds
    health_check_interval: float = 60.0  # seconds
    max_retries: int = 3
    cache_ttl_seconds: int = 300


class McpManager:
    """Manages multiple MCP server connections with unified interface.

    Features:
    - Coordinate multiple MCP servers
    - Automatic health monitoring and reconnection
    - Unified tool lookup across all servers
    - Graceful degradation when servers are unavailable
    """

    def __init__(self) -> None:
        self._clients: dict[str, McpClient] = {}
        self._configs: dict[str, McpServerConfig] = {}
        self._health_task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def servers(self) -> list[str]:
        """List of registered server names."""
        return list(self._clients.keys())

    @property
    def connected_servers(self) -> list[str]:
        """List of currently connected server names."""
        return [
            name
            for name, client in self._clients.items()
            if client.state == McpConnectionState.CONNECTED
        ]

    async def add_server(self, config: McpServerConfig) -> None:
        """Register and connect to an MCP server.

        Args:
            config: Server configuration

        Raises:
            Exception: If connection fails and auto_reconnect is False
        """
        client = McpClient(
            url=config.url,
            auth_token=config.auth_token,
            name=config.name,
            auto_reconnect=config.auto_reconnect,
            max_retries=config.max_retries,
            cache_ttl_seconds=config.cache_ttl_seconds,
        )
        self._clients[config.name] = client
        self._configs[config.name] = config

        try:
            await client.connect()
            LOGGER.info(
                "Connected to MCP server: %s (%d tools)",
                config.name,
                len(client.tools),
            )
        except Exception as e:
            LOGGER.error("Failed to connect to %s: %s", config.name, e)
            if not config.auto_reconnect:
                del self._clients[config.name]
                del self._configs[config.name]
                raise
            # Keep the client registered for later reconnection attempts
            LOGGER.info("Server %s will be retried during health monitoring", config.name)

    async def remove_server(self, name: str) -> None:
        """Remove and disconnect a server."""
        if name in self._clients:
            await self._clients[name].disconnect()
            del self._clients[name]
            del self._configs[name]
            LOGGER.info("Removed MCP server: %s", name)

    async def start(self) -> None:
        """Start the manager and health monitoring."""
        self._running = True
        self._health_task = asyncio.create_task(
            self._health_monitor_loop(), name="mcp_health_monitor"
        )
        LOGGER.info("MCP Manager started with %d servers", len(self._clients))

    async def stop(self) -> None:
        """Stop all clients and health monitoring."""
        self._running = False

        if self._health_task:
            self._health_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._health_task
            self._health_task = None

        # Disconnect all clients
        for client in self._clients.values():
            with suppress(Exception):
                await client.disconnect()

        LOGGER.info("MCP Manager stopped")

    def get_all_tools(self) -> list[tuple[str, McpTool]]:
        """Return all tools from all connected servers.

        Returns:
            List of (prefixed_name, McpTool) tuples
        """
        tools: list[tuple[str, McpTool]] = []
        for name, client in self._clients.items():
            if client.state != McpConnectionState.CONNECTED:
                continue

            config = self._configs[name]
            for tool in client.tools:
                if config.prefix_tools:
                    prefixed_name = f"{name}_{tool.name}"
                else:
                    prefixed_name = tool.name
                tools.append((prefixed_name, tool))
        return tools

    def get_all_resources(self) -> list[tuple[str, McpResource]]:
        """Return all resources from all connected servers."""
        resources: list[tuple[str, McpResource]] = []
        for name, client in self._clients.items():
            if client.state != McpConnectionState.CONNECTED:
                continue

            for resource in client.resources:
                resources.append((name, resource))
        return resources

    def get_all_prompts(self) -> list[tuple[str, McpPrompt]]:
        """Return all prompts from all connected servers."""
        prompts: list[tuple[str, McpPrompt]] = []
        for name, client in self._clients.items():
            if client.state != McpConnectionState.CONNECTED:
                continue

            for prompt in client.prompts:
                prompts.append((name, prompt))
        return prompts

    async def call_tool(self, prefixed_name: str, args: dict[str, Any]) -> Any:
        """Execute a tool, routing to the correct server.

        Args:
            prefixed_name: Tool name (possibly prefixed with server name)
            args: Tool arguments

        Returns:
            Tool execution result

        Raises:
            ValueError: If tool is not found
        """
        # Try to find the tool by prefix matching
        for server_name, client in self._clients.items():
            if client.state != McpConnectionState.CONNECTED:
                continue

            config = self._configs[server_name]
            if config.prefix_tools:
                prefix = f"{server_name}_"
                if prefixed_name.startswith(prefix):
                    actual_name = prefixed_name[len(prefix) :]
                    return await client.call_tool(actual_name, args)
            else:
                # No prefix - check if tool exists directly
                if any(t.name == prefixed_name for t in client.tools):
                    return await client.call_tool(prefixed_name, args)

        raise ValueError(f"MCP tool not found: {prefixed_name}")

    async def read_resource(self, server_name: str, uri: str) -> Any:
        """Read a resource from a specific server."""
        if server_name not in self._clients:
            raise ValueError(f"Unknown MCP server: {server_name}")

        client = self._clients[server_name]
        return await client.read_resource(uri)

    async def get_prompt(
        self, server_name: str, prompt_name: str, arguments: dict[str, str] | None = None
    ) -> Any:
        """Get a prompt from a specific server."""
        if server_name not in self._clients:
            raise ValueError(f"Unknown MCP server: {server_name}")

        client = self._clients[server_name]
        return await client.get_prompt(prompt_name, arguments)

    async def get_health(self) -> dict[str, dict[str, Any]]:
        """Get health status of all servers."""
        health: dict[str, dict[str, Any]] = {}

        for name, client in self._clients.items():
            is_healthy = await client.ping()
            health[name] = {
                "connected": client.is_connected,
                "healthy": is_healthy,
                "state": client.state.name,
                "tools_count": len(client.tools),
                "resources_count": len(client.resources),
                "prompts_count": len(client.prompts),
                "cache_stale": client.is_cache_stale(),
            }

        return health

    async def _health_monitor_loop(self) -> None:
        """Periodically check and restore connections."""
        while self._running:
            for name, client in self._clients.items():
                config = self._configs[name]

                if client.state == McpConnectionState.DISCONNECTED:
                    if config.auto_reconnect:
                        LOGGER.info("Attempting reconnect to MCP server: %s", name)
                        try:
                            await client.connect()
                            LOGGER.info(
                                "Reconnected to MCP server: %s (%d tools)",
                                name,
                                len(client.tools),
                            )
                        except Exception as e:
                            LOGGER.warning("Reconnect failed for %s: %s (will retry)", name, e)

                elif client.state == McpConnectionState.CONNECTED:
                    # Check if cache needs refresh
                    if client.is_cache_stale():
                        try:
                            await client.refresh_cache()
                            LOGGER.debug("Refreshed cache for MCP server: %s", name)
                        except Exception as e:
                            LOGGER.warning("Cache refresh failed for %s: %s", name, e)

            # Wait before next health check
            await asyncio.sleep(30)


# Global manager instance
_manager: McpManager | None = None


def get_mcp_manager() -> McpManager:
    """Get the global MCP manager instance."""
    global _manager
    if _manager is None:
        _manager = McpManager()
    return _manager


async def shutdown_mcp_manager() -> None:
    """Shutdown the global MCP manager."""
    global _manager
    if _manager is not None:
        await _manager.stop()
        _manager = None


__all__ = [
    "McpManager",
    "McpServerConfig",
    "get_mcp_manager",
    "shutdown_mcp_manager",
]
