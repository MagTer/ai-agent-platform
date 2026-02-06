"""OAuth 2.1 Protected Resource Metadata discovery (RFC 9728).

Implements discovery of OAuth authorization servers for MCP servers that
require OAuth authentication. This is informational -- it does not change
the existing auth flow, but enables interoperability discovery.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field

LOGGER = logging.getLogger(__name__)

# Timeout for discovery requests (seconds)
_DISCOVERY_TIMEOUT = 5.0


class ProtectedResourceMetadata(BaseModel):
    """RFC 9728 Protected Resource Metadata."""

    resource: str = Field(..., description="Protected resource identifier (URL).")
    authorization_servers: list[str] = Field(
        default_factory=list,
        alias="authorization_servers",
        description="List of authorization server URLs.",
    )
    scopes_supported: list[str] = Field(
        default_factory=list,
        alias="scopes_supported",
        description="OAuth scopes supported by the resource.",
    )

    model_config = {"populate_by_name": True}


async def discover_protected_resource_metadata(
    server_url: str,
) -> ProtectedResourceMetadata | None:
    """Discover OAuth metadata for an MCP server (RFC 9728).

    Fetches ``/.well-known/oauth-protected-resource`` from the server's origin.
    Returns *None* if the server does not support discovery (404, timeout, etc.).

    Args:
        server_url: The MCP server URL.

    Returns:
        Parsed metadata or None if discovery is not supported.
    """
    try:
        # Derive the well-known URL from the server origin
        parsed = httpx.URL(server_url)
        well_known_url = f"{parsed.scheme}://{parsed.host}"
        if parsed.port and parsed.port not in (80, 443):
            well_known_url += f":{parsed.port}"
        well_known_url += "/.well-known/oauth-protected-resource"

        async with httpx.AsyncClient() as client:
            response = await client.get(well_known_url, timeout=_DISCOVERY_TIMEOUT)

        if response.status_code == 200:
            data: dict[str, Any] = response.json()
            metadata = ProtectedResourceMetadata(**data)
            LOGGER.info(
                "Discovered OAuth metadata for %s: %d authorization server(s), scopes=%s",
                server_url,
                len(metadata.authorization_servers),
                metadata.scopes_supported,
            )
            return metadata

        LOGGER.debug(
            "OAuth discovery returned %d for %s (not supported)",
            response.status_code,
            server_url,
        )
        return None

    except httpx.TimeoutException:
        LOGGER.debug("OAuth discovery timed out for %s", server_url)
        return None
    except Exception as e:
        LOGGER.debug("OAuth discovery failed for %s: %s", server_url, e)
        return None


__all__ = ["ProtectedResourceMetadata", "discover_protected_resource_metadata"]
