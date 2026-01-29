from __future__ import annotations

import logging
import os
import shutil
from contextlib import AsyncExitStack
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from core.core.config import get_settings
from core.models.mcp import McpTool
from core.observability.tracing import start_span

LOGGER = logging.getLogger(__name__)


class Context7Client:
    """
    Client for the Context7 MCP Server using Stdio transport.
    Spawns `npx` to run the official Node.js server.
    """

    def __init__(self) -> None:
        self._exit_stack = AsyncExitStack()
        self._session: ClientSession | None = None
        self._settings = get_settings()

    @property
    def is_connected(self) -> bool:
        return self._session is not None

    async def connect(self) -> None:
        """Helper to start the stdio connection."""
        if self.is_connected:
            return

        api_key = self._settings.context7_api_key
        if not api_key:
            LOGGER.warning("Context7 API Key not configured. Tools will likely fail.")

        # Check for npx availability
        npx_path = shutil.which("npx")
        if not npx_path:
            raise RuntimeError("npx not found in PATH. Cannot start Context7 server.")

        # Prepare server parameters
        # npx -y @upstash/context7-mcp
        server_params = StdioServerParameters(
            command="npx",
            args=["-y", "@upstash/context7-mcp"],
            env={
                **os.environ,
                "CONTEXT7_API_KEY": api_key or "",
                "PATH": os.environ.get("PATH", ""),
            },
        )

        LOGGER.info("Starting Context7 MCP server via npx...")
        try:
            stdio_transport = await self._exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            read_stream, write_stream = stdio_transport
            self._session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            if not self._session:
                raise RuntimeError("Failed to create MCP ClientSession")

            await self._session.initialize()
            LOGGER.info("Connected to Context7 MCP server.")
        except Exception as e:
            LOGGER.error("Failed to connect to Context7 MCP server: %s", e)
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        self._session = None
        await self._exit_stack.aclose()

    async def list_tools(self) -> list[McpTool]:
        if not self._session:
            await self.connect()

        if not self._session:
            return []

        result = await self._session.list_tools()
        return [McpTool(**tool.model_dump()) for tool in result.tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if not self._session:
            await self.connect()

        if not self._session:
            raise RuntimeError("Context7 Client not connected.")

        with start_span(f"context7.tool.{name}"):
            LOGGER.info("Calling Context7 tool: %s", name)
            return await self._session.call_tool(name, arguments=arguments)


_CLIENT_INSTANCE: Context7Client | None = None


async def get_context7_client() -> Context7Client:
    global _CLIENT_INSTANCE
    if _CLIENT_INSTANCE is None:
        _CLIENT_INSTANCE = Context7Client()
        # Lazily connect
    return _CLIENT_INSTANCE
