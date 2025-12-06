"""Model Context Protocol client for discovering and executing remote tools."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.session_group import StreamableHttpParameters
from mcp.client.streamable_http import streamablehttp_client

from core.core.config import Settings
from ..models.mcp import McpTool
from core.observability.tracing import start_span

LOGGER = logging.getLogger(__name__)


class McpClient:
    """Connects to an MCP server to discover and execute remote tools."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._mcp_session: ClientSession | None = None
        self._headers: dict[str, str] = {}
        self._tools_cache: list[McpTool] = []

        if settings.homey_api_token:
            self._headers["Authorization"] = f"Bearer {settings.homey_api_token}"

    async def connect(self) -> None:
        """Connects to the MCP server and establishes the session."""
        if self._mcp_session:
            LOGGER.info("MCP client already connected.")
            return

        LOGGER.info("Attempting to connect to MCP server: %s", self._settings.homey_mcp_url)

        server_params = StreamableHttpParameters(
            url=str(self._settings.homey_mcp_url), headers=self._headers
        )

        try:
            # streamable_http_client is an async context manager
            self._mcp_session = await streamable_http_client(server_params).__aenter__()
            LOGGER.info("Successfully connected to MCP server.")
            # Fetch tools immediately after connecting
            self._tools_cache = await self.get_tools()
            LOGGER.info("Discovered %d tools from MCP server.", len(self._tools_cache))
        except Exception as e:
            LOGGER.error("Failed to connect or establish MCP session: %s", e)
            self._mcp_session = None
            raise

    async def disconnect(self) -> None:
        """Disconnects the MCP client session."""
        if self._mcp_session:
            await self._mcp_session.__aexit__(None, None, None)
            self._mcp_session = None
            LOGGER.info("Disconnected from MCP server.")

    async def get_tools(self) -> list[McpTool]:
        """Fetch tool definitions from the MCP server."""
        if not self._mcp_session:
            LOGGER.warning("MCP client not connected. Cannot fetch tools.")
            return []
        
        # If tools are already cached, return them.
        # In a full SSE implementation, this cache would be updated by tool_manifest events.
        if self._tools_cache:
            return self._tools_cache

        try:
            remote_tools_raw = await self._mcp_session.list_tools()
            self._tools_cache = [McpTool(**tool.model_dump()) for tool in remote_tools_raw]
            return self._tools_cache
        except Exception as e:
            LOGGER.error("Failed to fetch tools from MCP server: %s", e)
            return []

    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
        """Execute a remote tool on the MCP server."""
        if not self._mcp_session:
            raise RuntimeError("MCP client not connected. Cannot execute tool.")

        with start_span(f"mcp.tool_call.{tool_name}"):
            try:
                result = await self._mcp_session.call_tool(tool_name, **args)
                LOGGER.info("Successfully executed remote MCP tool '%s'.", tool_name)
                return result
            except Exception as e:
                LOGGER.error("Failed to execute remote MCP tool '%s': %s", tool_name, e)
                raise
