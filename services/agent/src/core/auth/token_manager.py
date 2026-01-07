"""High-level OAuth token management API.

This module provides a simple interface for managing OAuth tokens across
multiple providers. It wraps the OAuth client and handles provider configuration.
"""

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from core.auth.models import OAuthProviderConfig
from core.auth.oauth_client import OAuthClient

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager

    from sqlalchemy.ext.asyncio import AsyncSession

    from core.core.config import Settings

LOGGER = logging.getLogger(__name__)


class TokenManager:
    """High-level OAuth token manager.

    Provides simple interface for multi-provider token management.
    Handles provider configuration and delegates to OAuth client.
    """

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        settings: Settings,
    ):
        """Initialize token manager.

        Args:
            session_factory: Async callable that returns AsyncSession
            settings: Application settings with OAuth configuration
        """
        self._session_factory = session_factory
        self._settings = settings

        # Build provider configurations
        provider_configs = {}

        # Homey OAuth configuration
        if settings.homey_oauth_enabled and settings.homey_client_id:
            if not settings.oauth_redirect_uri:
                LOGGER.warning("Skipping Homey OAuth: oauth_redirect_uri not configured")
            else:
                provider_configs["homey"] = OAuthProviderConfig(
                    provider_name="homey",
                    authorization_url=settings.homey_authorization_url,
                    token_url=settings.homey_token_url,
                    client_id=settings.homey_client_id,
                    client_secret=settings.homey_client_secret,
                    scopes=None,  # Homey doesn't require specific scopes
                    redirect_uri=settings.oauth_redirect_uri,
                )
                LOGGER.info("Configured OAuth provider: homey")

        # Future providers can be added here (GitHub, Google, etc.)

        self._oauth_client = OAuthClient(session_factory, provider_configs)

    async def get_authorization_url(self, provider: str, context_id: UUID) -> tuple[str, str]:
        """Generate OAuth authorization URL for user.

        Args:
            provider: OAuth provider name (e.g., "homey")
            context_id: Context UUID

        Returns:
            Tuple of (authorization_url, state)

        Raises:
            ValueError: If provider not configured
        """
        return await self._oauth_client.get_authorization_url(provider, context_id)

    async def exchange_code_for_token(self, authorization_code: str, state: str) -> None:
        """Exchange authorization code for access token.

        Args:
            authorization_code: Code from OAuth provider callback
            state: State parameter for validation

        Raises:
            OAuthError: If exchange fails
        """
        await self._oauth_client.exchange_code_for_token(authorization_code, state)

    async def get_token(self, provider: str, context_id: UUID) -> str | None:
        """Get valid access token, refreshing if needed.

        Args:
            provider: OAuth provider name
            context_id: Context UUID

        Returns:
            Valid access token or None
        """
        return await self._oauth_client.get_token(provider, context_id)

    async def revoke_token(self, provider: str, context_id: UUID) -> None:
        """Revoke and delete OAuth token.

        Args:
            provider: OAuth provider name
            context_id: Context UUID
        """
        await self._oauth_client.revoke_token(provider, context_id)

    async def shutdown(self) -> None:
        """Cleanup on application shutdown.

        Currently no cleanup needed, but provided for consistency.
        """
        LOGGER.info("TokenManager shutdown complete")
