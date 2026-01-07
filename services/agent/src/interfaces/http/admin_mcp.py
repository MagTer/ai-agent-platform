"""Admin endpoints for MCP client management."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from core.tools.mcp_loader import get_mcp_client_pool, get_mcp_health, get_mcp_stats

from .admin_auth import verify_admin_api_key

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/mcp",
    tags=["admin", "mcp"],
    dependencies=[Depends(verify_admin_api_key)],
)


class MCPHealthResponse(BaseModel):
    """MCP client health status response."""

    health: dict[str, Any]


class MCPStatsResponse(BaseModel):
    """MCP client pool statistics response."""

    stats: dict[str, Any]


class DisconnectResponse(BaseModel):
    """Response after disconnecting MCP clients."""

    success: bool
    message: str
    context_id: UUID


@router.get("/health", response_model=MCPHealthResponse)
async def get_mcp_client_health() -> MCPHealthResponse:
    """Get health status of all MCP client pools across all contexts.

    Returns:
        Health status for each context, including:
        - Client connection status
        - Number of tools/resources/prompts
        - Cache staleness

    Security:
        Requires admin API key via X-API-Key header

    Example response:
        {
            "health": {
                "abc-123-def-456": {
                    "clients": [
                        {
                            "name": "Homey",
                            "connected": true,
                            "state": "CONNECTED",
                            "tools_count": 15,
                            "resources_count": 0,
                            "prompts_count": 0,
                            "cache_stale": false
                        }
                    ],
                    "total_clients": 1
                }
            }
        }
    """
    health = await get_mcp_health()
    return MCPHealthResponse(health=health)


@router.get("/stats", response_model=MCPStatsResponse)
async def get_mcp_client_stats() -> MCPStatsResponse:
    """Get overall MCP client pool statistics.

    Returns:
        Statistics including:
        - Total number of contexts
        - Total number of clients
        - Number of connected clients
        - Number of disconnected clients

    Security:
        Requires admin API key via X-API-Key header

    Example response:
        {
            "stats": {
                "total_contexts": 3,
                "total_clients": 5,
                "connected_clients": 4,
                "disconnected_clients": 1
            }
        }
    """
    stats = get_mcp_stats()
    return MCPStatsResponse(stats=stats)


@router.post("/disconnect/{context_id}", response_model=DisconnectResponse)
async def disconnect_mcp_clients(context_id: UUID) -> DisconnectResponse:
    """Force disconnect all MCP clients for a context.

    This will:
    1. Disconnect all active MCP clients for the context
    2. Remove them from the client pool cache
    3. Next request will create fresh connections

    Useful for:
    - Forcing re-authentication after OAuth token changes
    - Recovering from stuck connections
    - Resetting MCP client state

    Args:
        context_id: Context UUID

    Returns:
        Success confirmation

    Raises:
        HTTPException: 503 if MCP client pool not initialized

    Security:
        Requires admin API key via X-API-Key header
    """
    try:
        pool = get_mcp_client_pool()
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"MCP client pool not initialized: {e}",
        ) from e

    await pool.disconnect_context(context_id)

    LOGGER.info(f"Admin disconnected MCP clients for context {context_id}")

    return DisconnectResponse(
        success=True,
        message=f"Disconnected all MCP clients for context {context_id}",
        context_id=context_id,
    )


__all__ = ["router"]
