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
    """Wraps an MCP tool definition for dynamic execution."""

    def __init__(self, mcp_client: McpClient, mcp_tool: McpTool) -> None:
        self._mcp_client = mcp_client
        self._mcp_tool = mcp_tool
        self.name = mcp_tool.name
        self.description = mcp_tool.description
        self.parameters = mcp_tool.input_schema  # Assuming input_schema directly maps to parameters

    async def run(self, **kwargs: Any) -> Any:
        """Execute the remote MCP tool."""
        LOGGER.info("Executing remote MCP tool '%s' with args: %s", self.name, kwargs)
        return await self._mcp_client.call_tool(self.name, kwargs)


async def load_mcp_tools(settings: Settings, tool_registry: ToolRegistry) -> None:
    """Connect to configured MCP servers, discover tools, and register them."""

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
                "token": None,  # Context7 doesn't use a token in this setup yet
            }
        )

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
        mcp_client = McpClient(url, token)

        try:
            await mcp_client.connect()
            for mcp_tool in mcp_client._tools_cache:
                wrapper = McpToolWrapper(mcp_client, mcp_tool)
                # Prefix tool names (e.g. f"{client_name}_{wrapper.name}") to avoid collisions.
                # For now, we register them as is, assuming unique names or last-write-wins.
                tool_registry.register(wrapper)
                LOGGER.info("Registered MCP tool from %s: %s", client_name, wrapper.name)
        except Exception as e:
            LOGGER.error("Failed to load tools from MCP server '%s': %s", client_name, e)


__all__ = ["load_mcp_tools"]
