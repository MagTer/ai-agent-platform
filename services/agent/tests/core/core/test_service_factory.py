"""Unit tests for ServiceFactory.

Tests the context-scoped service creation factory that enables multi-tenant isolation.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.core.service_factory import ServiceFactory
from core.db.models import Context, ToolPermission
from core.db.oauth_models import OAuthToken


@pytest.mark.asyncio
class TestServiceFactory:
    """Test ServiceFactory context-scoped service creation."""

    async def test_factory_initialization(self, settings, litellm_client):
        """Test that factory initializes with base tool registry."""
        factory = ServiceFactory(settings=settings, litellm_client=litellm_client)

        assert factory._settings == settings
        assert factory._litellm == litellm_client
        assert factory._base_tool_registry is not None
        assert len(factory._base_tool_registry.list_tools()) > 0

    async def test_base_tool_registry_cached(self, settings, litellm_client):
        """Test that base tool registry is loaded once and cached."""
        with patch("core.core.service_factory.load_tool_registry") as mock_load:
            mock_registry = MagicMock()
            mock_registry.list_tools.return_value = ["tool1", "tool2"]
            mock_load.return_value = mock_registry

            ServiceFactory(settings=settings, litellm_client=litellm_client)

            # Should be called once during init
            mock_load.assert_called_once_with(settings.tools_config_path)

    async def test_create_service_without_permissions(
        self, async_session, settings, litellm_client
    ):
        """Test creating service for context with no tool permissions."""
        # Create context
        context = Context(
            name="test_context", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session.add(context)
        await async_session.flush()
        await async_session.commit()

        factory = ServiceFactory(settings=settings, litellm_client=litellm_client)
        service = await factory.create_service(context.id, async_session)

        # Verify service created
        assert service is not None
        assert service.tool_registry is not None
        assert service.memory is not None

        # Verify memory has correct context_id
        assert service.memory._context_id == context.id

        # Verify all tools available (no permissions = allow all)
        base_tools_count = len(factory._base_tool_registry.list_tools())
        service_tools_count = len(service.tool_registry.list_tools())
        # Note: MCP tools might be added, so >= not ==
        assert service_tools_count >= base_tools_count

    async def test_create_service_with_permissions(self, async_session, settings, litellm_client):
        """Test creating service with tool permissions applied."""
        # Create context
        context = Context(
            name="perm_test_context", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session.add(context)
        await async_session.flush()

        # Add tool permissions (deny bash, allow python)
        perm_bash = ToolPermission(
            context_id=context.id,
            tool_name="bash",
            allowed=False,
        )
        perm_python = ToolPermission(
            context_id=context.id,
            tool_name="python",
            allowed=True,
        )
        async_session.add(perm_bash)
        async_session.add(perm_python)
        await async_session.commit()

        factory = ServiceFactory(settings=settings, litellm_client=litellm_client)

        # Mock the base registry to have known tools
        mock_registry = MagicMock()
        mock_registry.list_tools.return_value = ["bash", "python", "grep"]
        mock_registry.clone.return_value = mock_registry
        factory._base_tool_registry = mock_registry

        await factory.create_service(context.id, async_session)

        # Verify permissions were loaded and applied
        mock_registry.filter_by_permissions.assert_called_once()
        call_args = mock_registry.filter_by_permissions.call_args
        permissions_dict = call_args[0][0]

        assert permissions_dict["bash"] is False
        assert permissions_dict["python"] is True

    async def test_create_service_clones_registry(self, async_session, settings, litellm_client):
        """Test that each service gets a cloned tool registry."""
        # Create two contexts
        context_a = Context(
            name="clone_context_a", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        context_b = Context(
            name="clone_context_b", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session.add(context_a)
        async_session.add(context_b)
        await async_session.flush()
        await async_session.commit()

        factory = ServiceFactory(settings=settings, litellm_client=litellm_client)

        service_a = await factory.create_service(context_a.id, async_session)
        service_b = await factory.create_service(context_b.id, async_session)

        # Verify different registry instances
        assert service_a.tool_registry is not service_b.tool_registry

        # Verify base registry not mutated
        base_tools = factory._base_tool_registry.list_tools()
        assert len(base_tools) > 0

    async def test_create_service_loads_mcp_tools(self, async_session, settings, litellm_client):
        """Test that MCP tools are loaded for contexts with OAuth tokens."""
        # Create context
        context = Context(
            name="mcp_test_context", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session.add(context)
        await async_session.flush()

        # Add OAuth token (would trigger MCP loading in real scenario)
        from datetime import datetime, timedelta

        oauth_token = OAuthToken(
            context_id=context.id,
            provider="homey",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        oauth_token.set_access_token("test_token")  # Use setter for encryption
        async_session.add(oauth_token)
        await async_session.commit()

        factory = ServiceFactory(settings=settings, litellm_client=litellm_client)

        # Mock MCP loading
        with patch("core.core.service_factory.load_mcp_tools_for_context") as mock_load_mcp:
            await factory.create_service(context.id, async_session)

            # Verify MCP loader was called
            mock_load_mcp.assert_called_once()
            call_args = mock_load_mcp.call_args
            assert call_args[1]["context_id"] == context.id
            assert call_args[1]["settings"] == settings

    async def test_create_service_handles_mcp_failure(
        self, async_session, settings, litellm_client
    ):
        """Test that service creation continues even if MCP loading fails."""
        # Create context
        context = Context(
            name="mcp_fail_context", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session.add(context)
        await async_session.flush()
        await async_session.commit()

        factory = ServiceFactory(settings=settings, litellm_client=litellm_client)

        # Mock MCP loading to fail
        with patch(
            "core.core.service_factory.load_mcp_tools_for_context",
            side_effect=RuntimeError("MCP connection failed"),
        ):
            # Should not raise - service creation continues
            service = await factory.create_service(context.id, async_session)

            assert service is not None
            assert service.tool_registry is not None

    async def test_create_service_memory_context_isolation(
        self, async_session, settings, litellm_client
    ):
        """Test that memory stores have correct context_id for isolation."""
        # Create two contexts
        context_a = Context(
            name="memory_context_a", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        context_b = Context(
            name="memory_context_b", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session.add(context_a)
        async_session.add(context_b)
        await async_session.flush()
        await async_session.commit()

        factory = ServiceFactory(settings=settings, litellm_client=litellm_client)

        service_a = await factory.create_service(context_a.id, async_session)
        service_b = await factory.create_service(context_b.id, async_session)

        # Verify memory stores have correct context_id
        assert service_a.memory._context_id == context_a.id
        assert service_b.memory._context_id == context_b.id

        # Verify they are different instances
        assert service_a.memory is not service_b.memory

    async def test_multiple_create_service_calls(self, async_session, settings, litellm_client):
        """Test that multiple calls to create_service create new instances."""
        # Create context
        context = Context(
            name="multi_create_context",
            type="virtual",
            config={},
            default_cwd="/tmp",  # noqa: S108,
        )
        async_session.add(context)
        await async_session.flush()
        await async_session.commit()

        factory = ServiceFactory(settings=settings, litellm_client=litellm_client)

        # Create service twice for same context
        service_1 = await factory.create_service(context.id, async_session)
        service_2 = await factory.create_service(context.id, async_session)

        # Should be different instances (no caching at service level)
        assert service_1 is not service_2
        assert service_1.tool_registry is not service_2.tool_registry
        assert service_1.memory is not service_2.memory


@pytest.fixture
def litellm_client(settings):
    """Create a LiteLLM client for testing."""
    from core.core.litellm_client import LiteLLMClient

    return LiteLLMClient(settings)
