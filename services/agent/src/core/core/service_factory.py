"""Factory for creating context-aware AgentService instances."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.core.config import Settings
from core.core.litellm_client import LiteLLMClient
from core.core.memory import MemoryStore
from core.core.service import AgentService
from core.db.models import ToolPermission
from core.tools.loader import load_tool_registry

if TYPE_CHECKING:
    from core.skills import SkillRegistry

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
        skill_registry: SkillRegistry | None = None,
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
        5. Creates context-scoped MemoryStore
        6. Returns fully configured AgentService

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
        # This uses OAuth tokens to create context-specific MCP clients.
        # Capped at 5s to avoid blocking chat requests when MCP servers are unreachable.
        # Failed attempts are cached to avoid retry storms.
        from core.tools.mcp_loader import get_mcp_client_pool, load_mcp_tools_for_context

        try:
            pool = get_mcp_client_pool()
            # Check negative cache before attempting (avoids 5s timeout on every request)
            if context_id not in pool._negative_cache or (
                time.monotonic() - pool._negative_cache[context_id] >= pool._negative_cache_ttl
            ):
                await asyncio.wait_for(
                    load_mcp_tools_for_context(
                        context_id=context_id,
                        tool_registry=tool_registry,
                        session=session,
                        settings=self._settings,
                    ),
                    timeout=5.0,
                )
        except TimeoutError:
            LOGGER.warning("MCP tool loading timed out for context %s (5s cap)", context_id)
            # Set negative cache so we don't retry on next request
            try:
                pool = get_mcp_client_pool()
                pool._negative_cache[context_id] = time.monotonic()
            except RuntimeError:
                pass
        except RuntimeError:
            # MCP pool not initialized - skip silently
            pass
        except Exception as e:
            # Don't fail service creation if MCP loading fails
            LOGGER.warning(f"Failed to load MCP tools for context {context_id}: {e}")

        # Create context-scoped memory store
        # MemoryStore will filter all searches by this context_id
        memory_store = MemoryStore(self._settings, context_id=context_id)
        await memory_store.ainit()

        # Create service with context-scoped dependencies
        service = AgentService(
            settings=self._settings,
            litellm=self._litellm,
            memory=memory_store,
            tool_registry=tool_registry,
            skill_registry=self._skill_registry,
        )

        LOGGER.info(
            "Created AgentService for context %s with %d tools",
            context_id,
            len(tool_registry.list_tools()),
        )

        return service


__all__ = ["ServiceFactory"]
