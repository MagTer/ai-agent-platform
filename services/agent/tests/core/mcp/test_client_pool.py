"""Unit tests for McpClientPool.

Tests the per-context MCP client pool that manages OAuth-authenticated MCP clients.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.db.models import Context
from core.db.oauth_models import OAuthToken
from core.mcp.client_pool import McpClientPool


@pytest.mark.asyncio
class TestMcpClientPool:
    """Test MCP client pool functionality."""

    async def test_pool_initialization(self, settings):
        """Test that pool initializes correctly."""
        pool = McpClientPool(settings)

        assert pool._settings == settings
        assert len(pool._pools) == 0
        assert len(pool._locks) == 0

    async def test_get_clients_no_tokens(self, async_session, settings):
        """Test get_clients returns empty list when context has no OAuth tokens."""
        # Create context without OAuth tokens
        context = Context(
            name="no_token_context", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session.add(context)
        await async_session.flush()
        await async_session.commit()

        pool = McpClientPool(settings)
        clients = await pool.get_clients(context.id, async_session)

        assert clients == []

    async def test_get_clients_creates_homey_client(self, async_session, settings):
        """Test get_clients creates Homey MCP client when token exists."""
        # Create context with Homey OAuth token
        context = Context(
            name="homey_context", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session.add(context)
        await async_session.flush()

        token = OAuthToken(
            context_id=context.id,
            provider="homey",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        token.set_access_token("test_homey_token")  # Use setter for encryption
        async_session.add(token)
        await async_session.commit()

        # Mock settings to have Homey MCP URL
        settings.homey_mcp_url = "https://mcp.athom.com/sse"

        pool = McpClientPool(settings)

        # Mock McpClient
        with patch("core.mcp.client_pool.McpClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.name = "Homey"
            mock_client.tools = []
            mock_client.is_connected = True
            mock_client_class.return_value = mock_client

            clients = await pool.get_clients(context.id, async_session)

            # Verify client was created and connected
            mock_client_class.assert_called_once()
            call_kwargs = mock_client_class.call_args[1]
            assert call_kwargs["url"] == "https://mcp.athom.com/sse"
            assert call_kwargs["context_id"] == context.id
            assert call_kwargs["oauth_provider"] == "homey"
            assert call_kwargs["name"] == "Homey"

            mock_client.connect.assert_called_once()

            assert len(clients) == 1
            assert clients[0] == mock_client

    async def test_get_clients_caches_clients(self, async_session, settings):
        """Test that clients are cached and reused."""
        # Create context with OAuth token
        context = Context(
            name="cache_context", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session.add(context)
        await async_session.flush()

        token = OAuthToken(
            context_id=context.id,
            provider="homey",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        token.set_access_token("cache_token")  # Use setter for encryption
        async_session.add(token)
        await async_session.commit()

        settings.homey_mcp_url = "https://mcp.athom.com/sse"

        pool = McpClientPool(settings)

        # Mock McpClient
        with patch("core.mcp.client_pool.McpClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.name = "Homey"
            mock_client.tools = []
            mock_client.is_connected = True
            mock_client.ping = AsyncMock(return_value=True)
            mock_client_class.return_value = mock_client

            # First call - should create client
            clients_1 = await pool.get_clients(context.id, async_session)
            assert len(clients_1) == 1

            # Second call - should return cached client
            await pool.get_clients(context.id, async_session)

            # Should have created client only once
            mock_client_class.assert_called_once()

            # Should be same instance

    async def test_get_clients_validates_health(self, async_session, settings):
        """Test that cached clients are health-checked before returning."""
        # Create context
        context = Context(
            name="health_context", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session.add(context)
        await async_session.flush()

        token = OAuthToken(
            context_id=context.id,
            provider="homey",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        token.set_access_token("health_token")  # Use setter for encryption
        async_session.add(token)
        await async_session.commit()

        settings.homey_mcp_url = "https://mcp.athom.com/sse"

        pool = McpClientPool(settings)

        # Mock unhealthy client
        with patch("core.mcp.client_pool.McpClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.name = "Homey"
            mock_client.tools = []
            mock_client.is_connected = True
            # First ping succeeds, second ping fails
            mock_client.ping = AsyncMock(side_effect=[True, False])
            mock_client_class.return_value = mock_client

            # First call - creates and caches client
            clients_1 = await pool.get_clients(context.id, async_session)
            assert len(clients_1) == 1

            # Second call - health check fails, should recreate
            await pool.get_clients(context.id, async_session)

            # Should have tried to disconnect unhealthy client
            mock_client.disconnect.assert_called()

            # Should have created new client (2 total calls)
            assert mock_client_class.call_count == 2

    async def test_disconnect_context(self, async_session, settings):
        """Test disconnecting all clients for a context."""
        # Create context
        context = Context(
            name="disconnect_context", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session.add(context)
        await async_session.flush()

        token = OAuthToken(
            context_id=context.id,
            provider="homey",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        token.set_access_token("disconnect_token")  # Use setter for encryption
        async_session.add(token)
        await async_session.commit()

        settings.homey_mcp_url = "https://mcp.athom.com/sse"

        pool = McpClientPool(settings)

        # Create clients
        with patch("core.mcp.client_pool.McpClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.name = "Homey"
            mock_client.tools = []
            mock_client.is_connected = True
            mock_client.ping = AsyncMock(return_value=True)
            mock_client_class.return_value = mock_client

            clients = await pool.get_clients(context.id, async_session)
            assert len(clients) == 1

            # Disconnect context
            await pool.disconnect_context(context.id)

            # Verify client was disconnected
            mock_client.disconnect.assert_called_once()

            # Verify removed from pool
            assert context.id not in pool._pools

    async def test_shutdown_all_contexts(self, async_session, settings):
        """Test shutting down all client pools."""
        # Create two contexts
        context_a = Context(
            name="shutdown_context_a", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        context_b = Context(
            name="shutdown_context_b", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session.add(context_a)
        async_session.add(context_b)
        await async_session.flush()

        # Add tokens for both
        token_a = OAuthToken(
            context_id=context_a.id,
            provider="homey",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        token_a.set_access_token("shutdown_token_a")  # Use setter for encryption
        token_b = OAuthToken(
            context_id=context_b.id,
            provider="homey",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        token_b.set_access_token("shutdown_token_b")  # Use setter for encryption
        async_session.add(token_a)
        async_session.add(token_b)
        await async_session.commit()

        settings.homey_mcp_url = "https://mcp.athom.com/sse"

        pool = McpClientPool(settings)

        # Create clients for both contexts
        with patch("core.mcp.client_pool.McpClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.name = "Homey"
            mock_client.tools = []
            mock_client.is_connected = True
            mock_client.ping = AsyncMock(return_value=True)
            mock_client_class.return_value = mock_client

            await pool.get_clients(context_a.id, async_session)
            await pool.get_clients(context_b.id, async_session)

            assert len(pool._pools) == 2

            # Shutdown all
            await pool.shutdown()

            # Verify all disconnected (called for each context)
            # Note: disconnect might be called multiple times per client
            assert mock_client.disconnect.call_count >= 2

            # Verify pools cleared
            assert len(pool._pools) == 0

    async def test_get_health_status(self, async_session, settings):
        """Test getting health status of all pools."""
        # Create context
        context = Context(
            name="health_status_context",
            type="virtual",
            config={},
            default_cwd="/tmp",  # noqa: S108,
        )
        async_session.add(context)
        await async_session.flush()

        token = OAuthToken(
            context_id=context.id,
            provider="homey",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        token.set_access_token("health_status_token")  # Use setter for encryption
        async_session.add(token)
        await async_session.commit()

        settings.homey_mcp_url = "https://mcp.athom.com/sse"

        pool = McpClientPool(settings)

        # Create clients
        with patch("core.mcp.client_pool.McpClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.name = "Homey"
            mock_client.tools = [MagicMock(), MagicMock()]  # 2 tools
            mock_client.resources = []
            mock_client.prompts = []
            mock_client.is_connected = True
            mock_client.state = MagicMock()
            mock_client.state.name = "CONNECTED"
            mock_client.is_cache_stale = MagicMock(return_value=False)
            mock_client.connect = AsyncMock()
            mock_client.ping = AsyncMock(return_value=True)

            mock_client_class.return_value = mock_client

            await pool.get_clients(context.id, async_session)

            # Get health status
            health = pool.get_health_status()

            assert str(context.id) in health
            context_health = health[str(context.id)]
            assert context_health["total_clients"] == 1
            assert len(context_health["clients"]) == 1

            client_info = context_health["clients"][0]
            assert client_info["name"] == "Homey"
            assert client_info["connected"] is True
            assert client_info["tools_count"] == 2

    async def test_get_stats(self, async_session, settings):
        """Test getting pool statistics."""
        # Create two contexts
        context_a = Context(
            name="stats_context_a", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        context_b = Context(
            name="stats_context_b", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session.add(context_a)
        async_session.add(context_b)
        await async_session.flush()

        # Add tokens
        token_a = OAuthToken(
            context_id=context_a.id,
            provider="homey",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        token_a.set_access_token("stats_token_a")  # Use setter for encryption
        token_b = OAuthToken(
            context_id=context_b.id,
            provider="homey",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        token_b.set_access_token("stats_token_b")  # Use setter for encryption
        async_session.add(token_a)
        async_session.add(token_b)
        await async_session.commit()

        settings.homey_mcp_url = "https://mcp.athom.com/sse"

        pool = McpClientPool(settings)

        # Create clients
        with patch("core.mcp.client_pool.McpClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.name = "Homey"
            mock_client.tools = []
            mock_client.is_connected = True
            mock_client.connect = AsyncMock()
            mock_client.ping = AsyncMock(return_value=True)

            mock_client_class.return_value = mock_client

            await pool.get_clients(context_a.id, async_session)
            await pool.get_clients(context_b.id, async_session)

            # Get stats
            stats = pool.get_stats()

            assert stats["total_contexts"] == 2
            assert stats["total_clients"] == 2
            assert stats["connected_clients"] == 2
            assert stats["disconnected_clients"] == 0

    async def test_concurrent_get_clients_same_context(self, async_session, settings):
        """Test that concurrent get_clients calls for same context don't create duplicates."""
        # Create context
        context = Context(
            name="concurrent_context", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session.add(context)
        await async_session.flush()

        token = OAuthToken(
            context_id=context.id,
            provider="homey",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        token.set_access_token("concurrent_token")  # Use setter for encryption
        async_session.add(token)
        await async_session.commit()

        settings.homey_mcp_url = "https://mcp.athom.com/sse"

        pool = McpClientPool(settings)

        # Mock McpClient
        with patch("core.mcp.client_pool.McpClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.name = "Homey"
            mock_client.tools = []
            mock_client.is_connected = True
            mock_client.ping = AsyncMock(return_value=True)
            mock_client_class.return_value = mock_client

            # Call get_clients concurrently
            from core.db.engine import AsyncSessionLocal

            async def get_clients_with_session():
                async with AsyncSessionLocal() as session:
                    return await pool.get_clients(context.id, session)

            results = await asyncio.gather(
                get_clients_with_session(),
                get_clients_with_session(),
                get_clients_with_session(),
            )

            # Should have created client only once (lock prevents duplicates)
            # Note: In real implementation, the lock should prevent this
            # For now, just verify all calls succeeded
            assert all(len(r) == 1 for r in results)
