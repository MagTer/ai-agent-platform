"""Tests for MCP client implementation."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.mcp.client import McpClient, McpConnectionState, McpTransport
from core.models.mcp import McpPrompt, McpResource, McpTool, McpToolAnnotations


def _mock_sse_cm(read: Any = None, write: Any = None) -> Any:
    """Create a proper async context manager mock for sse_client."""

    @asynccontextmanager
    async def _cm(*args: Any, **kwargs: Any) -> Any:
        yield (read or AsyncMock(), write or AsyncMock())

    return _cm


def _mock_streamable_cm(
    read: Any = None, write: Any = None, session_id: str = "test-session"
) -> Any:
    """Create a proper async context manager mock for streamablehttp_client."""

    @asynccontextmanager
    async def _cm(*args: Any, **kwargs: Any) -> Any:
        yield (read or AsyncMock(), write or AsyncMock(), session_id)

    return _cm


class TestMcpConnectionState:
    """Tests for connection state enum."""

    def test_state_values(self) -> None:
        """Verify all expected states exist."""
        assert McpConnectionState.DISCONNECTED
        assert McpConnectionState.CONNECTING
        assert McpConnectionState.CONNECTED
        assert McpConnectionState.RECONNECTING
        assert McpConnectionState.FAILED


class TestMcpTransport:
    """Tests for transport enum."""

    def test_transport_values(self) -> None:
        """Verify all transport types exist."""
        assert McpTransport.AUTO.value == "auto"
        assert McpTransport.SSE.value == "sse"
        assert McpTransport.STREAMABLE_HTTP.value == "streamable_http"


class TestMcpClientInit:
    """Tests for McpClient initialization."""

    def test_default_state_is_disconnected(self) -> None:
        """New client should be in DISCONNECTED state."""
        client = McpClient(url="http://localhost:8080/sse", name="test")
        assert client.state == McpConnectionState.DISCONNECTED
        assert not client.is_connected

    def test_default_transport_is_auto(self) -> None:
        """Default transport should be AUTO."""
        client = McpClient(url="http://localhost:8080/sse", name="test")
        assert client._transport == McpTransport.AUTO

    def test_explicit_transport(self) -> None:
        """Transport should be configurable."""
        client = McpClient(
            url="http://localhost:8080/sse",
            name="test",
            transport=McpTransport.SSE,
        )
        assert client._transport == McpTransport.SSE

    def test_static_token_stored(self) -> None:
        """Static auth token should be stored for later use."""
        client = McpClient(
            url="http://localhost:8080/sse",
            auth_token="test-token",
            name="test",
        )
        assert client._static_token == "test-token"

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
    async def test_connect_success_sse(self) -> None:
        """Successful SSE connection should update state."""
        client = McpClient(
            url="http://localhost:8080/sse",
            name="test",
            max_retries=1,
            transport=McpTransport.SSE,
        )

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[]))
        mock_session.list_resources = AsyncMock(return_value=MagicMock(resources=[]))
        mock_session.list_prompts = AsyncMock(return_value=MagicMock(prompts=[]))

        with patch("core.mcp.client.sse_client", side_effect=_mock_sse_cm()):
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
            transport=McpTransport.SSE,
        )

        call_count = 0

        @asynccontextmanager
        async def failing_sse(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            raise ConnectionError("Connection refused")
            yield  # unreachable, needed for generator

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


class TestMcpTransportSelection:
    """Tests for transport selection and AUTO fallback logic."""

    @pytest.mark.asyncio
    async def test_auto_tries_streamable_http_first(self) -> None:
        """AUTO transport should try Streamable HTTP first."""
        client = McpClient(
            url="http://localhost:8080/mcp",
            name="test",
            max_retries=1,
            transport=McpTransport.AUTO,
        )

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[]))
        mock_session.list_resources = AsyncMock(return_value=MagicMock(resources=[]))
        mock_session.list_prompts = AsyncMock(return_value=MagicMock(prompts=[]))
        mock_session.server_info = "test-server"

        streamable_called = False

        @asynccontextmanager
        async def tracking_streamable(*args: Any, **kwargs: Any) -> Any:
            nonlocal streamable_called
            streamable_called = True
            yield (AsyncMock(), AsyncMock(), "session-123")

        with (
            patch("core.mcp.client.streamablehttp_client", side_effect=tracking_streamable),
            patch("core.mcp.client.sse_client") as mock_sse,
            patch("core.mcp.client.ClientSession", return_value=mock_session),
        ):
            await client.connect()

            assert streamable_called
            mock_sse.assert_not_called()

        assert client.state == McpConnectionState.CONNECTED

    @pytest.mark.asyncio
    async def test_auto_falls_back_to_sse(self) -> None:
        """AUTO transport should fall back to SSE when Streamable HTTP fails."""
        client = McpClient(
            url="http://localhost:8080/sse",
            name="test",
            max_retries=1,
            transport=McpTransport.AUTO,
        )

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[]))
        mock_session.list_resources = AsyncMock(return_value=MagicMock(resources=[]))
        mock_session.list_prompts = AsyncMock(return_value=MagicMock(prompts=[]))

        streamable_called = False
        sse_called = False

        @asynccontextmanager
        async def failing_streamable(*args: Any, **kwargs: Any) -> Any:
            nonlocal streamable_called
            streamable_called = True
            raise ConnectionError("Not supported")
            yield  # unreachable

        @asynccontextmanager
        async def tracking_sse(*args: Any, **kwargs: Any) -> Any:
            nonlocal sse_called
            sse_called = True
            yield (AsyncMock(), AsyncMock())

        with (
            patch("core.mcp.client.streamablehttp_client", side_effect=failing_streamable),
            patch("core.mcp.client.sse_client", side_effect=tracking_sse),
            patch("core.mcp.client.ClientSession", return_value=mock_session),
        ):
            await client.connect()

            assert streamable_called
            assert sse_called

        assert client.state == McpConnectionState.CONNECTED

    @pytest.mark.asyncio
    async def test_explicit_sse_transport(self) -> None:
        """Explicit SSE transport should only call SSE."""
        client = McpClient(
            url="http://localhost:8080/sse",
            name="test",
            max_retries=1,
            transport=McpTransport.SSE,
        )

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[]))
        mock_session.list_resources = AsyncMock(return_value=MagicMock(resources=[]))
        mock_session.list_prompts = AsyncMock(return_value=MagicMock(prompts=[]))

        sse_called = False

        @asynccontextmanager
        async def tracking_sse(*args: Any, **kwargs: Any) -> Any:
            nonlocal sse_called
            sse_called = True
            yield (AsyncMock(), AsyncMock())

        with (
            patch("core.mcp.client.streamablehttp_client") as mock_streamable,
            patch("core.mcp.client.sse_client", side_effect=tracking_sse),
            patch("core.mcp.client.ClientSession", return_value=mock_session),
        ):
            await client.connect()

            assert sse_called
            mock_streamable.assert_not_called()

        assert client.state == McpConnectionState.CONNECTED

    @pytest.mark.asyncio
    async def test_explicit_streamable_http_transport(self) -> None:
        """Explicit Streamable HTTP transport should only call streamablehttp_client."""
        client = McpClient(
            url="http://localhost:8080/mcp",
            name="test",
            max_retries=1,
            transport=McpTransport.STREAMABLE_HTTP,
        )

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[]))
        mock_session.list_resources = AsyncMock(return_value=MagicMock(resources=[]))
        mock_session.list_prompts = AsyncMock(return_value=MagicMock(prompts=[]))

        streamable_called = False

        @asynccontextmanager
        async def tracking_streamable(*args: Any, **kwargs: Any) -> Any:
            nonlocal streamable_called
            streamable_called = True
            yield (AsyncMock(), AsyncMock(), "session-456")

        with (
            patch("core.mcp.client.streamablehttp_client", side_effect=tracking_streamable),
            patch("core.mcp.client.sse_client") as mock_sse,
            patch("core.mcp.client.ClientSession", return_value=mock_session),
        ):
            await client.connect()

            assert streamable_called
            mock_sse.assert_not_called()

        assert client.state == McpConnectionState.CONNECTED


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
        mock_session.send_ping = AsyncMock(return_value=None)
        client._mcp_session = mock_session

        assert await client.ping()

    @pytest.mark.asyncio
    async def test_ping_failure_updates_state(self) -> None:
        """Ping failure should mark client as disconnected."""
        client = McpClient(url="http://localhost:8080/sse", name="test")
        client._state = McpConnectionState.CONNECTED

        mock_session = AsyncMock()
        mock_session.send_ping = AsyncMock(side_effect=Exception("Connection lost"))
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

    def test_mcp_tool_with_annotations(self) -> None:
        """McpTool should parse annotations from dict."""
        tool = McpTool(
            name="delete_item",
            description="Delete an item",
            input_schema={"type": "object"},
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        )
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.destructive_hint is True
        assert tool.annotations.idempotent_hint is False
        assert tool.annotations.open_world_hint is True

    def test_mcp_tool_without_annotations(self) -> None:
        """McpTool annotations should be None by default."""
        tool = McpTool(name="simple", description="Simple tool")
        assert tool.annotations is None
        assert tool.output_schema is None

    def test_mcp_tool_with_output_schema(self) -> None:
        """McpTool should handle outputSchema alias."""
        schema = {"type": "object", "properties": {"result": {"type": "string"}}}
        tool = McpTool(
            name="structured",
            description="Structured output tool",
            outputSchema=schema,
        )
        assert tool.output_schema == schema

    def test_mcp_tool_annotations_model_alias(self) -> None:
        """McpToolAnnotations should support both alias and field name."""
        annot = McpToolAnnotations(readOnlyHint=True)
        assert annot.read_only_hint is True
        annot2 = McpToolAnnotations(read_only_hint=True)
        assert annot2.read_only_hint is True

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


class TestMcpToolWrapper:
    """Tests for McpToolWrapper annotations and structured output."""

    def test_destructive_tool_requires_confirmation(self) -> None:
        """Tools with destructiveHint=True should require confirmation."""
        from core.tools.mcp_loader import McpToolWrapper

        mcp_tool = McpTool(
            name="delete_all",
            description="Delete everything",
            input_schema={"type": "object"},
            annotations={"destructiveHint": True},
        )
        mock_client = MagicMock()
        wrapper = McpToolWrapper(
            mcp_client=mock_client,
            mcp_tool=mcp_tool,
            server_name="test",
        )
        assert wrapper.requires_confirmation is True
        assert wrapper.mcp_annotations is not None
        assert wrapper.mcp_annotations["destructiveHint"] is True

    def test_readonly_tool_no_confirmation(self) -> None:
        """Tools with readOnlyHint=True should not require confirmation."""
        from core.tools.mcp_loader import McpToolWrapper

        mcp_tool = McpTool(
            name="list_items",
            description="List items",
            input_schema={"type": "object"},
            annotations={"readOnlyHint": True, "destructiveHint": False},
        )
        mock_client = MagicMock()
        wrapper = McpToolWrapper(
            mcp_client=mock_client,
            mcp_tool=mcp_tool,
            server_name="test",
        )
        assert wrapper.requires_confirmation is False

    def test_no_annotations_no_confirmation(self) -> None:
        """Tools without annotations should not require confirmation."""
        from core.tools.mcp_loader import McpToolWrapper

        mcp_tool = McpTool(
            name="basic",
            description="Basic tool",
            input_schema={"type": "object"},
        )
        mock_client = MagicMock()
        wrapper = McpToolWrapper(
            mcp_client=mock_client,
            mcp_tool=mcp_tool,
            server_name="test",
        )
        assert wrapper.requires_confirmation is False
        assert wrapper.mcp_annotations is None

    @pytest.mark.asyncio
    async def test_prefers_structured_content(self) -> None:
        """Wrapper should return structuredContent when available."""
        from core.tools.mcp_loader import McpToolWrapper

        mcp_tool = McpTool(name="get_data", description="Get data")
        mock_client = AsyncMock()
        structured = {"status": "ok", "items": [1, 2, 3]}
        mock_client.call_tool.return_value = {
            "structuredContent": structured,
            "content": [{"type": "text", "text": "fallback text"}],
        }

        wrapper = McpToolWrapper(
            mcp_client=mock_client,
            mcp_tool=mcp_tool,
            server_name="test",
        )
        result = await wrapper.run(query="test")
        assert result == structured

    @pytest.mark.asyncio
    async def test_falls_back_to_text_content(self) -> None:
        """Wrapper should fall back to text content when no structuredContent."""
        from core.tools.mcp_loader import McpToolWrapper

        mcp_tool = McpTool(name="get_text", description="Get text")
        mock_client = AsyncMock()
        mock_client.call_tool.return_value = {
            "content": [{"type": "text", "text": "hello world"}],
        }

        wrapper = McpToolWrapper(
            mcp_client=mock_client,
            mcp_tool=mcp_tool,
            server_name="test",
        )
        result = await wrapper.run(query="test")
        assert result == "hello world"
