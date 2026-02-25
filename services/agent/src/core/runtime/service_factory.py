"""Factory for creating context-aware AgentService instances."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING
from uuid import UUID

from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import ToolPermission
from core.runtime.config import Settings
from core.runtime.litellm_client import LiteLLMClient
from core.runtime.memory import MemoryStore
from core.runtime.service import AgentService
from core.tools.loader import load_tool_registry

if TYPE_CHECKING:
    from core.skills import SkillRegistryProtocol

LOGGER = logging.getLogger(__name__)


class ServiceFactory:
    """Factory for creating context-scoped AgentService instances.

    This factory creates AgentService instances with proper context isolation,
    ensuring that each context has its own:
    - ToolRegistry (with context-specific MCP tools)
    - MemoryStore (with context-filtered searches)
    - Properly scoped dependencies

    The factory caches the base tool registry to avoid repeatedly parsing
    the tools configuration file.
    """

    def __init__(
        self,
        settings: Settings,
        litellm_client: LiteLLMClient,
        skill_registry: SkillRegistryProtocol | None = None,
    ):
        """Initialize the service factory.

        Args:
            settings: Application settings
            litellm_client: Shared LiteLLM client (stateless, safe to share)
            skill_registry: Optional shared skill registry for skills-native execution
        """
        self._settings = settings
        self._litellm = litellm_client
        self._skill_registry = skill_registry

        # Cache base tool registry (native tools only, no MCP tools yet)
        # This is safe to share as a template - we'll clone it per-context
        LOGGER.info("Loading base tool registry from %s", settings.tools_config_path)
        self._base_tool_registry = load_tool_registry(settings.tools_config_path)
        LOGGER.info("Loaded %d base tools", len(self._base_tool_registry.list_tools()))

        # Create shared AsyncQdrantClient for reuse across requests
        # This avoids creating a new HTTP client + connection pool per request
        self._qdrant_client = AsyncQdrantClient(
            url=str(settings.qdrant_url),
            api_key=settings.qdrant_api_key,
            timeout=30,  # SECURITY: Prevent hanging under load
        )
        LOGGER.info("Created shared AsyncQdrantClient for service factory")

    async def create_service(
        self,
        context_id: UUID,
        session: AsyncSession,
    ) -> AgentService:
        """Create an AgentService instance for a specific context.

        This method:
        1. Clones the base tool registry to avoid mutation
        2. Loads tool permissions for this context
        3. Filters tools by permissions
        4. Loads MCP tools for this context (Phase 3)
        5. Loads per-context skills and creates CompositeSkillRegistry if needed
        6. Creates context-scoped MemoryStore
        7. Returns fully configured AgentService

        Args:
            context_id: Context UUID for isolation
            session: Database session for loading context-specific config

        Returns:
            AgentService instance scoped to the context
        """
        LOGGER.debug("Creating AgentService for context %s", context_id)

        # Clone base registry to avoid mutation
        # Each context gets its own registry instance
        tool_registry = self._base_tool_registry.clone()

        # Load tool permissions for this context
        stmt = select(ToolPermission).where(ToolPermission.context_id == context_id)
        result = await session.execute(stmt)
        permissions_records = result.scalars().all()

        if permissions_records:
            # Build permission dict: tool_name → allowed
            permissions = {perm.tool_name: perm.allowed for perm in permissions_records}

            LOGGER.debug(
                "Loaded %d tool permissions for context %s",
                len(permissions),
                context_id,
            )

            # Apply permissions to filter tools
            tool_registry.filter_by_permissions(permissions)
        else:
            LOGGER.debug(
                "No tool permissions defined for context %s - allowing all tools",
                context_id,
            )

        # Load MCP tools for this context (Phase 3)
        # Non-blocking: if cached clients exist, use them immediately.
        # Otherwise, fire a background task to connect and skip MCP for this request.
        from core.tools.mcp_loader import McpToolWrapper, get_mcp_client_pool

        try:
            pool = get_mcp_client_pool()
            cached = pool._pools.get(context_id, [])
            if cached:
                # Instant path: use already-connected MCP clients
                for client in cached:
                    if client.is_connected:
                        for mcp_tool in client.tools:
                            tool_registry.register(
                                McpToolWrapper(
                                    mcp_client=client,
                                    mcp_tool=mcp_tool,
                                    server_name=client.name,
                                )
                            )
                LOGGER.debug("Loaded cached MCP tools for context %s", context_id)
            elif context_id not in pool._negative_cache or (
                time.monotonic() - pool._negative_cache[context_id] >= pool._negative_cache_ttl
            ):
                # No cached clients and not in negative cache: connect in background
                asyncio.create_task(self._connect_mcp_background(context_id, pool))
        except RuntimeError:
            pass  # MCP pool not initialized

        # Load per-context skills (if any exist)
        # Fast path: if no context skills directory exists, skip loading
        from core.context.files import get_context_dir
        from core.skills.composite import CompositeSkillRegistry
        from core.skills.registry import SkillRegistry

        skill_registry_for_context = self._skill_registry  # default: shared global
        if self._skill_registry:  # Only if global registry exists
            context_skills_dir = get_context_dir(context_id) / "skills"
            if context_skills_dir.exists():
                # Check if directory contains any .md files before loading
                skill_files = list(context_skills_dir.rglob("*.md"))
                if skill_files:
                    LOGGER.debug(
                        "Loading %d per-context skill files for context %s",
                        len(skill_files),
                        context_id,
                    )
                    context_skills = await SkillRegistry.load_skills_from_dir(context_skills_dir)
                    if context_skills:
                        skill_registry_for_context = CompositeSkillRegistry(
                            self._skill_registry, context_skills
                        )
                        LOGGER.info(
                            "Created CompositeSkillRegistry with %d context skills for context %s",
                            len(context_skills),
                            context_id,
                        )

        # Create context-scoped memory store with shared Qdrant client
        # MemoryStore will filter all searches by this context_id
        memory_store = MemoryStore(
            self._settings,
            context_id=context_id,
            client=self._qdrant_client,
        )
        await memory_store.ainit()

        # Create service with context-scoped dependencies
        service = AgentService(
            settings=self._settings,
            litellm=self._litellm,
            memory=memory_store,
            tool_registry=tool_registry,
            skill_registry=skill_registry_for_context,
        )

        LOGGER.info(
            "Created AgentService for context %s with %d tools",
            context_id,
            len(tool_registry.list_tools()),
        )

        return service

    async def _connect_mcp_background(
        self,
        context_id: UUID,
        pool: object,
    ) -> None:
        """Connect MCP clients in the background so chat requests are never blocked.

        Populates the pool's client cache. Next request for this context
        will find cached clients and register tools instantly.
        """
        from core.db.engine import AsyncSessionLocal
        from core.mcp.client_pool import McpClientPool

        if not isinstance(pool, McpClientPool):
            return

        try:
            async with AsyncSessionLocal() as bg_session:
                await asyncio.wait_for(
                    pool.get_clients(context_id, bg_session),
                    timeout=10.0,
                )
        except TimeoutError:
            LOGGER.warning("Background MCP connect timed out for context %s", context_id)
            pool._negative_cache[context_id] = time.monotonic()
        except Exception as e:
            LOGGER.warning(
                "Background MCP connect failed for context %s: %s",
                context_id,
                e,
            )
            pool._negative_cache[context_id] = time.monotonic()

    @property
    def litellm(self) -> LiteLLMClient:
        """Return the shared LiteLLM client.

        Returns:
            The shared LiteLLMClient instance.
        """
        return self._litellm

    @property
    def skill_registry(self) -> SkillRegistryProtocol | None:
        """Return the shared skill registry.

        Returns:
            The shared skill registry instance, or None if not configured.
        """
        return self._skill_registry

    async def close(self) -> None:
        """Close the shared AsyncQdrantClient.

        This should be called during application shutdown to clean up resources.
        """
        if self._qdrant_client is not None:
            await self._qdrant_client.close()
            LOGGER.info("Closed shared AsyncQdrantClient")


__all__ = ["ServiceFactory"]
