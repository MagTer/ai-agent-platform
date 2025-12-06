"""MCP tool loader for dynamically registering remote tools."""

from __future__ import annotations

import asyncio
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
        self.parameters = mcp_tool.input_schema # Assuming input_schema directly maps to parameters

    async def run(self, **kwargs: Any) -> Any:
        """Execute the remote MCP tool."""
        LOGGER.info("Executing remote MCP tool '%s' with args: %s", self.name, kwargs)
        return await self._mcp_client.call_tool(self.name, kwargs)


async def load_mcp_tools(settings: Settings, tool_registry: ToolRegistry) -> None:
    """Connect to the MCP server, discover tools, and register them."""
    if not settings.homey_mcp_url or not settings.homey_api_token:
        LOGGER.info("MCP integration disabled (missing URL or API token).")
        return

    mcp_client = McpClient(settings)
    
    try:
        await mcp_client.connect() # This will connect and fetch tools into its cache
        for mcp_tool in mcp_client._tools_cache: # Access the cached tools directly
            wrapper = McpToolWrapper(mcp_client, mcp_tool)
            tool_registry.register(wrapper)
            LOGGER.info("Registered MCP tool: %s", wrapper.name)
    except Exception:
        LOGGER.exception("Failed to load tools from MCP server.")


__all__ = ["load_mcp_tools"]