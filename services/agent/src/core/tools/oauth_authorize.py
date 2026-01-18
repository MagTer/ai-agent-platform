"""OAuth authorization tool for initiating OAuth flows from chat."""

import logging
from typing import Any
from uuid import UUID

from core.providers import get_token_manager
from core.tools.base import Tool

LOGGER = logging.getLogger(__name__)


class OAuthAuthorizeTool(Tool):
    """Tool to initiate OAuth authorization flow for a provider.

    This tool can be called by the agent when authentication is needed
    for external services like Homey.
    """

    def __init__(self, context_id: UUID | None = None, user_id: UUID | None = None):
        self.name = "oauth_authorize"
        self.description = (
            "Initiate OAuth authorization for external services (Homey, GitHub, etc.). "
            "Call this when a service requires authentication. Returns a clickable "
            "authorization link for the user."
        )
        self.parameters = {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "OAuth provider name (e.g., 'homey', 'github')",
                    "enum": ["homey"],  # Add more providers as they're configured
                },
            },
            "required": ["provider"],
        }
        self.category = "oauth"
        self._context_id = context_id
        self._user_id = user_id

    async def run(self, provider: str, **kwargs: Any) -> str:
        """Run OAuth authorization flow.

        Args:
            provider: OAuth provider name (e.g., "homey")

        Returns:
            User-friendly message with authorization link
        """
        if not self._context_id:
            return (
                "❌ OAuth authorization is not available in this context. "
                "Please contact your administrator."
            )

        if not self._user_id:
            return (
                "❌ OAuth authorization requires user authentication. "
                "Please contact your administrator."
            )

        try:
            token_manager = get_token_manager()
            authorization_url, state = await token_manager.get_authorization_url(
                provider=provider.lower(),
                context_id=self._context_id,
                user_id=self._user_id,
            )

            provider_name = provider.capitalize()
            message = (
                f"🔐 **{provider_name} Authorization Required**\n\n"
                f"To authorize {provider_name}, please click the link below:\n\n"
                f"👉 **[Authorize {provider_name}]({authorization_url})**\n\n"
                f"You'll be redirected to {provider_name} to log in and approve access. "
                f"Once you complete the authorization, I'll automatically be able to "
                f"use {provider_name} tools.\n\n"
                f"_Note: The authorization link expires in 10 minutes._"
            )

            LOGGER.info(
                "Generated OAuth authorization for %s (context: %s)",
                provider,
                self._context_id,
            )
            return message

        except ValueError as e:
            LOGGER.error("OAuth authorization failed: %s", e)
            return (
                f"❌ OAuth provider '{provider}' is not configured. "
                f"Please check the server configuration."
            )
        except Exception as e:
            LOGGER.error("Unexpected error during OAuth authorization: %s", e)
            return (
                f"❌ Failed to generate authorization link: {e}\n\n"
                f"Please try again or contact your administrator."
            )


__all__ = ["OAuthAuthorizeTool"]
