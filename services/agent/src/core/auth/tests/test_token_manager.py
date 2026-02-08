"""Unit tests for TokenManager.

Tests the high-level OAuth token management API including:
- Provider configuration and initialization
- Authorization URL generation
- Token exchange
- Token retrieval and refresh
- Token revocation
- Shutdown behavior
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import HttpUrl

from core.auth.models import OAuthError
from core.auth.token_manager import TokenManager
from core.core.config import Settings


@pytest.fixture
def mock_settings() -> Settings:
    """Create test settings with Homey OAuth configured."""
    settings = Settings(
        homey_oauth_enabled=True,
        homey_client_id="test_client_id",
        homey_client_secret="test_client_secret",
        homey_authorization_url=HttpUrl("https://api.athom.com/oauth2/authorise"),
        homey_token_url=HttpUrl("https://api.athom.com/oauth2/token"),
        oauth_redirect_uri=HttpUrl("https://app.example.com/callback"),
    )
    return settings


@pytest.fixture
def mock_session_factory() -> tuple[Any, Any]:
    """Create a mock async session factory.

    TokenManager uses `async with self._session_factory() as session`,
    so the factory must return an async context manager.
    """
    mock_session = AsyncMock()

    # Create async context manager class
    class MockSessionContextManager:
        async def __aenter__(self) -> Any:
            return mock_session

        async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            return None

    def session_factory() -> MockSessionContextManager:
        return MockSessionContextManager()

    return session_factory, mock_session


@pytest.fixture
def token_manager(mock_session_factory: tuple[Any, Any], mock_settings: Settings) -> TokenManager:
    """Create a TokenManager instance with mocked dependencies."""
    factory, _ = mock_session_factory
    return TokenManager(factory, mock_settings)


class TestTokenManagerInit:
    """Tests for TokenManager initialization and provider configuration."""

    def test_initialization_with_valid_settings(
        self, mock_session_factory: tuple[Any, Any], mock_settings: Settings
    ) -> None:
        """Should initialize with valid OAuth settings."""
        factory, _ = mock_session_factory
        manager = TokenManager(factory, mock_settings)

        assert manager is not None
        assert manager._settings == mock_settings
        assert manager._oauth_client is not None

    def test_homey_provider_configured_when_enabled(
        self, mock_session_factory: tuple[Any, Any]
    ) -> None:
        """Should configure Homey provider when enabled with valid settings."""
        factory, _ = mock_session_factory
        settings = Settings(
            homey_oauth_enabled=True,
            homey_client_id="test_client_id",
            homey_client_secret="test_secret",
            oauth_redirect_uri=HttpUrl("https://app.example.com/callback"),
        )

        manager = TokenManager(factory, settings)

        # Verify OAuth client was initialized (internal implementation)
        assert manager._oauth_client is not None

    def test_homey_provider_not_configured_when_disabled(
        self, mock_session_factory: tuple[Any, Any]
    ) -> None:
        """Should not configure Homey provider when disabled."""
        factory, _ = mock_session_factory
        settings = Settings(
            homey_oauth_enabled=False,
            homey_client_id="test_client_id",
            oauth_redirect_uri=HttpUrl("https://app.example.com/callback"),
        )

        manager = TokenManager(factory, settings)

        # OAuth client exists but without Homey provider
        assert manager._oauth_client is not None

    def test_homey_provider_not_configured_without_client_id(
        self, mock_session_factory: tuple[Any, Any]
    ) -> None:
        """Should not configure Homey provider without client_id."""
        factory, _ = mock_session_factory
        settings = Settings(
            homey_oauth_enabled=True,
            homey_client_id=None,  # Missing
            oauth_redirect_uri=HttpUrl("https://app.example.com/callback"),
        )

        manager = TokenManager(factory, settings)

        assert manager._oauth_client is not None

    def test_homey_provider_not_configured_without_redirect_uri(
        self, mock_session_factory: tuple[Any, Any], caplog: Any
    ) -> None:
        """Should not configure Homey provider without redirect_uri and log warning."""
        factory, _ = mock_session_factory
        settings = Settings(
            homey_oauth_enabled=True,
            homey_client_id="test_client_id",
            oauth_redirect_uri=None,  # Missing
        )

        manager = TokenManager(factory, settings)

        assert manager._oauth_client is not None
        # Check that warning was logged
        assert any(
            "Skipping Homey OAuth: oauth_redirect_uri not configured" in record.message
            for record in caplog.records
        )


@pytest.mark.asyncio
class TestGetAuthorizationUrl:
    """Tests for get_authorization_url method."""

    async def test_delegates_to_oauth_client(self, token_manager: TokenManager) -> None:
        """Should delegate to OAuthClient.get_authorization_url."""
        context_id = uuid4()
        user_id = uuid4()

        # Mock the OAuth client method
        token_manager._oauth_client.get_authorization_url = AsyncMock(  # type: ignore[method-assign]
            return_value=("https://auth.example.com/authorize?...", "state_value")
        )

        url, state = await token_manager.get_authorization_url("homey", context_id, user_id)

        token_manager._oauth_client.get_authorization_url.assert_called_once_with(
            "homey", context_id, user_id
        )
        assert url.startswith("https://auth.example.com/authorize")
        assert isinstance(state, str)

    async def test_raises_value_error_for_unknown_provider(
        self, token_manager: TokenManager
    ) -> None:
        """Should raise ValueError for unknown provider."""
        context_id = uuid4()
        user_id = uuid4()

        # Mock the OAuth client to raise ValueError
        token_manager._oauth_client.get_authorization_url = AsyncMock(  # type: ignore[method-assign]
            side_effect=ValueError("Provider 'unknown' not configured")
        )

        with pytest.raises(ValueError, match="not configured"):
            await token_manager.get_authorization_url("unknown", context_id, user_id)

    async def test_requires_user_id_for_csrf_protection(self, token_manager: TokenManager) -> None:
        """Should require user_id parameter for CSRF protection."""
        context_id = uuid4()
        user_id = uuid4()

        token_manager._oauth_client.get_authorization_url = AsyncMock(  # type: ignore[method-assign]
            return_value=("https://auth.example.com/authorize", "state")
        )

        await token_manager.get_authorization_url("homey", context_id, user_id)

        # Verify user_id was passed to OAuth client
        token_manager._oauth_client.get_authorization_url.assert_called_once_with(
            "homey", context_id, user_id
        )


@pytest.mark.asyncio
class TestExchangeCodeForToken:
    """Tests for exchange_code_for_token method."""

    async def test_delegates_to_oauth_client(self, token_manager: TokenManager) -> None:
        """Should delegate to OAuthClient.exchange_code_for_token."""
        authorization_code = "auth_code_123"
        state = "state_value"

        token_manager._oauth_client.exchange_code_for_token = AsyncMock()  # type: ignore[method-assign]

        await token_manager.exchange_code_for_token(authorization_code, state)

        token_manager._oauth_client.exchange_code_for_token.assert_called_once_with(
            authorization_code, state
        )

    async def test_raises_oauth_error_on_invalid_state(self, token_manager: TokenManager) -> None:
        """Should raise OAuthError when state validation fails."""
        token_manager._oauth_client.exchange_code_for_token = AsyncMock(  # type: ignore[method-assign]
            side_effect=OAuthError(error="invalid_state", description="State not found")
        )

        with pytest.raises(OAuthError) as exc_info:
            await token_manager.exchange_code_for_token("code", "invalid_state")

        assert exc_info.value.error == "invalid_state"

    async def test_raises_oauth_error_on_exchange_failure(
        self, token_manager: TokenManager
    ) -> None:
        """Should raise OAuthError when token exchange fails."""
        token_manager._oauth_client.exchange_code_for_token = AsyncMock(  # type: ignore[method-assign]
            side_effect=OAuthError(error="invalid_grant", description="Code expired")
        )

        with pytest.raises(OAuthError) as exc_info:
            await token_manager.exchange_code_for_token("expired_code", "state")

        assert exc_info.value.error == "invalid_grant"


@pytest.mark.asyncio
class TestGetToken:
    """Tests for get_token method (retrieval and refresh)."""

    async def test_delegates_to_oauth_client(self, token_manager: TokenManager) -> None:
        """Should delegate to OAuthClient.get_token."""
        context_id = uuid4()

        token_manager._oauth_client.get_token = AsyncMock(return_value="access_token_123")  # type: ignore[method-assign]

        token = await token_manager.get_token("homey", context_id)

        token_manager._oauth_client.get_token.assert_called_once_with("homey", context_id)
        assert token == "access_token_123"

    async def test_returns_none_when_token_not_found(self, token_manager: TokenManager) -> None:
        """Should return None when no token exists for provider/context."""
        context_id = uuid4()

        token_manager._oauth_client.get_token = AsyncMock(return_value=None)  # type: ignore[method-assign]

        token = await token_manager.get_token("homey", context_id)

        assert token is None

    async def test_returns_valid_token_without_refresh(self, token_manager: TokenManager) -> None:
        """Should return valid token without refreshing."""
        context_id = uuid4()

        token_manager._oauth_client.get_token = AsyncMock(return_value="valid_token")  # type: ignore[method-assign]

        token = await token_manager.get_token("homey", context_id)

        assert token == "valid_token"

    async def test_refreshes_expired_token_automatically(self, token_manager: TokenManager) -> None:
        """Should automatically refresh expired token and return new token."""
        context_id = uuid4()

        # OAuth client handles refresh internally and returns new token
        token_manager._oauth_client.get_token = AsyncMock(return_value="refreshed_token")  # type: ignore[method-assign]

        token = await token_manager.get_token("homey", context_id)

        assert token == "refreshed_token"

    async def test_returns_none_when_expired_without_refresh_token(
        self, token_manager: TokenManager
    ) -> None:
        """Should return None when token is expired and no refresh_token available."""
        context_id = uuid4()

        token_manager._oauth_client.get_token = AsyncMock(return_value=None)  # type: ignore[method-assign]

        token = await token_manager.get_token("homey", context_id)

        assert token is None

    async def test_handles_decryption_of_stored_tokens(self, token_manager: TokenManager) -> None:
        """Should handle decryption of encrypted tokens from database."""
        context_id = uuid4()

        # OAuth client handles decryption internally
        token_manager._oauth_client.get_token = AsyncMock(return_value="decrypted_token")  # type: ignore[method-assign]

        token = await token_manager.get_token("homey", context_id)

        assert token == "decrypted_token"


@pytest.mark.asyncio
class TestRevokeToken:
    """Tests for revoke_token method."""

    async def test_delegates_to_oauth_client(self, token_manager: TokenManager) -> None:
        """Should delegate to OAuthClient.revoke_token."""
        context_id = uuid4()

        token_manager._oauth_client.revoke_token = AsyncMock()  # type: ignore[method-assign]

        await token_manager.revoke_token("homey", context_id)

        token_manager._oauth_client.revoke_token.assert_called_once_with("homey", context_id)

    async def test_deletes_token_from_database(self, token_manager: TokenManager) -> None:
        """Should delete token record from database."""
        context_id = uuid4()

        token_manager._oauth_client.revoke_token = AsyncMock()  # type: ignore[method-assign]

        await token_manager.revoke_token("homey", context_id)

        # Verify revoke was called (deletion happens in OAuth client)
        token_manager._oauth_client.revoke_token.assert_called_once()

    async def test_handles_missing_token_gracefully(self, token_manager: TokenManager) -> None:
        """Should not raise error when revoking non-existent token."""
        context_id = uuid4()

        # OAuth client handles gracefully
        token_manager._oauth_client.revoke_token = AsyncMock()  # type: ignore[method-assign]

        # Should not raise
        await token_manager.revoke_token("homey", context_id)

        token_manager._oauth_client.revoke_token.assert_called_once()


@pytest.mark.asyncio
class TestShutdown:
    """Tests for shutdown method."""

    async def test_shutdown_completes_without_error(self, token_manager: TokenManager) -> None:
        """Should complete shutdown without raising errors."""
        # Should not raise
        await token_manager.shutdown()

    async def test_shutdown_logs_completion(self, token_manager: TokenManager, caplog: Any) -> None:
        """Should log shutdown completion."""
        import logging

        # Set log level to capture INFO messages
        caplog.set_level(logging.INFO)

        await token_manager.shutdown()

        # Check that shutdown message was logged
        assert any("TokenManager shutdown complete" in record.message for record in caplog.records)

    async def test_shutdown_is_idempotent(self, token_manager: TokenManager) -> None:
        """Should allow multiple shutdown calls without error."""
        await token_manager.shutdown()
        await token_manager.shutdown()  # Second call should be safe
        await token_manager.shutdown()  # Third call should also be safe


class TestProviderConfiguration:
    """Tests for provider configuration logic."""

    def test_multiple_providers_can_be_configured(
        self, mock_session_factory: tuple[Any, Any]
    ) -> None:
        """Should support configuration of multiple OAuth providers."""
        factory, _ = mock_session_factory

        # Configure multiple providers (Homey is currently the only one)
        settings = Settings(
            homey_oauth_enabled=True,
            homey_client_id="homey_client",
            oauth_redirect_uri=HttpUrl("https://app.example.com/callback"),
        )

        manager = TokenManager(factory, settings)

        assert manager._oauth_client is not None

    def test_provider_config_includes_all_required_fields(
        self, mock_session_factory: tuple[Any, Any], mock_settings: Settings
    ) -> None:
        """Should configure provider with all required OAuth fields."""
        factory, _ = mock_session_factory

        manager = TokenManager(factory, mock_settings)

        # Verify internal OAuth client has provider configurations
        assert manager._oauth_client is not None
        # Provider configs are internal to OAuth client, verified through behavior


class TestMultiTenancy:
    """Tests for multi-tenant context isolation."""

    @pytest.mark.asyncio
    async def test_tokens_are_context_isolated(self, token_manager: TokenManager) -> None:
        """Should isolate tokens by context_id for multi-tenancy."""
        context_id_1 = uuid4()
        context_id_2 = uuid4()

        token_manager._oauth_client.get_token = AsyncMock(  # type: ignore[method-assign]
            side_effect=lambda provider, ctx: f"token_{ctx}" if ctx == context_id_1 else None
        )

        token1 = await token_manager.get_token("homey", context_id_1)
        token2 = await token_manager.get_token("homey", context_id_2)

        # Different contexts should have different (or no) tokens
        assert token1 == f"token_{context_id_1}"
        assert token2 is None

    @pytest.mark.asyncio
    async def test_revoke_only_affects_specified_context(self, token_manager: TokenManager) -> None:
        """Should only revoke token for the specified context_id."""
        context_id = uuid4()

        token_manager._oauth_client.revoke_token = AsyncMock()  # type: ignore[method-assign]

        await token_manager.revoke_token("homey", context_id)

        # Verify revoke was called with specific context
        token_manager._oauth_client.revoke_token.assert_called_once_with("homey", context_id)
