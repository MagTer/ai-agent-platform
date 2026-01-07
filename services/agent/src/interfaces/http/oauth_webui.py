"""OAuth endpoints optimized for Open WebUI integration."""

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.db.models import Conversation
from core.db.oauth_models import OAuthToken
from core.providers import get_token_manager

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/webui/oauth", tags=["oauth-webui"])


class OAuthStatusResponse(BaseModel):
    """OAuth status for a conversation."""

    provider: str
    is_authorized: bool
    authorization_url: str | None = None
    message: str


class InitiateOAuthRequest(BaseModel):
    """Request to initiate OAuth for current conversation."""

    conversation_id: str
    provider: str


class InitiateOAuthResponse(BaseModel):
    """Response with authorization URL."""

    authorization_url: str
    message: str


async def get_context_id_from_conversation(
    conversation_id: str, session: AsyncSession
) -> UUID | None:
    """Look up context_id from conversation_id.

    Args:
        conversation_id: Conversation/chat ID from OpenWebUI
        session: Database session

    Returns:
        Context UUID or None if not found
    """
    try:
        conversation_uuid = UUID(conversation_id)
    except ValueError:
        LOGGER.warning(f"Invalid conversation_id format: {conversation_id}")
        return None

    stmt = select(Conversation).where(Conversation.id == conversation_uuid)
    result = await session.execute(stmt)
    conversation = result.scalar_one_or_none()

    if conversation:
        return conversation.context_id

    LOGGER.warning(f"Conversation not found: {conversation_id}")
    return None


@router.get("/status/{conversation_id}/{provider}")
async def get_oauth_status(
    conversation_id: str,
    provider: str,
    session: AsyncSession = Depends(get_db),
) -> OAuthStatusResponse:
    """Check OAuth authorization status for a provider in this conversation.

    This endpoint checks if the user has already authorized the provider
    and returns the status along with an authorization URL if needed.

    Args:
        conversation_id: OpenWebUI conversation/chat ID
        provider: OAuth provider name (e.g., "homey")
        session: Database session

    Returns:
        OAuth status with authorization URL if not authorized
    """
    # Get context_id from conversation
    context_id = await get_context_id_from_conversation(conversation_id, session)

    if not context_id:
        return OAuthStatusResponse(
            provider=provider,
            is_authorized=False,
            authorization_url=None,
            message="⚠️ Unable to determine user context. Please start a new conversation.",
        )

    # Check if token exists and is valid
    stmt = select(OAuthToken).where(
        OAuthToken.context_id == context_id,
        OAuthToken.provider == provider.lower(),
    )
    result = await session.execute(stmt)
    token = result.scalar_one_or_none()

    if token:
        # Token exists - check if valid
        try:
            token_manager = get_token_manager()
            access_token = await token_manager.get_token(provider.lower(), context_id)

            if access_token:
                return OAuthStatusResponse(
                    provider=provider,
                    is_authorized=True,
                    message=f"✅ {provider.capitalize()} is already authorized!",
                )
        except Exception as e:
            LOGGER.warning(f"Error checking token validity: {e}")

    # Not authorized - generate authorization URL
    try:
        token_manager = get_token_manager()
        authorization_url, _ = await token_manager.get_authorization_url(
            provider=provider.lower(),
            context_id=context_id,
        )

        provider_name = provider.capitalize()
        return OAuthStatusResponse(
            provider=provider,
            is_authorized=False,
            authorization_url=authorization_url,
            message=(
                f"🔐 {provider_name} authorization required. " f"Click the link to authorize."
            ),
        )

    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' is not configured: {e}",
        ) from e
    except Exception as e:
        LOGGER.error(f"Failed to generate authorization URL: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate authorization link: {e}",
        ) from e


@router.post("/authorize")
async def initiate_oauth(
    request: InitiateOAuthRequest,
    session: AsyncSession = Depends(get_db),
) -> InitiateOAuthResponse:
    """Initiate OAuth authorization flow for a provider.

    This endpoint is called from the WebUI when the user wants to authorize
    a provider. It generates an authorization URL and returns it.

    Args:
        request: OAuth initiation request with conversation_id and provider
        session: Database session

    Returns:
        Authorization URL and user-friendly message
    """
    # Get context_id from conversation
    context_id = await get_context_id_from_conversation(request.conversation_id, session)

    if not context_id:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found. Please start a new conversation.",
        )

    try:
        token_manager = get_token_manager()
        authorization_url, _ = await token_manager.get_authorization_url(
            provider=request.provider.lower(),
            context_id=context_id,
        )

        provider_name = request.provider.capitalize()
        message = (
            f"🔐 **{provider_name} Authorization**\n\n"
            f"Click the link below to authorize {provider_name}:\n\n"
            f"[Authorize {provider_name}]({authorization_url})\n\n"
            f"You'll be redirected to {provider_name} to log in and approve access. "
            f"Once complete, return here and your tools will work!"
        )

        LOGGER.info(
            f"Initiated OAuth for {request.provider} (conversation: {request.conversation_id}, "
            f"context: {context_id})"
        )

        return InitiateOAuthResponse(
            authorization_url=authorization_url,
            message=message,
        )

    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{request.provider}' is not configured: {e}",
        ) from e
    except Exception as e:
        LOGGER.error(f"Failed to initiate OAuth: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate authorization link: {e}",
        ) from e


@router.get("/providers")
async def list_oauth_providers() -> dict[str, Any]:
    """List all configured OAuth providers.

    Returns:
        List of available OAuth provider names
    """
    try:
        # Get configured providers from token manager
        # For now, we know Homey is configured if OAuth is enabled
        # In the future, this could dynamically check the token manager's provider configs

        providers = ["homey"]  # Add more as they're configured

        return {
            "providers": providers,
            "message": f"Found {len(providers)} configured OAuth provider(s)",
        }

    except Exception as e:
        LOGGER.error(f"Failed to list OAuth providers: {e}")
        return {"providers": [], "message": f"Error listing providers: {e}"}
