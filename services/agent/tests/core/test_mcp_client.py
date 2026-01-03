"""Tests for MCP client implementation."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.mcp.client import McpClient, McpConnectionState
from core.models.mcp import McpPrompt, McpResource, McpTool


class TestMcpConnectionState:
    """Tests for connection state enum."""

    def test_state_values(self) -> None:
        """Verify all expected states exist."""
        assert McpConnectionState.DISCONNECTED
        assert McpConnectionState.CONNECTING
        assert McpConnectionState.CONNECTED
        assert McpConnectionState.RECONNECTING
        assert McpConnectionState.FAILED


class TestMcpClientInit:
    """Tests for McpClient initialization."""

    def test_default_state_is_disconnected(self) -> None:
        """New client should be in DISCONNECTED state."""
        client = McpClient(url="http://localhost:8080/sse", name="test")
        assert client.state == McpConnectionState.DISCONNECTED
        assert not client.is_connected

    def test_auth_header_set(self) -> None:
        """Auth token should be set as Bearer header."""
        client = McpClient(
            url="http://localhost:8080/sse",
            auth_token="test-token",
            name="test",
        )
        assert client._headers["Authorization"] == "Bearer secret123"

    def test_empty_caches(self) -> None:
        """Caches should be empty initially."""
        client = McpClient(url="http://localhost:8080/sse", name="test")
        assert client.tools == []
        assert client.resources == []
        assert client.prompts == []

    def test_cache_is_stale_on_init(self) -> None:
        """Cache should be considered stale when no timestamp."""
        client = McpClient(url="http://localhost:8080/sse", name="test")
        assert client.is_cache_stale()

    def test_name_property(self) -> None:
        """Name property should return configured name."""
        client = McpClient(url="http://localhost:8080/sse", name="my-server")
        assert client.name == "my-server"


class TestMcpClientCacheStaleness:
    """Tests for cache TTL logic."""

    def test_fresh_cache(self) -> None:
        """Cache should not be stale within TTL."""
        client = McpClient(
            url="http://localhost:8080/sse",
            name="test",
            cache_ttl_seconds=300,
        )
        client._cache_timestamp = datetime.now()
        assert not client.is_cache_stale()

    def test_stale_cache(self) -> None:
        """Cache should be stale after TTL expires."""
        client = McpClient(
            url="http://localhost:8080/sse",
            name="test",
            cache_ttl_seconds=10,
        )
        client._cache_timestamp = datetime.now() - timedelta(seconds=15)
        assert client.is_cache_stale()

    def test_zero_ttl_always_stale(self) -> None:
        """Zero TTL should always be stale."""
        client = McpClient(
            url="http://localhost:8080/sse",
            name="test",
            cache_ttl_seconds=0,
        )
        client._cache_timestamp = datetime.now()
        assert client.is_cache_stale()


class TestMcpClientConnect:
    """Tests for connection logic."""

    @pytest.mark.asyncio
    async def test_connect_success(self) -> None:
        """Successful connection should update state."""
        client = McpClient(
            url="http://localhost:8080/sse",
            name="test",
            max_retries=1,
        )

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[]))
        mock_session.list_resources = AsyncMock(return_value=MagicMock(resources=[]))
        mock_session.list_prompts = AsyncMock(return_value=MagicMock(prompts=[]))

        with patch("core.mcp.client.sse_client") as mock_sse:
            # Create async context manager mock
            async def mock_sse_cm(*args: Any, **kwargs: Any) -> Any:
                yield (AsyncMock(), AsyncMock())

            mock_sse.return_value = mock_sse_cm()

            with patch("core.mcp.client.ClientSession", return_value=mock_session):
                await client.connect()

        assert client.state == McpConnectionState.CONNECTED
        assert client.is_connected

    @pytest.mark.asyncio
    async def test_connect_retries_on_failure(self) -> None:
        """Connection should retry with exponential backoff."""
        client = McpClient(
            url="http://localhost:8080/sse",
            name="test",
            max_retries=2,
        )

        call_count = 0

        async def failing_sse(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            raise ConnectionError("Connection refused")
            yield  # Make it a generator

        with patch("core.mcp.client.sse_client", side_effect=failing_sse):
            with pytest.raises(ConnectionError):
                await client.connect()

        # Should have retried max_retries times
        assert call_count == 2
        assert client.state == McpConnectionState.FAILED

    @pytest.mark.asyncio
    async def test_already_connected_noop(self) -> None:
        """Connecting when already connected should be a no-op."""
        client = McpClient(url="http://localhost:8080/sse", name="test")
        client._state = McpConnectionState.CONNECTED

        # Should not attempt connection
        with patch("core.mcp.client.sse_client") as mock_sse:
            await client.connect()
            mock_sse.assert_not_called()


class TestMcpClientDisconnect:
    """Tests for disconnection."""

    @pytest.mark.asyncio
    async def test_disconnect_updates_state(self) -> None:
        """Disconnect should update state to DISCONNECTED."""
        client = McpClient(url="http://localhost:8080/sse", name="test")
        client._state = McpConnectionState.CONNECTED

        await client.disconnect()

        assert client.state == McpConnectionState.DISCONNECTED
        assert not client.is_connected


class TestMcpClientPing:
    """Tests for health check ping."""

    @pytest.mark.asyncio
    async def test_ping_disconnected_returns_false(self) -> None:
        """Ping should return False when not connected."""
        client = McpClient(url="http://localhost:8080/sse", name="test")
        assert not await client.ping()

    @pytest.mark.asyncio
    async def test_ping_connected_success(self) -> None:
        """Ping should return True when healthy."""
        client = McpClient(url="http://localhost:8080/sse", name="test")
        client._state = McpConnectionState.CONNECTED

        mock_session = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[]))
        client._mcp_session = mock_session

        assert await client.ping()

    @pytest.mark.asyncio
    async def test_ping_failure_updates_state(self) -> None:
        """Ping failure should mark client as disconnected."""
        client = McpClient(url="http://localhost:8080/sse", name="test")
        client._state = McpConnectionState.CONNECTED

        mock_session = AsyncMock()
        mock_session.list_tools = AsyncMock(side_effect=Exception("Connection lost"))
        client._mcp_session = mock_session

        result = await client.ping()

        assert not result
        assert client.state == McpConnectionState.DISCONNECTED


class TestMcpModels:
    """Tests for MCP Pydantic models."""

    def test_mcp_tool_create(self) -> None:
        """McpTool should be creatable with required fields."""
        tool = McpTool(
            name="my_tool",
            description="A test tool",
            input_schema={"type": "object"},
        )
        assert tool.name == "my_tool"
        assert tool.description == "A test tool"

    def test_mcp_resource_create(self) -> None:
        """McpResource should be creatable."""
        resource = McpResource(
            uri="file:///test.txt",
            name="Test File",
        )
        assert resource.uri == "file:///test.txt"
        assert resource.name == "Test File"

    def test_mcp_resource_mime_type_alias(self) -> None:
        """McpResource should handle mimeType alias."""
        resource = McpResource(
            uri="file:///test.txt",
            name="Test File",
            mimeType="text/plain",
        )
        assert resource.mime_type == "text/plain"

    def test_mcp_prompt_create(self) -> None:
        """McpPrompt should be creatable."""
        prompt = McpPrompt(
            name="greeting",
            description="A greeting prompt",
            arguments=[{"name": "name", "required": True}],
        )
        assert prompt.name == "greeting"
        assert len(prompt.arguments) == 1
