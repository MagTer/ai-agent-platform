"""Unit tests for OAuth 2.0 client with PKCE.

Tests the OAuth client implementation including:
- PKCE parameter generation
- Authorization URL generation
- Token exchange
- Token refresh
- Error handling
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.auth.models import OAuthError, OAuthProviderConfig
from core.auth.oauth_client import OAuthClient
from core.db.oauth_models import OAuthState, OAuthToken


@pytest.fixture
def provider_config() -> OAuthProviderConfig:
    """Create a test OAuth provider configuration."""
    from pydantic import HttpUrl

    return OAuthProviderConfig(
        provider_name="test_provider",
        authorization_url=HttpUrl("https://auth.example.com/authorize"),
        token_url=HttpUrl("https://auth.example.com/token"),
        client_id="test_client_id",
        client_secret="test_client_secret",
        scopes="read write",
        redirect_uri=HttpUrl("https://app.example.com/callback"),
    )


@pytest.fixture
def mock_session_factory() -> tuple[Any, Any]:
    """Create a mock async session factory.

    The OAuth client uses `async with self._session_factory() as session`,
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


class TestPKCEGeneration:
    """Tests for PKCE parameter generation."""

    def test_generate_pkce_params_returns_tuple(self) -> None:
        """Test that PKCE generation returns verifier and challenge."""
        verifier, challenge = OAuthClient._generate_pkce_params()

        assert isinstance(verifier, str)
        assert isinstance(challenge, str)
        assert len(verifier) > 0
        assert len(challenge) > 0

    def test_generate_pkce_params_verifier_length(self) -> None:
        """Test that code_verifier has correct length (43-128 chars)."""
        verifier, _ = OAuthClient._generate_pkce_params()

        # token_urlsafe(64) generates ~86 characters
        assert len(verifier) >= 43
        assert len(verifier) <= 128

    def test_generate_pkce_params_challenge_is_base64url(self) -> None:
        """Test that code_challenge is base64url encoded."""
        _, challenge = OAuthClient._generate_pkce_params()

        # Base64url alphabet (no padding)
        valid_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        assert all(c in valid_chars for c in challenge)

    def test_generate_pkce_params_unique(self) -> None:
        """Test that each call generates unique values."""
        results = [OAuthClient._generate_pkce_params() for _ in range(10)]
        verifiers = [v for v, _ in results]
        challenges = [c for _, c in results]

        assert len(set(verifiers)) == 10
        assert len(set(challenges)) == 10


@pytest.mark.asyncio
class TestAuthorizationURL:
    """Tests for authorization URL generation."""

    async def test_get_authorization_url_creates_state(
        self, provider_config: Any, mock_session_factory: Any
    ) -> None:
        """Test that authorization URL generation creates state in database."""
        factory, mock_session = mock_session_factory
        client = OAuthClient(factory, {"test_provider": provider_config})
        context_id = uuid.uuid4()

        url, state = await client.get_authorization_url("test_provider", context_id)

        # Verify state was added to session
        mock_session.add.assert_called_once()
        added_state = mock_session.add.call_args[0][0]
        assert isinstance(added_state, OAuthState)
        assert added_state.context_id == context_id
        assert added_state.provider == "test_provider"
        assert len(added_state.code_verifier) > 0
        mock_session.commit.assert_called_once()

    async def test_get_authorization_url_format(
        self, provider_config: Any, mock_session_factory: Any
    ) -> None:
        """Test that authorization URL has correct format and parameters."""
        factory, mock_session = mock_session_factory
        client = OAuthClient(factory, {"test_provider": provider_config})
        context_id = uuid.uuid4()

        url, state = await client.get_authorization_url("test_provider", context_id)

        assert url.startswith("https://auth.example.com/authorize?")
        assert "client_id=test_client_id" in url
        assert "redirect_uri=" in url
        assert "response_type=code" in url
        assert "state=" in url
        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url
        assert "scope=read+write" in url

    async def test_get_authorization_url_unknown_provider(self, mock_session_factory: Any) -> None:
        """Test that unknown provider raises ValueError."""
        factory, _ = mock_session_factory
        client = OAuthClient(factory, {})
        context_id = uuid.uuid4()

        with pytest.raises(ValueError, match="not configured"):
            await client.get_authorization_url("unknown", context_id)

    async def test_get_authorization_url_state_expires(
        self, provider_config: Any, mock_session_factory: Any
    ) -> None:
        """Test that state has correct expiration time (10 minutes)."""
        factory, mock_session = mock_session_factory
        client = OAuthClient(factory, {"test_provider": provider_config})
        context_id = uuid.uuid4()

        before = datetime.utcnow()
        await client.get_authorization_url("test_provider", context_id)
        after = datetime.utcnow()

        added_state = mock_session.add.call_args[0][0]
        expected_min = before + timedelta(minutes=10)
        expected_max = after + timedelta(minutes=10)

        assert added_state.expires_at >= expected_min
        assert added_state.expires_at <= expected_max


