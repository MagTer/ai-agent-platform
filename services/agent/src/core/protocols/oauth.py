"""Protocol for OAuth 2.0 client services."""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class IOAuthClient(Protocol):
    """Abstract interface for OAuth 2.0 client implementations.

    This protocol defines the contract for OAuth 2.0 Authorization Code Grant
    with PKCE. Implementations handle authorization URL generation, token exchange,
    and automatic token refresh.
    """

    async def get_authorization_url(
        self, provider: str, context_id: UUID, user_id: UUID
    ) -> tuple[str, str]:
        """Generate OAuth authorization URL for user to visit.

        Creates PKCE parameters (code_verifier, code_challenge) and stores them
        in the database for later token exchange. Returns authorization URL with
        state parameter for CSRF protection.

        Args:
            provider: OAuth provider name (e.g., "homey", "github")
            context_id: Context UUID for multi-tenant isolation
            user_id: User UUID (REQUIRED for CSRF protection)

        Returns:
            Tuple of (authorization_url, state) where:
                - authorization_url: URL for user to visit
                - state: Random state string for CSRF protection

        Raises:
            ValueError: If provider configuration not found
        """
        ...

    async def exchange_code_for_token(self, authorization_code: str, state: str) -> None:
        """Exchange authorization code for access token.

        Validates state parameter (CSRF protection), exchanges code for tokens,
        and stores tokens in database.

        Args:
            authorization_code: Code from OAuth provider callback
            state: State parameter from callback (for validation)

        Raises:
            OAuthError: If state invalid, code expired, or exchange fails
        """
        ...

    async def get_token(self, provider: str, context_id: UUID) -> str | None:
        """Get valid access token, refreshing if needed.

        Retrieves token from database and checks expiration. If expired,
        automatically refreshes using refresh_token. Returns None if no token
        exists or refresh fails.

        Args:
            provider: OAuth provider name
            context_id: Context UUID

        Returns:
            Valid access token or None if unavailable
        """
        ...

    async def revoke_token(self, provider: str, context_id: UUID) -> None:
        """Revoke and delete OAuth token.

        Removes token from database. Does not call provider's revocation endpoint
        (provider-specific behavior).

        Args:
            provider: OAuth provider name
            context_id: Context UUID
        """
        ...


__all__ = ["IOAuthClient"]
