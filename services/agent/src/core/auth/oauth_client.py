"""OAuth 2.0 Authorization Code Grant client with PKCE.

This module implements OAuth 2.0 Authorization Code Grant (RFC 6749) with PKCE
(RFC 7636) for secure authentication without client secrets.
"""

import base64
import hashlib
import logging
import secrets
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    pass

from core.auth.models import OAuthError, OAuthProviderConfig, TokenResponse
from core.db.oauth_models import OAuthState, OAuthToken

LOGGER = logging.getLogger(__name__)


class OAuthClient:
    """OAuth 2.0 client with PKCE support.

    Implements Authorization Code Grant flow with PKCE for secure authentication.
    Stores tokens in database and handles automatic refresh.
    """

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        provider_configs: dict[str, OAuthProviderConfig],
    ):
        """Initialize OAuth client.

        Args:
            session_factory: Async callable that returns AsyncSession
            provider_configs: Dict mapping provider name to configuration
        """
        self._session_factory = session_factory
        self._provider_configs = provider_configs

    def _get_provider_config(self, provider: str) -> OAuthProviderConfig:
        """Get provider configuration.

        Args:
            provider: Provider name

        Returns:
            Provider configuration

        Raises:
            ValueError: If provider not configured
        """
        config = self._provider_configs.get(provider)
        if not config:
            raise ValueError(f"OAuth provider '{provider}' not configured")
        return config

    @staticmethod
    def _generate_pkce_params() -> tuple[str, str]:
        """Generate PKCE code verifier and challenge.

        Returns:
            Tuple of (code_verifier, code_challenge)
        """
        # Generate code_verifier (43-128 chars)
        code_verifier = secrets.token_urlsafe(64)

        # Generate code_challenge using S256 method
        code_challenge = hashlib.sha256(code_verifier.encode()).digest()
        code_challenge_b64 = base64.urlsafe_b64encode(code_challenge).decode().rstrip("=")

        return code_verifier, code_challenge_b64

    async def get_authorization_url(
        self, provider: str, context_id: UUID, user_id: UUID
    ) -> tuple[str, str]:
        """Generate OAuth authorization URL with PKCE.

        Creates PKCE parameters and state, stores them in database, and returns
        authorization URL for user to visit.

        Args:
            provider: OAuth provider name
            context_id: Context UUID
            user_id: User UUID (REQUIRED for CSRF protection)

        Returns:
            Tuple of (authorization_url, state)

        Raises:
            ValueError: If provider not configured
        """
        config = self._get_provider_config(provider)

        # Generate PKCE parameters
        code_verifier, code_challenge = self._generate_pkce_params()

        # Generate state for CSRF protection
        state = secrets.token_urlsafe(32)

        # Store state and code_verifier in database (expires in 10 minutes)
        async with self._session_factory() as session:
            oauth_state = OAuthState(
                state=state,
                context_id=context_id,
                user_id=user_id,
                provider=provider,
                code_verifier=code_verifier,
                expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=10),
            )
            session.add(oauth_state)
            await session.commit()

        # Build authorization URL
        params: dict[str, Any] = {
            "client_id": config.client_id,
            "redirect_uri": str(config.redirect_uri),
            "response_type": "code",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        if config.scopes:
            params["scope"] = config.scopes

        authorization_url = f"{config.authorization_url}?{urlencode(params)}"

        LOGGER.info(
            "Generated OAuth authorization URL",
            extra={
                "provider": provider,
                "context_id": str(context_id),
                "user_id": str(user_id),
            },
        )

        return authorization_url, state

    async def exchange_code_for_token(self, authorization_code: str, state: str) -> None:
        """Exchange authorization code for access token.

        Validates state, retrieves code_verifier, exchanges code for tokens,
        and stores tokens in database.

        Args:
            authorization_code: Authorization code from provider
            state: State parameter for validation

        Raises:
            OAuthError: If state invalid, code expired, or exchange fails
        """
        async with self._session_factory() as session:
            # Retrieve and validate state
            oauth_state = await session.get(OAuthState, state)

            if not oauth_state:
                raise OAuthError("invalid_state", "State parameter not found or expired")

            if oauth_state.expires_at < datetime.now(UTC).replace(tzinfo=None):
                await session.delete(oauth_state)
                await session.commit()
                raise OAuthError("invalid_state", "State parameter expired")

            provider = oauth_state.provider
            context_id = oauth_state.context_id
            user_id = oauth_state.user_id
            code_verifier = oauth_state.code_verifier

            # Get provider config
            config = self._get_provider_config(provider)

            # Exchange authorization code for tokens
            token_data = {
                "grant_type": "authorization_code",
                "code": authorization_code,
                "redirect_uri": str(config.redirect_uri),
                "client_id": config.client_id,
                "code_verifier": code_verifier,
            }

            if config.client_secret:
                token_data["client_secret"] = config.client_secret

            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        str(config.token_url),
                        data=token_data,
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                        timeout=30.0,
                    )
                    response.raise_for_status()
                    token_response_data = response.json()

            except httpx.HTTPStatusError as e:
                error_data = e.response.json() if e.response else {}
                error_code = error_data.get("error", "token_exchange_failed")
                error_desc = error_data.get("error_description", str(e))
                LOGGER.error(
                    "OAuth token exchange failed",
                    extra={"provider": provider, "error": error_code},
                )
                raise OAuthError(error_code, error_desc) from e
            except Exception as e:
                LOGGER.error(
                    "OAuth token exchange network error",
                    extra={"provider": provider, "error": str(e)},
                )
                raise OAuthError("network_error", f"Failed to contact OAuth provider: {e}") from e

            # Parse token response
            token_response = TokenResponse(**token_response_data)

            # Delete or update existing token for this user/context
            if user_id:
                stmt = select(OAuthToken).where(
                    OAuthToken.context_id == context_id,
                    OAuthToken.provider == provider,
                    OAuthToken.user_id == user_id,
                )
            else:
                stmt = select(OAuthToken).where(
                    OAuthToken.context_id == context_id,
                    OAuthToken.provider == provider,
                    OAuthToken.user_id.is_(None),
                )
            result = await session.execute(stmt)
            existing_token = result.scalar_one_or_none()

            now = datetime.now(UTC).replace(tzinfo=None)
            expires_at = now + timedelta(seconds=token_response.expires_in)

            if existing_token:
                # Update existing token
                existing_token.access_token = token_response.access_token
                existing_token.refresh_token = token_response.refresh_token
                existing_token.token_type = token_response.token_type
                existing_token.expires_at = expires_at
                existing_token.scope = token_response.scope
                existing_token.updated_at = datetime.now(UTC).replace(tzinfo=None)
            else:
                # Create new token
                new_token = OAuthToken(
                    context_id=context_id,
                    user_id=user_id,
                    provider=provider,
                    access_token=token_response.access_token,
                    refresh_token=token_response.refresh_token,
                    token_type=token_response.token_type,
                    expires_at=expires_at,
                    scope=token_response.scope,
                )
                session.add(new_token)

            # Delete used state
            await session.delete(oauth_state)
            await session.commit()

            LOGGER.info(
                "OAuth token stored successfully",
                extra={
                    "provider": provider,
                    "context_id": str(context_id),
                    "user_id": str(user_id) if user_id else None,
                },
            )

    async def get_token(
        self, provider: str, context_id: UUID, user_id: UUID | None = None
    ) -> str | None:
        """Get valid access token, refreshing if needed.

        Retrieves token from database and checks expiration. If expired and
        refresh_token exists, automatically refreshes the token.

        Supports both user-specific and context-level tokens. If user_id is provided,
        prefers user-specific token but falls back to context-level token.

        Args:
            provider: OAuth provider name
            context_id: Context UUID
            user_id: Optional user UUID for user-specific tokens

        Returns:
            Valid access token or None if unavailable
        """
        async with self._session_factory() as session:
            token = None

            # First try user-specific token if user_id provided
            if user_id:
                stmt = select(OAuthToken).where(
                    OAuthToken.context_id == context_id,
                    OAuthToken.provider == provider,
                    OAuthToken.user_id == user_id,
                )
                result = await session.execute(stmt)
                token = result.scalar_one_or_none()

            # Fall back to context-level token (user_id is NULL)
            if not token:
                stmt = select(OAuthToken).where(
                    OAuthToken.context_id == context_id,
                    OAuthToken.provider == provider,
                    OAuthToken.user_id.is_(None),
                )
                result = await session.execute(stmt)
                token = result.scalar_one_or_none()

            if not token:
                return None

            # Check if token expired (with 60s buffer)
            now = datetime.now(UTC).replace(tzinfo=None)
            if now >= token.expires_at - timedelta(seconds=60):
                if token.refresh_token:
                    # Attempt refresh
                    try:
                        await self._refresh_token(session, token, provider)
                        await session.commit()
                        # Return refreshed token
                        return token.access_token
                    except Exception as e:
                        LOGGER.warning(
                            "Token refresh failed",
                            extra={"provider": provider, "error": str(e)},
                        )
                        return None
                else:
                    # No refresh token, token invalid
                    return None

            return token.access_token

    async def _refresh_token(self, session: AsyncSession, token: OAuthToken, provider: str) -> None:
        """Refresh expired access token.

        Args:
            session: Database session
            token: Token to refresh (modified in place)
            provider: OAuth provider name

        Raises:
            OAuthError: If refresh fails
        """
        config = self._get_provider_config(provider)

        refresh_data = {
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
            "client_id": config.client_id,
        }

        if config.client_secret:
            refresh_data["client_secret"] = config.client_secret

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    str(config.token_url),
                    data=refresh_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=30.0,
                )
                response.raise_for_status()
                token_response_data = response.json()

        except httpx.HTTPStatusError as e:
            error_data = e.response.json() if e.response else {}
            error_code = error_data.get("error", "refresh_failed")
            error_desc = error_data.get("error_description", str(e))
            LOGGER.error(
                "OAuth token refresh failed",
                extra={"provider": provider, "error": error_code},
            )
            raise OAuthError(error_code, error_desc) from e
        except Exception as e:
            LOGGER.error(
                "OAuth token refresh network error",
                extra={"provider": provider, "error": str(e)},
            )
            raise OAuthError("network_error", f"Failed to contact OAuth provider: {e}") from e

        # Parse token response
        token_response = TokenResponse(**token_response_data)

        # Update token in place
        token.access_token = token_response.access_token
        if token_response.refresh_token:
            token.refresh_token = token_response.refresh_token
        now = datetime.now(UTC).replace(tzinfo=None)
        token.expires_at = now + timedelta(seconds=token_response.expires_in)
        token.updated_at = now

        LOGGER.info(
            "OAuth token refreshed successfully",
            extra={"provider": provider, "context_id": str(token.context_id)},
        )

    async def revoke_token(
        self, provider: str, context_id: UUID, user_id: UUID | None = None
    ) -> None:
        """Revoke and delete OAuth token from database.

        Args:
            provider: OAuth provider name
            context_id: Context UUID
            user_id: Optional user UUID for user-specific tokens
        """
        async with self._session_factory() as session:
            # Revoke user-specific token if user_id provided
            if user_id:
                stmt = select(OAuthToken).where(
                    OAuthToken.context_id == context_id,
                    OAuthToken.provider == provider,
                    OAuthToken.user_id == user_id,
                )
            else:
                # Revoke context-level token
                stmt = select(OAuthToken).where(
                    OAuthToken.context_id == context_id,
                    OAuthToken.provider == provider,
                    OAuthToken.user_id.is_(None),
                )
            result = await session.execute(stmt)
            token = result.scalar_one_or_none()

            if token:
                await session.delete(token)
                await session.commit()
                LOGGER.info(
                    "OAuth token revoked",
                    extra={
                        "provider": provider,
                        "context_id": str(context_id),
                        "user_id": str(user_id) if user_id else None,
                    },
                )