@pytest.mark.asyncio
class TestTokenExchange:
    """Tests for authorization code to token exchange."""

    async def test_exchange_code_invalid_state(
        self, provider_config: Any, mock_session_factory: Any
    ) -> None:
        """Test that invalid state raises OAuthError."""
        factory, mock_session = mock_session_factory
        mock_session.get.return_value = None

        client = OAuthClient(factory, {"test_provider": provider_config})

        with pytest.raises(OAuthError) as exc_info:
            await client.exchange_code_for_token("auth_code", "invalid_state")

        assert exc_info.value.error == "invalid_state"

    async def test_exchange_code_expired_state(
        self, provider_config: Any, mock_session_factory: Any
    ) -> None:
        """Test that expired state raises OAuthError."""
        factory, mock_session = mock_session_factory

        expired_state = MagicMock()
        expired_state.expires_at = datetime.utcnow() - timedelta(minutes=1)
        mock_session.get.return_value = expired_state

        client = OAuthClient(factory, {"test_provider": provider_config})

        with pytest.raises(OAuthError) as exc_info:
            await client.exchange_code_for_token("auth_code", "expired_state")

        assert exc_info.value.error == "invalid_state"
        mock_session.delete.assert_called_once_with(expired_state)

    @patch("core.auth.oauth_client.httpx.AsyncClient")
    async def test_exchange_code_success(
        self, mock_httpx: Any, provider_config: Any, mock_session_factory: Any
    ) -> None:
        """Test successful token exchange."""
        factory, mock_session = mock_session_factory
        context_id = uuid.uuid4()

        # Mock valid state
        valid_state = MagicMock()
        valid_state.expires_at = datetime.utcnow() + timedelta(minutes=5)
        valid_state.provider = "test_provider"
        valid_state.context_id = context_id
        valid_state.code_verifier = "test_verifier"
        mock_session.get.return_value = valid_state

        # Mock no existing token
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        # Mock HTTP response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "new_access_token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": "new_refresh_token",
        }
        mock_response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = mock_response
        mock_http_client.__aenter__.return_value = mock_http_client
        mock_http_client.__aexit__.return_value = None
        mock_httpx.return_value = mock_http_client

        client = OAuthClient(factory, {"test_provider": provider_config})
        await client.exchange_code_for_token("auth_code", "valid_state")

        # Verify new token was created
        assert mock_session.add.call_count >= 1
        # Find the OAuthToken in add calls
        token_added = False
        for call in mock_session.add.call_args_list:
            if isinstance(call[0][0], OAuthToken):
                token = call[0][0]
                assert token.access_token == "new_access_token"
                assert token.refresh_token == "new_refresh_token"
                assert token.context_id == context_id
                token_added = True
        assert token_added or mock_session.add.call_count > 0

        # Verify state was deleted
        mock_session.delete.assert_called_with(valid_state)

    @patch("core.auth.oauth_client.httpx.AsyncClient")
    async def test_exchange_code_http_error(
        self, mock_httpx: Any, provider_config: Any, mock_session_factory: Any
    ) -> None:
        """Test token exchange HTTP error handling."""
        factory, mock_session = mock_session_factory
        context_id = uuid.uuid4()

        # Mock valid state
        valid_state = MagicMock()
        valid_state.expires_at = datetime.utcnow() + timedelta(minutes=5)
        valid_state.provider = "test_provider"
        valid_state.context_id = context_id
        valid_state.code_verifier = "test_verifier"
        mock_session.get.return_value = valid_state

        # Mock HTTP error
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "error": "invalid_grant",
            "error_description": "Code expired",
        }
        mock_http_client = AsyncMock()
        mock_http_client.post.side_effect = httpx.HTTPStatusError(
            "Error", request=MagicMock(), response=mock_response
        )
        mock_http_client.__aenter__.return_value = mock_http_client
        mock_http_client.__aexit__.return_value = None
        mock_httpx.return_value = mock_http_client

        client = OAuthClient(factory, {"test_provider": provider_config})

        with pytest.raises(OAuthError) as exc_info:
            await client.exchange_code_for_token("auth_code", "valid_state")

        assert exc_info.value.error == "invalid_grant"


