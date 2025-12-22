"""Model Context Protocol client for discovering and executing remote tools."""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client

from core.observability.tracing import start_span

from ..models.mcp import McpTool

LOGGER = logging.getLogger(__name__)


class McpClient:
    """Connects to an MCP server to discover and execute remote tools."""

    def __init__(self, url: str, auth_token: str | None = None) -> None:
        self._url = url
        self._mcp_session: ClientSession | None = None
        self._exit_stack = AsyncExitStack()
        self._headers: dict[str, str] = {}
        self._tools_cache: list[McpTool] = []

        if auth_token:
            self._headers["Authorization"] = f"Bearer {auth_token}"

    async def connect(self) -> None:
        """Connects to the MCP server and establishes the session."""
        if self._mcp_session:
            LOGGER.info("MCP client already connected.")
            return

        LOGGER.info("Attempting to connect to MCP server: %s", self._url)

        try:
            streams = await self._exit_stack.enter_async_context(
                sse_client(str(self._url), headers=self._headers)
            )
            read_stream, write_stream = streams

            # Now instantiate ClientSession with the streams
            self._mcp_session = ClientSession(read_stream, write_stream)
            try:
                await asyncio.wait_for(
                    self._mcp_session.initialize(), timeout=60.0
                )  # Initialize the ClientSession
            except TimeoutError:
                LOGGER.error("MCP session initialization timed out for %s", self._url)
                raise

            LOGGER.info("Successfully connected to MCP server at %s", self._url)

            # Fetch tools immediately after connecting
            self._tools_cache = await self.get_tools()
            LOGGER.info(
                "Discovered %d tools from MCP server at %s.",
                len(self._tools_cache),
                self._url,
            )
        except Exception as e:
            LOGGER.error("Failed to connect or establish MCP session with %s: %s", self._url, e)
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        """Disconnects the MCP client session."""
        if self._mcp_session:
            # The session itself doesn't strictly need closing if we close the transport,
            # but good practice if the SDK supports it.
            # However, mcp.client.session.ClientSession is an async context manager?
            # Checking SDK usage, usually closing streams is enough.
            self._mcp_session = None

        await self._exit_stack.aclose()
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
            result = await self._mcp_session.list_tools()
            self._tools_cache = [McpTool(**tool.model_dump()) for tool in result.tools]
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
