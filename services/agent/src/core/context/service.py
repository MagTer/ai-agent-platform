"""Shared context resolution for all platform adapters.

Consolidates the duplicated context creation logic from OpenWebUI adapter,
Telegram adapter, and the /v1/agent endpoint into a single service.

Each method preserves the exact Context.name patterns used by the original
code so existing database records remain valid.
"""

import logging
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.header_auth import UserIdentity
from core.auth.user_service import get_or_create_user, get_user_default_context
from core.db.models import Context, Conversation

LOGGER = logging.getLogger(__name__)


class ContextService:
    """Resolves or creates Context records for different adapter scenarios."""

    @staticmethod
    async def resolve_for_authenticated_user(identity: UserIdentity, session: AsyncSession) -> UUID:
        """Resolve context for an authenticated user (e.g. OpenWebUI with headers).

        Flow: get_or_create_user -> get_user_default_context -> fallback create.
        Preserves naming: ``personal_{user_id}``.
        """
        user = await get_or_create_user(identity, session)
        context = await get_user_default_context(user, session)
        if context:
            LOGGER.debug("Using personal context %s for user %s", context.id, user.email)
            return context.id

        # Fallback: create context if somehow missing (shouldn't happen)
        LOGGER.warning("User %s has no default context, creating one", user.email)
        context = Context(
            name=f"personal_{user.id}",
            type="personal",
            config={"owner_email": user.email},
            default_cwd="/tmp",  # noqa: S108
        )
        session.add(context)
        await session.flush()
        return context.id

    @staticmethod
    async def resolve_for_platform(platform: str, platform_id: str, session: AsyncSession) -> UUID:
        """Resolve context for a platform conversation (Telegram, API).

        Looks up Conversation by (platform, platform_id). If not found,
        creates a new Context. Preserves naming: ``{platform}_{platform_id}``.
        """
        stmt = select(Conversation).where(
            Conversation.platform == platform,
            Conversation.platform_id == platform_id,
        )
        result = await session.execute(stmt)
        conversation = result.scalar_one_or_none()

        if conversation:
            LOGGER.debug(
                "Found existing %s conversation, context_id=%s",
                platform,
                conversation.context_id,
            )
            return conversation.context_id

        context = Context(
            name=f"{platform}_{platform_id}",
            type="virtual",
            config={"platform": platform, "chat_id": platform_id},
            default_cwd="/tmp",  # noqa: S108
        )
        session.add(context)
        await session.flush()
        LOGGER.info("Created new context for %s chat %s: %s", platform, platform_id, context.id)
        return context.id

    @staticmethod
    async def resolve_for_conversation_id(
        conversation_id_str: str, platform: str, session: AsyncSession
    ) -> UUID:
        """Resolve context from a conversation UUID string (OpenWebUI-style).

        Tries to parse the string as UUID, looks up Conversation, then falls
        back to creating a Context. Preserves naming: ``{platform}_{uuid}``.
        """
        try:
            conversation_uuid = UUID(conversation_id_str)
        except ValueError:
            LOGGER.warning(
                "Invalid conversation_id format: %s, creating new context",
                conversation_id_str,
            )
            return await ContextService.resolve_anonymous(platform, session)

        # Look up existing conversation
        stmt = select(Conversation).where(Conversation.id == conversation_uuid)
        result = await session.execute(stmt)
        conversation = result.scalar_one_or_none()

        if conversation:
            LOGGER.debug(
                "Found existing conversation %s, context_id=%s",
                conversation_uuid,
                conversation.context_id,
            )
            return conversation.context_id

        # No conversation yet -- check if context already exists (handles retries)
        ctx_name = f"{platform}_{conversation_uuid}"
        ctx_stmt = select(Context).where(Context.name == ctx_name)
        ctx_result = await session.execute(ctx_stmt)
        context = ctx_result.scalar_one_or_none()

        if not context:
            context = Context(
                name=ctx_name,
                type="virtual",
                config={"platform": platform, "conversation_id": str(conversation_uuid)},
                default_cwd="/tmp",  # noqa: S108
            )
            session.add(context)
            await session.flush()
            LOGGER.info(
                "Created new context for conversation %s: %s", conversation_uuid, context.id
            )
        else:
            LOGGER.debug(
                "Reusing existing context %s for conversation %s", context.id, conversation_uuid
            )

        return context.id

    @staticmethod
    async def resolve_anonymous(platform: str, session: AsyncSession) -> UUID:
        """Create an ephemeral context when there is no identity or conversation.

        Preserves naming: ``{platform}_{random_uuid}``.
        """
        context = Context(
            name=f"{platform}_{uuid4()}",
            type="virtual",
            config={},
            default_cwd="/tmp",  # noqa: S108
        )
        session.add(context)
        await session.flush()
        LOGGER.info("Created anonymous context for %s: %s", platform, context.id)
        return context.id
