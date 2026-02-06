"""MCP tool loader for dynamically registering remote tools."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from core.core.config import Settings
from core.mcp.client import McpClient
from core.models.mcp import McpTool
from core.tools.base import Tool
from core.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from core.mcp.client_pool import McpClientPool

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

        # Surface MCP tool annotations (spec 2025-03-26+)
        if mcp_tool.annotations:
            self.mcp_annotations = mcp_tool.annotations.model_dump(by_alias=True)
            # Destructive tools require user confirmation
            if mcp_tool.annotations.destructive_hint is True:
                self.requires_confirmation = True

    async def run(self, **kwargs: Any) -> Any:
        """Execute the remote MCP tool."""
        LOGGER.info(
            "Executing MCP tool '%s' (server: %s) with args: %s",
            self._original_name,
            self._server_name,
            kwargs,
        )

        try:
            # Use original tool name for the actual call
            result = await self._mcp_client.call_tool(self._original_name, kwargs)

            # Handle MCP result format
            if isinstance(result, dict):
                # Prefer structured content (spec 2025-11-25+) when available
                structured = result.get("structuredContent")
                if structured is not None:
                    return structured

                # Fall back to text extraction from content list
                contents = result.get("content", [])
                if contents:
                    first = contents[0]
                    if isinstance(first, dict):
                        text = first.get("text")
                        if text is not None:
                            return text
                    return str(first)
                return str(result)

            # Legacy: handle object-style result
            if hasattr(result, "content"):
                contents = result.content
                if contents and len(contents) > 0:
                    first_content = contents[0]
                    if hasattr(first_content, "text"):
                        return first_content.text
                    return str(first_content)
                return str(result)

            return result

        except Exception as e:
            error_str = str(e).lower()

            # Detect authentication/authorization errors
            if any(
                keyword in error_str
                for keyword in [
                    "401",
                    "unauthorized",
                    "authentication",
                    "auth",
                    "forbidden",
                    "403",
                ]
            ):
                LOGGER.warning(
                    "Authentication error for MCP tool %s (%s): %s",
                    self._original_name,
                    self._server_name,
                    e,
                )

                # Construct authorization message
                provider_lower = self._server_name.lower()

                return (
                    f"[AUTH] **{self._server_name} Authentication Required**\n\n"
                    f"To use {self._server_name} tools, I need permission to access "
                    f"your account.\n\n"
                    f"**To authorize {self._server_name}:**\n"
                    f"I can help you start the authorization process. Just tell me to "
                    f'"authorize {provider_lower}" or "set up {provider_lower}", and '
                    f"I'll provide you with a secure authorization link.\n\n"
                    f"_The authorization is done through your browser and only needs to be "
                    f"completed once._"
                )

            # Re-raise other errors
            raise


# Module-level MCP client pool (singleton)
_client_pool: McpClientPool | None = None


def set_mcp_client_pool(pool: McpClientPool) -> None:
    """Register the global MCP client pool.

    Called during application startup to provide the pool to the loader.

    Args:
        pool: Initialized McpClientPool instance
    """
    global _client_pool
    _client_pool = pool
    LOGGER.info("MCP client pool registered")


def get_mcp_client_pool() -> McpClientPool:
    """Get the global MCP client pool.

    Returns:
        The registered MCP client pool

    Raises:
        RuntimeError: If pool not initialized
    """
    if _client_pool is None:
        raise RuntimeError(
            "MCP client pool not initialized. "
            "Call set_mcp_client_pool() during application startup."
        )
    return _client_pool


async def load_mcp_tools_for_context(
    context_id: UUID,
    tool_registry: ToolRegistry,
    session: AsyncSession,
    settings: Settings,
) -> None:
    """Load MCP tools for a specific context using OAuth tokens.

    This replaces the global load_mcp_tools() function with per-context loading.
    It fetches the context's OAuth tokens and creates MCP clients accordingly.

    Args:
        context_id: Context UUID for isolation
        tool_registry: ToolRegistry to populate with MCP tools
        session: Database session for loading OAuth tokens
        settings: Application settings

    Raises:
        RuntimeError: If MCP client pool not initialized
    """
    # Get the global client pool
    try:
        pool = get_mcp_client_pool()
    except RuntimeError as e:
        LOGGER.warning(f"MCP client pool not available: {e}")
        return

    # Get clients for this context (creates if needed)
    clients = await pool.get_clients(context_id, session)

    if not clients:
        LOGGER.debug(f"No MCP clients available for context {context_id}")
        return

    # Register tools from each client
    total_tools = 0
    for client in clients:
        for mcp_tool in client.tools:
            wrapper = McpToolWrapper(
                mcp_client=client,
                mcp_tool=mcp_tool,
                server_name=client.name,
                prefix_name=True,  # Avoid collisions
            )
            tool_registry.register(wrapper)
            total_tools += 1

        LOGGER.debug(
            f"Registered {len(client.tools)} tools from {client.name} " f"for context {context_id}"
        )

    LOGGER.info(
        f"Loaded {total_tools} MCP tools from {len(clients)} clients " f"for context {context_id}"
    )


async def shutdown_all_mcp_clients() -> None:
    """Shutdown all MCP clients across all contexts.

    Called during application shutdown.
    """
    if _client_pool:
        await _client_pool.shutdown()
        LOGGER.info("All MCP client pools shut down")
    else:
        LOGGER.debug("No MCP client pool to shut down")


async def get_mcp_health() -> dict[str, dict[str, Any]]:
    """Get health status of all MCP connections across all contexts.

    Returns:
        Dict mapping context_id (str) → health info
    """
    if not _client_pool:
        return {}

    return _client_pool.get_health_status()


def get_mcp_stats() -> dict[str, Any]:
    """Get overall MCP client pool statistics.

    Returns:
        Statistics about total contexts, clients, etc.
    """
    if not _client_pool:
        return {
            "total_contexts": 0,
            "total_clients": 0,
            "connected_clients": 0,
            "disconnected_clients": 0,
        }

    return _client_pool.get_stats()


__all__ = [
    "load_mcp_tools_for_context",
    "shutdown_all_mcp_clients",
    "get_mcp_health",
    "get_mcp_stats",
    "set_mcp_client_pool",
    "get_mcp_client_pool",
    "McpToolWrapper",
]
