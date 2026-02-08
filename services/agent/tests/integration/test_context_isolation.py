"""Integration tests for multi-tenant context isolation.

This test suite verifies that different contexts are properly isolated and cannot
access each other's data:
- Conversations
- OAuth tokens
- Tool permissions
- Memory (Qdrant)
- MCP clients
- Service instances
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from core.core.memory import MemoryRecord, MemoryStore
from core.core.service_factory import ServiceFactory
from core.db.models import Context, Conversation, ToolPermission
from core.db.oauth_models import OAuthToken


@pytest.mark.asyncio
class TestContextIsolation:
    """Test that contexts are properly isolated from each other."""

    async def test_conversation_isolation(self, async_session):
        """Verify conversations are isolated between contexts."""
        # Create two contexts
        context_a = Context(
            name="context_a", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        context_b = Context(
            name="context_b", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session.add(context_a)
        async_session.add(context_b)
        await async_session.flush()

        # Create conversation for context A
        conv_a = Conversation(
            platform="test",
            platform_id="conv_a",
            context_id=context_a.id,
            current_cwd="/tmp",  # noqa: S108,
        )
        async_session.add(conv_a)

        # Create conversation for context B
        conv_b = Conversation(
            platform="test",
            platform_id="conv_b",
            context_id=context_b.id,
            current_cwd="/tmp",  # noqa: S108,
        )
        async_session.add(conv_b)
        await async_session.commit()

        # Query conversations for context A
        stmt_a = select(Conversation).where(Conversation.context_id == context_a.id)
        result_a = await async_session.execute(stmt_a)
        convs_a = result_a.scalars().all()

        # Query conversations for context B
        stmt_b = select(Conversation).where(Conversation.context_id == context_b.id)
        result_b = await async_session.execute(stmt_b)
        convs_b = result_b.scalars().all()

        # Assert isolation
        assert len(convs_a) == 1
        assert len(convs_b) == 1
        assert convs_a[0].id != convs_b[0].id
        assert convs_a[0].platform_id == "conv_a"
        assert convs_b[0].platform_id == "conv_b"

    async def test_oauth_token_isolation(self, async_session):
        """Verify OAuth tokens are isolated between contexts."""
        # Create two contexts
        context_a = Context(
            name="oauth_context_a", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        context_b = Context(
            name="oauth_context_b", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session.add(context_a)
        async_session.add(context_b)
        await async_session.flush()

        # Create OAuth token for context A (Homey)
        token_a = OAuthToken(
            context_id=context_a.id,
            provider="homey",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        token_a.set_access_token("token_a_secret")  # Use setter for encryption
        async_session.add(token_a)

        # Create OAuth token for context B (Homey)
        token_b = OAuthToken(
            context_id=context_b.id,
            provider="homey",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        token_b.set_access_token("token_b_secret")  # Use setter for encryption
        async_session.add(token_b)
        await async_session.commit()

        # Query tokens for context A
        stmt_a = select(OAuthToken).where(OAuthToken.context_id == context_a.id)
        result_a = await async_session.execute(stmt_a)
        tokens_a = result_a.scalars().all()

        # Query tokens for context B
        stmt_b = select(OAuthToken).where(OAuthToken.context_id == context_b.id)
        result_b = await async_session.execute(stmt_b)
        tokens_b = result_b.scalars().all()

        # Assert isolation
        assert len(tokens_a) == 1
        assert len(tokens_b) == 1
        assert tokens_a[0].get_access_token() == "token_a_secret"  # Use getter for decryption
        assert tokens_b[0].get_access_token() == "token_b_secret"  # Use getter for decryption
        assert tokens_a[0].id != tokens_b[0].id

    async def test_tool_permission_isolation(self, async_session):
        """Verify tool permissions are isolated between contexts."""
        # Create two contexts
        context_a = Context(
            name="perm_context_a", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        context_b = Context(
            name="perm_context_b", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session.add(context_a)
        async_session.add(context_b)
        await async_session.flush()

        # Create tool permission for context A (deny bash)
        perm_a = ToolPermission(
            context_id=context_a.id,
            tool_name="bash",
            allowed=False,
        )
        async_session.add(perm_a)

        # Create tool permission for context B (allow bash)
        perm_b = ToolPermission(
            context_id=context_b.id,
            tool_name="bash",
            allowed=True,
        )
        async_session.add(perm_b)
        await async_session.commit()

        # Query permissions for context A
        stmt_a = select(ToolPermission).where(ToolPermission.context_id == context_a.id)
        result_a = await async_session.execute(stmt_a)
        perms_a = result_a.scalars().all()

        # Query permissions for context B
        stmt_b = select(ToolPermission).where(ToolPermission.context_id == context_b.id)
        result_b = await async_session.execute(stmt_b)
        perms_b = result_b.scalars().all()

        # Assert isolation
        assert len(perms_a) == 1
        assert len(perms_b) == 1
        assert perms_a[0].allowed is False
        assert perms_b[0].allowed is True
        assert perms_a[0].tool_name == "bash"
        assert perms_b[0].tool_name == "bash"

    async def test_context_cascade_delete(self, async_session):
        """Verify deleting a context cascades to related entities."""
        # Create context
        context = Context(
            name="cascade_test", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session.add(context)
        await async_session.flush()

        # Create related entities
        conversation = Conversation(
            platform="test",
            platform_id="cascade_conv",
            context_id=context.id,
            current_cwd="/tmp",  # noqa: S108,
        )
        async_session.add(conversation)

        oauth_token = OAuthToken(
            context_id=context.id,
            provider="homey",
            token_type="Bearer",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        oauth_token.set_access_token("cascade_token")  # Use setter for encryption
        async_session.add(oauth_token)

        tool_perm = ToolPermission(
            context_id=context.id,
            tool_name="bash",
            allowed=False,
        )
        async_session.add(tool_perm)
        await async_session.commit()

        # Store IDs
        conv_id = conversation.id
        token_id = oauth_token.id
        perm_id = tool_perm.id

        # Delete context
        await async_session.delete(context)
        await async_session.commit()

        # Verify cascade deletion
        stmt = select(Conversation).where(Conversation.id == conv_id)
        result = await async_session.execute(stmt)
        assert result.scalar_one_or_none() is None

        stmt = select(OAuthToken).where(OAuthToken.id == token_id)
        result = await async_session.execute(stmt)
        assert result.scalar_one_or_none() is None

        stmt = select(ToolPermission).where(ToolPermission.id == perm_id)
        result = await async_session.execute(stmt)
        assert result.scalar_one_or_none() is None

    @pytest.mark.skip(reason="Requires Qdrant running")
    async def test_memory_isolation(self, settings):
        """Verify memory (Qdrant) is isolated between contexts."""
        context_a_id = uuid.uuid4()
        context_b_id = uuid.uuid4()

        # Create memory stores for each context
        memory_a = MemoryStore(settings, context_id=context_a_id)
        memory_b = MemoryStore(settings, context_id=context_b_id)

        await memory_a.ainit()
        await memory_b.ainit()

        # Store memories for context A
        record_a = MemoryRecord(
            conversation_id="conv_a",
            text="Context A secret information",
            metadata={},
        )
        await memory_a.store(record_a)

        # Store memories for context B
        record_b = MemoryRecord(
            conversation_id="conv_b",
            text="Context B secret information",
            metadata={},
        )
        await memory_b.store(record_b)

        # Search from context A - should only see context A memories
        results_a = await memory_a.search("secret", limit=10)
        assert len(results_a) >= 1
        assert all("Context A" in r.text for r in results_a)
        assert all("Context B" not in r.text for r in results_a)

        # Search from context B - should only see context B memories
        results_b = await memory_b.search("secret", limit=10)
        assert len(results_b) >= 1
        assert all("Context B" in r.text for r in results_b)
        assert all("Context A" not in r.text for r in results_b)

    async def test_service_factory_creates_isolated_services(
        self, async_session, settings, litellm_client
    ):
        """Verify ServiceFactory creates isolated services per context."""
        # Create two contexts with different tool permissions
        context_a = Context(
            name="service_context_a", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        context_b = Context(
            name="service_context_b", type="virtual", config={}, default_cwd="/tmp"  # noqa: S108,
        )
        async_session.add(context_a)
        async_session.add(context_b)
        await async_session.flush()

        # Context A: Deny bash
        perm_a = ToolPermission(
            context_id=context_a.id,
            tool_name="bash",
            allowed=False,
        )
        async_session.add(perm_a)

        # Context B: Allow all (no permissions)
        await async_session.commit()

        # Create ServiceFactory
        factory = ServiceFactory(settings=settings, litellm_client=litellm_client)

        # Create services for each context
        service_a = await factory.create_service(context_a.id, async_session)
        service_b = await factory.create_service(context_b.id, async_session)

        # Verify they are different instances
        assert service_a is not service_b

        # Verify tool registries are different
        assert service_a.tool_registry is not service_b.tool_registry

        # Verify tool permissions are applied
        tools_a = service_a.tool_registry.list_tools()
        tools_b = service_b.tool_registry.list_tools()

        # Context A should have fewer tools (bash denied)
        assert len(tools_a) < len(tools_b)
        assert "bash" not in tools_a
        # Note: We can't guarantee bash is in tools_b without knowing the base registry

        # Verify memory stores are different instances with correct context_id
        assert service_a.memory is not service_b.memory
        assert service_a.memory._context_id == context_a.id
        assert service_b.memory._context_id == context_b.id

    async def test_concurrent_context_access(self, async_session, settings, litellm_client):
        """Verify concurrent access to different contexts doesn't cause interference."""
        import asyncio

        # Create multiple contexts
        contexts = []
        for i in range(5):
            context = Context(
                name=f"concurrent_context_{i}",
                type="virtual",
                config={},
                default_cwd="/tmp",  # noqa: S108,
            )
            async_session.add(context)
            contexts.append(context)

        await async_session.flush()
        await async_session.commit()

        factory = ServiceFactory(settings=settings, litellm_client=litellm_client)

        # Create services concurrently
        async def create_service(context_id):
            # Need fresh session for each concurrent task
            from core.db.engine import AsyncSessionLocal

            async with AsyncSessionLocal() as session:
                service = await factory.create_service(context_id, session)
                return service

        services = await asyncio.gather(*[create_service(ctx.id) for ctx in contexts])

        # Verify all services were created
        assert len(services) == 5

        # Verify they are all different instances
        service_ids = [id(s) for s in services]
        assert len(set(service_ids)) == 5

        # Verify each has correct context_id in memory
        for i, service in enumerate(services):
            assert service.memory._context_id == contexts[i].id


@pytest.fixture
def litellm_client(settings):
    """Create a LiteLLM client for testing."""
    from core.core.litellm_client import LiteLLMClient

    return LiteLLMClient(settings)