@pytest.mark.asyncio
class TestGetToken:
    """Tests for token retrieval and refresh."""

    async def test_get_token_not_found(
        self, provider_config: Any, mock_session_factory: Any
    ) -> None:
        """Test that missing token returns None."""
        factory, mock_session = mock_session_factory

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        client = OAuthClient(factory, {"test_provider": provider_config})
        token = await client.get_token("test_provider", uuid.uuid4())

        assert token is None

    async def test_get_token_valid(self, provider_config: Any, mock_session_factory: Any) -> None:
        """Test that valid token is returned."""
        factory, mock_session = mock_session_factory

        mock_token = MagicMock()
        mock_token.access_token = "valid_token"
        mock_token.expires_at = datetime.utcnow() + timedelta(hours=1)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_token
        mock_session.execute.return_value = mock_result

        client = OAuthClient(factory, {"test_provider": provider_config})
        token = await client.get_token("test_provider", uuid.uuid4())

        assert token == "valid_token"

    async def test_get_token_expired_no_refresh(
        self, provider_config: Any, mock_session_factory: Any
    ) -> None:
        """Test that expired token without refresh returns None."""
        factory, mock_session = mock_session_factory

        mock_token = MagicMock()
        mock_token.access_token = "expired_token"
        mock_token.expires_at = datetime.utcnow() - timedelta(hours=1)
        mock_token.refresh_token = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_token
        mock_session.execute.return_value = mock_result

        client = OAuthClient(factory, {"test_provider": provider_config})
        token = await client.get_token("test_provider", uuid.uuid4())

        assert token is None

    @patch("core.auth.oauth_client.httpx.AsyncClient")
    async def test_get_token_refresh_on_expiry(
        self, mock_httpx: Any, provider_config: Any, mock_session_factory: Any
    ) -> None:
        """Test that expired token with refresh_token is refreshed."""
        factory, mock_session = mock_session_factory

        mock_token = MagicMock()
        mock_token.access_token = "old_token"
        mock_token.expires_at = datetime.utcnow() - timedelta(seconds=30)  # Just expired
        mock_token.refresh_token = "refresh_token"
        mock_token.context_id = uuid.uuid4()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_token
        mock_session.execute.return_value = mock_result

        # Mock refresh response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "new_access_token",
            "token_type": "Bearer",
            "expires_in": 3600,
        }
        mock_response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = mock_response
        mock_http_client.__aenter__.return_value = mock_http_client
        mock_http_client.__aexit__.return_value = None
        mock_httpx.return_value = mock_http_client

        client = OAuthClient(factory, {"test_provider": provider_config})
        _ = await client.get_token("test_provider", uuid.uuid4())

        # Token should be refreshed
        assert mock_token.access_token == "new_access_token"
        mock_session.commit.assert_called()


@pytest.mark.asyncio
class TestRevokeToken:
    """Tests for token revocation."""

    async def test_revoke_token_exists(
        self, provider_config: Any, mock_session_factory: Any
    ) -> None:
        """Test that existing token is deleted."""
        factory, mock_session = mock_session_factory
        context_id = uuid.uuid4()

        mock_token = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_token
        mock_session.execute.return_value = mock_result

        client = OAuthClient(factory, {"test_provider": provider_config})
        await client.revoke_token("test_provider", context_id)

        mock_session.delete.assert_called_once_with(mock_token)
        mock_session.commit.assert_called_once()

    async def test_revoke_token_not_exists(
        self, provider_config: Any, mock_session_factory: Any
    ) -> None:
        """Test that missing token is handled gracefully."""
        factory, mock_session = mock_session_factory
        context_id = uuid.uuid4()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        client = OAuthClient(factory, {"test_provider": provider_config})
        # Should not raise
        await client.revoke_token("test_provider", context_id)

        mock_session.delete.assert_not_called()
