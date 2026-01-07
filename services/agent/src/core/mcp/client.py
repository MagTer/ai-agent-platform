"""Model Context Protocol client for discovering and executing remote tools."""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack, suppress
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Any
from uuid import UUID

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.shared.exceptions import McpError
from pydantic import AnyUrl

from core.observability.tracing import start_span

from ..models.mcp import McpPrompt, McpResource, McpTool

LOGGER = logging.getLogger(__name__)


class McpConnectionState(Enum):
    """Connection state for MCP client."""

    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    RECONNECTING = auto()
    FAILED = auto()


class McpClient:
    """Connects to an MCP server to discover and execute remote tools.

    Features:
    - Automatic reconnection with exponential backoff
    - Connection state tracking
    - Cache TTL for tools/resources/prompts
    - Support for full MCP protocol (Tools, Resources, Prompts)
    """

    def __init__(
        self,
        url: str,
        auth_token: str | None = None,
        name: str = "mcp",
        auto_reconnect: bool = True,
        max_retries: int = 3,
        cache_ttl_seconds: int = 300,  # 5 minutes
        context_id: UUID | None = None,
        oauth_provider: str | None = None,
    ) -> None:
        self._url = url
        self._name = name
        self._mcp_session: ClientSession | None = None
        self._exit_stack = AsyncExitStack()
        self._headers: dict[str, str] = {}

        # OAuth support
        self._context_id = context_id
        self._oauth_provider = oauth_provider
        self._static_token = auth_token

        # Caches
        self._tools_cache: list[McpTool] = []
        self._resources_cache: list[McpResource] = []
        self._prompts_cache: list[McpPrompt] = []
        self._cache_timestamp: datetime | None = None
        self._cache_ttl = timedelta(seconds=cache_ttl_seconds)

        # Connection management
        self._state = McpConnectionState.DISCONNECTED
        self._auto_reconnect = auto_reconnect
        self._max_retries = max_retries
        self._connect_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        """Server name identifier."""
        return self._name

    @property
    def state(self) -> McpConnectionState:
        """Current connection state."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._state == McpConnectionState.CONNECTED

    @property
    def tools(self) -> list[McpTool]:
        """Cached tools (may be stale)."""
        return self._tools_cache

    @property
    def resources(self) -> list[McpResource]:
        """Cached resources."""
        return self._resources_cache

    @property
    def prompts(self) -> list[McpPrompt]:
        """Cached prompts."""
        return self._prompts_cache

    def is_cache_stale(self) -> bool:
        """Check if cache needs refresh."""
        if not self._cache_timestamp:
            return True
        return datetime.now() - self._cache_timestamp > self._cache_ttl

    async def _get_auth_token(self) -> str | None:
        """Get authentication token from database or fallback to static token.

        Fetches OAuth token from database if context_id and oauth_provider are configured.
        Falls back to static token if OAuth not configured or token fetch fails.

        Returns:
            Authentication token or None
        """
        if self._context_id and self._oauth_provider:
            try:
                from core.providers import get_token_manager

                token_manager = get_token_manager()
                token = await token_manager.get_token(self._oauth_provider, self._context_id)
                if token:
                    LOGGER.debug("Fetched OAuth token for %s provider", self._oauth_provider)
                    return token
                else:
                    LOGGER.warning(
                        "No OAuth token found for %s provider, context %s",
                        self._oauth_provider,
                        self._context_id,
                    )
            except Exception as e:
                LOGGER.warning(
                    "Failed to fetch OAuth token for %s: %s. Falling back to static token.",
                    self._oauth_provider,
                    e,
                )

        # Fallback to static token
        return self._static_token

    async def connect(self) -> None:
        """Connect to the MCP server with retry logic and exponential backoff."""
        async with self._connect_lock:
            if self._state == McpConnectionState.CONNECTED:
                LOGGER.debug("MCP client %s already connected.", self._name)
                return

            self._state = McpConnectionState.CONNECTING

            for attempt in range(1, self._max_retries + 1):
                try:
                    await self._establish_connection()
                    self._state = McpConnectionState.CONNECTED
                    return
                except TimeoutError:
                    LOGGER.warning(
                        "MCP connect attempt %d/%d timed out for %s",
                        attempt,
                        self._max_retries,
                        self._name,
                    )
                    if attempt >= self._max_retries:
                        self._state = McpConnectionState.FAILED
                        raise
                    await asyncio.sleep(2 ** (attempt - 1))
                except Exception as e:
                    LOGGER.warning(
                        "MCP connect attempt %d/%d failed for %s: %s",
                        attempt,
                        self._max_retries,
                        self._name,
                        e,
                    )
                    if attempt >= self._max_retries:
                        self._state = McpConnectionState.FAILED
                        raise
                    # Exponential backoff: 1s, 2s, 4s
                    await asyncio.sleep(2 ** (attempt - 1))

    async def _establish_connection(self) -> None:
        """Internal connection establishment."""
        LOGGER.info("Connecting to MCP server %s at %s", self._name, self._url)

        try:
            # Get current authentication token (from DB or static)
            auth_token = await self._get_auth_token()
            headers = self._headers.copy()

            if auth_token:
                headers["Authorization"] = f"Bearer {auth_token}"
                LOGGER.debug("Using Bearer token for MCP authentication")
            else:
                LOGGER.warning("No authentication token available for MCP %s", self._name)

            streams = await self._exit_stack.enter_async_context(
                sse_client(str(self._url), headers=headers)
            )
            read_stream, write_stream = streams

            self._mcp_session = ClientSession(read_stream, write_stream)
            await asyncio.wait_for(self._mcp_session.initialize(), timeout=60.0)

            LOGGER.info("Connected to MCP server %s at %s", self._name, self._url)

            # Refresh all caches
            await self.refresh_cache()

        except Exception as e:
            LOGGER.error("Failed to connect to MCP %s: %s", self._name, e)
            await self._cleanup_connection()
            raise

    async def _cleanup_connection(self) -> None:
        """Clean up connection resources."""
        self._mcp_session = None
        with suppress(Exception):
            await self._exit_stack.aclose()
        self._exit_stack = AsyncExitStack()

    async def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        await self._cleanup_connection()
        self._state = McpConnectionState.DISCONNECTED
        LOGGER.info("Disconnected from MCP server %s.", self._name)

    async def refresh_cache(self) -> None:
        """Refresh tools, resources, and prompts from server."""
        if not self._mcp_session:
            LOGGER.warning("Cannot refresh cache: not connected")
            return

        with start_span(f"mcp.refresh.{self._name}"):
            # Refresh tools
            try:
                tools_result = await self._mcp_session.list_tools()
                self._tools_cache = [McpTool(**t.model_dump()) for t in tools_result.tools]
                LOGGER.info("Loaded %d tools from %s", len(self._tools_cache), self._name)
            except Exception as e:
                LOGGER.error("Failed to load tools from %s: %s", self._name, e)

            # Refresh resources (optional - not all servers have resources)
            try:
                resources_result = await self._mcp_session.list_resources()
                self._resources_cache = [
                    McpResource(**r.model_dump()) for r in resources_result.resources
                ]
                if self._resources_cache:
                    LOGGER.info(
                        "Loaded %d resources from %s",
                        len(self._resources_cache),
                        self._name,
                    )
            except Exception as e:
                LOGGER.debug("No resources from %s (expected for some servers): %s", self._name, e)

            # Refresh prompts (optional - not all servers have prompts)
            try:
                prompts_result = await self._mcp_session.list_prompts()
                self._prompts_cache = [McpPrompt(**p.model_dump()) for p in prompts_result.prompts]
                if self._prompts_cache:
                    LOGGER.info(
                        "Loaded %d prompts from %s",
                        len(self._prompts_cache),
                        self._name,
                    )
            except Exception as e:
                LOGGER.debug("No prompts from %s (expected for some servers): %s", self._name, e)

            self._cache_timestamp = datetime.now()

    async def get_tools(self) -> list[McpTool]:
        """Fetch tool definitions from the MCP server.

        Returns cached tools if available, otherwise fetches fresh.
        """
        if not self._mcp_session:
            LOGGER.warning("MCP client not connected. Cannot fetch tools.")
            return []

        if self._tools_cache and not self.is_cache_stale():
            return self._tools_cache

        await self.refresh_cache()
        return self._tools_cache

    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
        """Execute a remote tool on the MCP server.

        Auto-reconnects if disconnected and auto_reconnect is enabled.
        """
        # Auto-reconnect if disconnected
        if self._state != McpConnectionState.CONNECTED and self._auto_reconnect:
            LOGGER.info("Reconnecting to %s before tool call", self._name)
            self._state = McpConnectionState.RECONNECTING
            await self.connect()

        if not self._mcp_session:
            raise RuntimeError(f"MCP client {self._name} not connected. Cannot execute tool.")

        with start_span(
            f"mcp.tool_call.{tool_name}",
            attributes={"mcp.server": self._name, "mcp.tool": tool_name},
        ):
            try:
                result = await self._mcp_session.call_tool(tool_name, arguments=args)
                LOGGER.info("Successfully executed MCP tool '%s' on %s.", tool_name, self._name)
                return result
            except McpError as e:
                LOGGER.error(
                    "MCP error calling '%s' on %s: %s",
                    tool_name,
                    self._name,
                    e.error.message if hasattr(e, "error") else str(e),
                )
                raise
            except Exception as e:
                LOGGER.error(
                    "Failed to execute MCP tool '%s' on %s: %s",
                    tool_name,
                    self._name,
                    e,
                )
                # Mark as disconnected if connection error
                error_str = str(e).lower()
                if any(kw in error_str for kw in ("connection", "closed", "eof", "reset")):
                    self._state = McpConnectionState.DISCONNECTED
                raise

    async def read_resource(self, uri: str) -> Any:
        """Read a resource from the MCP server."""
        if not self._mcp_session:
            raise RuntimeError(f"MCP client {self._name} not connected.")

        with start_span(
            "mcp.resource.read",
            attributes={"mcp.server": self._name, "mcp.resource_uri": uri},
        ):
            result = await self._mcp_session.read_resource(AnyUrl(uri))
            return result.contents

    async def get_prompt(self, name: str, arguments: dict[str, str] | None = None) -> Any:
        """Get a prompt from the MCP server."""
        if not self._mcp_session:
            raise RuntimeError(f"MCP client {self._name} not connected.")

        with start_span(
            "mcp.prompt.get",
            attributes={"mcp.server": self._name, "mcp.prompt": name},
        ):
            result = await self._mcp_session.get_prompt(name, arguments or {})
            return result

    async def ping(self) -> bool:
        """Check if the connection is healthy.

        Returns True if connected and responsive, False otherwise.
        """
        if not self._mcp_session:
            return False
        try:
            # list_tools is a lightweight call to verify connection
            await asyncio.wait_for(self._mcp_session.list_tools(), timeout=5.0)
            return True
        except Exception:
            self._state = McpConnectionState.DISCONNECTED
            return False


__all__ = ["McpClient", "McpConnectionState"]
