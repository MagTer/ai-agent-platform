"""Tests for OAuth 2.1 Protected Resource Metadata discovery (RFC 9728)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.mcp.oauth_discovery import (
    ProtectedResourceMetadata,
    discover_protected_resource_metadata,
)


class TestProtectedResourceMetadata:
    """Tests for the metadata model."""

    def test_parse_minimal(self) -> None:
        """Minimal metadata should parse correctly."""
        meta = ProtectedResourceMetadata(resource="https://mcp.example.com")
        assert meta.resource == "https://mcp.example.com"
        assert meta.authorization_servers == []
        assert meta.scopes_supported == []

    def test_parse_full(self) -> None:
        """Full metadata should parse correctly."""
        meta = ProtectedResourceMetadata(
            resource="https://mcp.example.com",
            authorization_servers=["https://auth.example.com"],
            scopes_supported=["read", "write"],
        )
        assert len(meta.authorization_servers) == 1
        assert meta.scopes_supported == ["read", "write"]


class TestDiscoverProtectedResourceMetadata:
    """Tests for the discovery function."""

    @pytest.mark.asyncio
    async def test_successful_discovery(self) -> None:
        """Successful 200 response should return parsed metadata."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "resource": "https://mcp.example.com",
            "authorization_servers": ["https://auth.example.com"],
            "scopes_supported": ["read", "write"],
        }

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        with patch("core.mcp.oauth_discovery.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await discover_protected_resource_metadata("https://mcp.example.com/sse")

        assert result is not None
        assert result.resource == "https://mcp.example.com"
        assert result.authorization_servers == ["https://auth.example.com"]

    @pytest.mark.asyncio
    async def test_server_not_supporting_discovery(self) -> None:
        """404 response should return None."""
        mock_response = AsyncMock()
        mock_response.status_code = 404

        with patch("core.mcp.oauth_discovery.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await discover_protected_resource_metadata("https://mcp.example.com/sse")

        assert result is None

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self) -> None:
        """Timeout should return None gracefully."""
        with patch("core.mcp.oauth_discovery.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.TimeoutException("timed out")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await discover_protected_resource_metadata("https://mcp.example.com/sse")

        assert result is None

    @pytest.mark.asyncio
    async def test_connection_error_returns_none(self) -> None:
        """Connection error should return None gracefully."""
        with patch("core.mcp.oauth_discovery.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ConnectError("refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await discover_protected_resource_metadata("https://mcp.example.com/sse")

        assert result is None

    @pytest.mark.asyncio
    async def test_well_known_url_derivation(self) -> None:
        """Well-known URL should be derived from server origin."""
        mock_response = AsyncMock()
        mock_response.status_code = 404

        with patch("core.mcp.oauth_discovery.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await discover_protected_resource_metadata("https://mcp.example.com:9090/some/path")

            # Verify the well-known URL was constructed correctly
            call_args = mock_client.get.call_args
            url = call_args[0][0]
            assert url == "https://mcp.example.com:9090/.well-known/oauth-protected-resource"
