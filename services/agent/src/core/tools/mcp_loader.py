"""MCP tool loader for dynamically registering remote tools."""

from __future__ import annotations

import logging
from typing import Any

from core.core.config import Settings

from ..mcp.client import McpClient
from ..models.mcp import McpTool
from .base import Tool
from .registry import ToolRegistry

LOGGER = logging.getLogger(__name__)


class McpToolWrapper(Tool):
    """Wraps an MCP tool definition for dynamic execution.

    Prefixes tool names with server name to avoid collisions between
    tools from different MCP servers.
    """

    def __init__(
        self,
        mcp_client: McpClient,
        mcp_tool: McpTool,
        server_name: str,
        prefix_name: bool = True,
    ) -> None:
        self._mcp_client = mcp_client
        self._mcp_tool = mcp_tool
        self._server_name = server_name
        self._original_name = mcp_tool.name

        # Prefix tool names to avoid collisions
        if prefix_name:
            self.name = f"{server_name}_{mcp_tool.name}"
        else:
            self.name = mcp_tool.name

        self.description = mcp_tool.description or f"MCP tool from {server_name}"
        self.parameters = mcp_tool.input_schema
        self.category = "mcp"  # Mark as MCP tool for filtering

    async def run(self, **kwargs: Any) -> Any:
        """Execute the remote MCP tool."""
        LOGGER.info(
            "Executing MCP tool '%s' (server: %s) with args: %s",
            self._original_name,
            self._server_name,
            kwargs,
        )
        # Use original tool name for the actual call
        result = await self._mcp_client.call_tool(self._original_name, kwargs)

        # Handle MCP result format - extract content if available
        if hasattr(result, "content"):
            # MCP returns a CallToolResult with content list
            contents = result.content
            if contents and len(contents) > 0:
                first_content = contents[0]
                if hasattr(first_content, "text"):
                    return first_content.text
                return str(first_content)
            return str(result)
        return result


# Store active clients for lifecycle management
_active_clients: list[McpClient] = []


async def load_mcp_tools(settings: Settings, tool_registry: ToolRegistry) -> None:
    """Connect to configured MCP servers, discover tools, and register them.

    This function is called during application startup to discover and register
    tools from all configured MCP servers. It handles connection failures
    gracefully, allowing the application to start even if some MCP servers
    are unavailable.
    """
    global _active_clients

    mcp_configs: list[dict[str, str | None]] = []

    # Homey MCP
    if settings.homey_mcp_url and settings.homey_api_token:
        mcp_configs.append(
            {
                "name": "Homey",
                "url": str(settings.homey_mcp_url),
                "token": settings.homey_api_token,
            }
        )
    else:
        LOGGER.info("Homey MCP integration disabled (missing URL or API token).")

    # Context7 MCP
    if settings.context7_mcp_url:
        mcp_configs.append(
            {
                "name": "Context7",
                "url": str(settings.context7_mcp_url),
                "token": settings.context7_api_key,  # Use API key if configured
            }
        )
    else:
        LOGGER.info("Context7 MCP integration disabled (missing URL).")

    if not mcp_configs:
        LOGGER.info("No MCP servers configured.")
        return

    for config in mcp_configs:
        client_name = config.get("name")
        url = config.get("url")
        token = config.get("token")

        if not url or not client_name:
            LOGGER.warning("Skipping MCP config with missing name or URL: %s", config)
            continue

        LOGGER.info("Initializing MCP client for %s at %s...", client_name, url)
        mcp_client = McpClient(
            url=url,
            auth_token=token,
            name=client_name,
            auto_reconnect=True,
            max_retries=3,
            cache_ttl_seconds=300,  # 5 minute cache
        )

        try:
            await mcp_client.connect()
            _active_clients.append(mcp_client)

            # Register all discovered tools
            for mcp_tool in mcp_client.tools:
                wrapper = McpToolWrapper(
                    mcp_client=mcp_client,
                    mcp_tool=mcp_tool,
                    server_name=client_name,
                    prefix_name=True,  # Avoid collisions
                )
                tool_registry.register(wrapper)
                LOGGER.info("Registered MCP tool: %s", wrapper.name)

            LOGGER.info(
                "Successfully loaded %d tools from MCP server '%s'",
                len(mcp_client.tools),
                client_name,
            )

        except Exception as e:
            LOGGER.error(
                "Failed to load tools from MCP server '%s': %s. "
                "The agent will continue without these tools.",
                client_name,
                e,
            )
            # Continue with other servers even if one fails


async def shutdown_mcp_clients() -> None:
    """Disconnect all active MCP clients.

    Should be called during application shutdown.
    """
    global _active_clients

    for client in _active_clients:
        try:
            await client.disconnect()
        except Exception as e:
            LOGGER.warning("Error disconnecting MCP client %s: %s", client.name, e)

    _active_clients.clear()
    LOGGER.info("All MCP clients disconnected.")


async def get_mcp_health() -> dict[str, dict[str, Any]]:
    """Get health status of all MCP connections.

    Returns a dict mapping server name to health info.
    """
    health: dict[str, dict[str, Any]] = {}

    for client in _active_clients:
        is_healthy = await client.ping()
        health[client.name] = {
            "connected": client.is_connected,
            "healthy": is_healthy,
            "state": client.state.name,
            "tools_count": len(client.tools),
            "resources_count": len(client.resources),
            "prompts_count": len(client.prompts),
            "cache_stale": client.is_cache_stale(),
        }

    return health


__all__ = ["load_mcp_tools", "shutdown_mcp_clients", "get_mcp_health", "McpToolWrapper"]
