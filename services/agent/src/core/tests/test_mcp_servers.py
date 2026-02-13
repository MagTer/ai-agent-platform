"""Tests for user-managed MCP server functionality."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from core.db.models import McpServer

# -- Fixtures --


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure a valid Fernet encryption key is set for tests."""
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("AGENT_CREDENTIAL_ENCRYPTION_KEY", key)


@pytest.fixture
def context_id() -> UUID:
    return uuid4()


@pytest.fixture
def mock_session() -> AsyncMock:
    """Create a mock async database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.delete = AsyncMock()
    session.commit = AsyncMock()
    return session


# -- Model Tests --


class TestMcpServerModel:
    """Tests for McpServer SQLAlchemy model."""

    def test_default_values(self) -> None:
        """Verify field values on McpServer with explicit defaults."""
        server = McpServer(
            context_id=uuid4(),
            name="test-server",
            url="https://mcp.example.com/sse",
            transport="auto",
            auth_type="none",
            is_enabled=True,
            status="pending",
            tools_count=0,
        )
        assert server.transport == "auto"
        assert server.auth_type == "none"
        assert server.is_enabled is True
        assert server.status == "pending"
        assert server.tools_count == 0
        assert server.auth_token_encrypted is None
        assert server.oauth_provider_name is None

    def test_encrypt_decrypt_auth_token_roundtrip(self) -> None:
        """Auth token should survive encrypt/decrypt roundtrip."""
        server = McpServer(
            context_id=uuid4(),
            name="test",
            url="https://example.com",
        )
        token = "sk-secret-api-key-12345"
        server.set_auth_token(token)

        assert server.auth_token_encrypted is not None
        assert server.auth_token_encrypted != token
        assert server.get_auth_token() == token

    def test_encrypt_decrypt_oauth_secret_roundtrip(self) -> None:
        """OAuth client secret should survive encrypt/decrypt roundtrip."""
        server = McpServer(
            context_id=uuid4(),
            name="test",
            url="https://example.com",
        )
        secret = "oauth-client-secret-value"
        server.set_oauth_client_secret(secret)

        assert server.oauth_client_secret_encrypted is not None
        assert server.oauth_client_secret_encrypted != secret
        assert server.get_oauth_client_secret() == secret

    def test_set_auth_token_none_clears(self) -> None:
        """Setting auth_token to None should clear the encrypted value."""
        server = McpServer(
            context_id=uuid4(),
            name="test",
            url="https://example.com",
        )
        server.set_auth_token("some-token")
        assert server.auth_token_encrypted is not None

        server.set_auth_token(None)
        assert server.auth_token_encrypted is None
        assert server.get_auth_token() is None

    def test_set_oauth_client_secret_none_clears(self) -> None:
        """Setting oauth_client_secret to None should clear the encrypted value."""
        server = McpServer(
            context_id=uuid4(),
            name="test",
            url="https://example.com",
        )
        server.set_oauth_client_secret("secret")
        assert server.oauth_client_secret_encrypted is not None

        server.set_oauth_client_secret(None)
        assert server.oauth_client_secret_encrypted is None
        assert server.get_oauth_client_secret() is None

    def test_get_auth_token_returns_none_when_not_set(self) -> None:
        """get_auth_token returns None when no token is set."""
        server = McpServer(
            context_id=uuid4(),
            name="test",
            url="https://example.com",
        )
        assert server.get_auth_token() is None

    def test_get_oauth_client_secret_returns_none_when_not_set(self) -> None:
        """get_oauth_client_secret returns None when no secret is set."""
        server = McpServer(
            context_id=uuid4(),
            name="test",
            url="https://example.com",
        )
        assert server.get_oauth_client_secret() is None


# -- Client Pool Tests --


class TestLoadUserMcpServers:
    """Tests for McpClientPool._load_user_mcp_servers."""

    @pytest.mark.asyncio
    async def test_no_servers_returns_false(
        self, context_id: UUID, mock_session: AsyncMock
    ) -> None:
        """Returns False when no user-defined servers exist."""
        from core.mcp.client_pool import McpClientPool
        from core.runtime.config import Settings

        # Mock empty result
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        settings = MagicMock(spec=Settings)
        pool = McpClientPool(settings)
        clients: list = []

        result = await pool._load_user_mcp_servers(context_id, mock_session, clients)

        assert result is False
        assert len(clients) == 0

    @pytest.mark.asyncio
    async def test_disabled_servers_skipped(
        self, context_id: UUID, mock_session: AsyncMock
    ) -> None:
        """Disabled servers (is_enabled=False) should not be loaded."""
        from core.mcp.client_pool import McpClientPool
        from core.runtime.config import Settings

        # The query filters by is_enabled=True, so disabled servers
        # should not appear in results at all
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        settings = MagicMock(spec=Settings)
        pool = McpClientPool(settings)
        clients: list = []

        result = await pool._load_user_mcp_servers(context_id, mock_session, clients)
        assert result is False

    @pytest.mark.asyncio
    async def test_bearer_auth_passes_token(
        self, context_id: UUID, mock_session: AsyncMock
    ) -> None:
        """Bearer-authenticated servers should pass decrypted token to McpClient."""
        from core.mcp.client_pool import McpClientPool
        from core.runtime.config import Settings

        server = McpServer(
            id=uuid4(),
            context_id=context_id,
            name="test-bearer",
            url="https://mcp.example.com/sse",
            transport="auto",
            auth_type="bearer",
            is_enabled=True,
            status="pending",
            tools_count=0,
        )
        server.set_auth_token("test-api-key")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [server]
        mock_session.execute.return_value = mock_result

        settings = MagicMock(spec=Settings)
        settings.oauth_redirect_uri = None
        pool = McpClientPool(settings)
        clients: list = []

        with patch("core.mcp.client_pool.McpClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.tools = [MagicMock(), MagicMock()]
            mock_client.connect = AsyncMock()
            mock_client_cls.return_value = mock_client

            with patch("core.mcp.client_pool.asyncio.wait_for", new_callable=AsyncMock):
                result = await pool._load_user_mcp_servers(context_id, mock_session, clients)

            assert result is True
            # Verify McpClient was created with the decrypted token
            call_kwargs = mock_client_cls.call_args[1]
            assert call_kwargs["auth_token"] == "test-api-key"
            assert call_kwargs["name"] == "test-bearer"

    @pytest.mark.asyncio
    async def test_connection_error_updates_status(
        self, context_id: UUID, mock_session: AsyncMock
    ) -> None:
        """Connection errors should update server status to 'error'."""
        from core.mcp.client_pool import McpClientPool
        from core.runtime.config import Settings

        server = McpServer(
            id=uuid4(),
            context_id=context_id,
            name="failing-server",
            url="https://mcp.example.com/sse",
            transport="auto",
            auth_type="none",
            is_enabled=True,
            status="pending",
            tools_count=0,
        )

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [server]
        mock_session.execute.return_value = mock_result

        settings = MagicMock(spec=Settings)
        settings.oauth_redirect_uri = None
        pool = McpClientPool(settings)
        clients: list = []

        with patch("core.mcp.client_pool.McpClient") as mock_client_cls:
            mock_client_cls.return_value.connect = AsyncMock(
                side_effect=ConnectionError("Connection refused")
            )

            with patch(
                "core.mcp.client_pool.asyncio.wait_for",
                side_effect=ConnectionError("Connection refused"),
            ):
                result = await pool._load_user_mcp_servers(context_id, mock_session, clients)

            assert result is True
            assert len(clients) == 0
            assert server.status == "error"
            assert "Connection refused" in (server.last_error or "")


# -- TokenManager Tests --


class TestTokenManagerDynamicProviders:
    """Tests for TokenManager dynamic provider registration."""

    def test_register_dynamic_provider(self) -> None:
        """register_dynamic_provider should add config to the OAuth client."""
        from pydantic import HttpUrl

        from core.auth.models import OAuthProviderConfig
        from core.auth.token_manager import TokenManager

        session_factory = MagicMock()
        settings = MagicMock()
        settings.homey_oauth_enabled = False
        settings.homey_client_id = None

        tm = TokenManager(session_factory, settings)

        config = OAuthProviderConfig(
            provider_name="mcp_test",
            authorization_url=HttpUrl("https://example.com/auth"),
            token_url=HttpUrl("https://example.com/token"),
            client_id="test-client",
            client_secret=None,
            scopes=None,
            redirect_uri=HttpUrl("https://example.com/callback"),
        )

        tm.register_dynamic_provider("mcp_test", config)

        assert "mcp_test" in tm._oauth_client._provider_configs
        assert tm._oauth_client._provider_configs["mcp_test"] == config

    def test_unregister_dynamic_provider(self) -> None:
        """unregister_dynamic_provider should remove config."""
        from pydantic import HttpUrl

        from core.auth.models import OAuthProviderConfig
        from core.auth.token_manager import TokenManager

        session_factory = MagicMock()
        settings = MagicMock()
        settings.homey_oauth_enabled = False
        settings.homey_client_id = None

        tm = TokenManager(session_factory, settings)

        config = OAuthProviderConfig(
            provider_name="mcp_test",
            authorization_url=HttpUrl("https://example.com/auth"),
            token_url=HttpUrl("https://example.com/token"),
            client_id="test-client",
            client_secret=None,
            scopes=None,
            redirect_uri=HttpUrl("https://example.com/callback"),
        )

        tm.register_dynamic_provider("mcp_test", config)
        assert "mcp_test" in tm._oauth_client._provider_configs

        tm.unregister_dynamic_provider("mcp_test")
        assert "mcp_test" not in tm._oauth_client._provider_configs

    def test_unregister_nonexistent_provider_no_error(self) -> None:
        """Unregistering a provider that doesn't exist should not raise."""
        from core.auth.token_manager import TokenManager

        session_factory = MagicMock()
        settings = MagicMock()
        settings.homey_oauth_enabled = False
        settings.homey_client_id = None

        tm = TokenManager(session_factory, settings)
        # Should not raise
        tm.unregister_dynamic_provider("nonexistent_provider")


# -- API Endpoint Validation Tests --


class TestMcpServerCreateValidation:
    """Tests for McpServerCreate Pydantic model validation."""

    def test_valid_create_no_auth(self) -> None:
        """Should accept valid creation request with no auth."""
        from interfaces.http.admin_mcp import McpServerCreate

        req = McpServerCreate(
            context_id=str(uuid4()),
            name="Test Server",
            url="https://mcp.example.com/sse",
        )
        assert req.auth_type == "none"
        assert req.transport == "auto"

    def test_valid_create_bearer(self) -> None:
        """Should accept valid creation request with bearer auth."""
        from interfaces.http.admin_mcp import McpServerCreate

        req = McpServerCreate(
            context_id=str(uuid4()),
            name="Bearer Server",
            url="https://mcp.example.com/sse",
            auth_type="bearer",
            auth_token="test-token-123",
        )
        assert req.auth_type == "bearer"
        assert req.auth_token == "test-token-123"

    def test_valid_create_oauth(self) -> None:
        """Should accept valid creation request with OAuth auth."""
        from interfaces.http.admin_mcp import McpServerCreate

        req = McpServerCreate(
            context_id=str(uuid4()),
            name="OAuth Server",
            url="https://mcp.example.com/sse",
            auth_type="oauth",
            oauth_authorize_url="https://provider.com/auth",
            oauth_token_url="https://provider.com/token",
            oauth_client_id="client-id",
            oauth_client_secret="client-secret",
            oauth_scopes="read write",
        )
        assert req.auth_type == "oauth"
        assert req.oauth_client_id == "client-id"


class TestMcpServerUpdateValidation:
    """Tests for McpServerUpdate Pydantic model."""

    def test_partial_update_name_only(self) -> None:
        """Should allow updating only the name."""
        from interfaces.http.admin_mcp import McpServerUpdate

        req = McpServerUpdate(name="New Name")
        assert req.name == "New Name"
        assert req.url is None
        assert req.auth_type is None

    def test_partial_update_enable_disable(self) -> None:
        """Should allow toggling is_enabled."""
        from interfaces.http.admin_mcp import McpServerUpdate

        req = McpServerUpdate(is_enabled=False)
        assert req.is_enabled is False
        assert req.name is None


class TestMcpServerInfo:
    """Tests for McpServerInfo response model."""

    def test_serialize_server_info(self) -> None:
        """Should serialize all fields correctly."""
        from interfaces.http.admin_mcp import McpServerInfo

        info = McpServerInfo(
            id=str(uuid4()),
            context_id=str(uuid4()),
            context_name="Test Context",
            name="Test Server",
            url="https://mcp.example.com",
            transport="auto",
            auth_type="none",
            is_enabled=True,
            status="connected",
            last_error=None,
            last_connected_at="2026-02-09T12:00:00",
            tools_count=5,
            has_oauth_config=False,
            created_at="2026-02-09T10:00:00",
            updated_at="2026-02-09T12:00:00",
        )
        data = info.model_dump()
        assert data["name"] == "Test Server"
        assert data["tools_count"] == 5
        assert data["has_oauth_config"] is False


# -- OAuth Auto-Refresh Tests (Phase 8) --


class TestOAuthProviderConfigDbFallback:
    """Tests for OAuthClient._get_provider_config_with_db_fallback."""

    @pytest.mark.asyncio
    async def test_returns_in_memory_config_first(self) -> None:
        """Should return in-memory config without hitting DB."""
        from pydantic import HttpUrl

        from core.auth.models import OAuthProviderConfig
        from core.auth.oauth_client import OAuthClient

        config = OAuthProviderConfig(
            provider_name="mcp_test",
            authorization_url=HttpUrl("https://example.com/auth"),
            token_url=HttpUrl("https://example.com/token"),
            client_id="test-client",
            client_secret=None,
            scopes=None,
            redirect_uri=HttpUrl("https://example.com/callback"),
        )

        session_factory = MagicMock()
        client = OAuthClient(session_factory, {"mcp_test": config})
        mock_session = AsyncMock()

        result = await client._get_provider_config_with_db_fallback("mcp_test", mock_session)
        assert result == config
        # DB should NOT have been queried
        mock_session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_db_for_mcp_provider(self) -> None:
        """Should load config from McpServer table for mcp_ providers."""
        from core.auth.oauth_client import OAuthClient

        server = McpServer(
            id=uuid4(),
            context_id=uuid4(),
            name="test-oauth",
            url="https://mcp.example.com",
            auth_type="oauth",
            oauth_provider_name="mcp_fallback",
            oauth_authorize_url="https://auth.example.com/authorize",
            oauth_token_url="https://auth.example.com/token",
            oauth_client_id="fallback-client",
            oauth_scopes="read",
        )

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = server
        mock_session.execute.return_value = mock_result

        session_factory = MagicMock()
        client = OAuthClient(session_factory, {})

        with patch("core.runtime.config.get_settings") as mock_settings:
            from pydantic import HttpUrl

            mock_settings.return_value.oauth_redirect_uri = HttpUrl("https://example.com/callback")
            result = await client._get_provider_config_with_db_fallback(
                "mcp_fallback", mock_session
            )

        assert result.client_id == "fallback-client"
        assert result.provider_name == "mcp_fallback"
        # Should be cached for future calls
        assert "mcp_fallback" in client._provider_configs

    @pytest.mark.asyncio
    async def test_raises_for_unknown_non_mcp_provider(self) -> None:
        """Should raise ValueError for unknown non-mcp providers."""
        from core.auth.oauth_client import OAuthClient

        session_factory = MagicMock()
        client = OAuthClient(session_factory, {})
        mock_session = AsyncMock()

        with pytest.raises(ValueError, match="not configured"):
            await client._get_provider_config_with_db_fallback("unknown_provider", mock_session)

    @pytest.mark.asyncio
    async def test_raises_for_mcp_provider_not_in_db(self) -> None:
        """Should raise ValueError if mcp_ provider not found in DB."""
        from core.auth.oauth_client import OAuthClient

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        session_factory = MagicMock()
        client = OAuthClient(session_factory, {})

        with pytest.raises(ValueError, match="not configured"):
            await client._get_provider_config_with_db_fallback("mcp_nonexistent", mock_session)


class TestOAuthRefreshTokenRotation:
    """Tests for refresh token rotation in _refresh_token."""

    @pytest.mark.asyncio
    async def test_refresh_stores_new_refresh_token(self) -> None:
        """When refresh response includes new refresh_token, it should be stored."""
        from pydantic import HttpUrl

        from core.auth.models import OAuthProviderConfig
        from core.auth.oauth_client import OAuthClient
        from core.db.oauth_models import OAuthToken

        config = OAuthProviderConfig(
            provider_name="mcp_test",
            authorization_url=HttpUrl("https://example.com/auth"),
            token_url=HttpUrl("https://example.com/token"),
            client_id="test-client",
            client_secret=None,
            scopes=None,
            redirect_uri=HttpUrl("https://example.com/callback"),
        )

        session_factory = MagicMock()
        client = OAuthClient(session_factory, {"mcp_test": config})

        # Create a mock token with refresh token
        token = MagicMock(spec=OAuthToken)
        token.context_id = uuid4()
        token.get_refresh_token.return_value = "old-refresh-token"

        mock_session = AsyncMock()

        # Mock httpx response with new refresh token
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new-access-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": "new-refresh-token",
        }
        mock_response.raise_for_status = MagicMock()

        with patch("core.auth.oauth_client.httpx.AsyncClient") as mock_httpx:
            mock_httpx_instance = AsyncMock()
            mock_httpx_instance.post.return_value = mock_response
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx_instance)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)

            await client._refresh_token(mock_session, token, "mcp_test")

        # Verify new refresh token was stored (rotation)
        token.set_refresh_token.assert_called_once_with("new-refresh-token")
        token.set_access_token.assert_called_once_with("new-access-token")

    @pytest.mark.asyncio
    async def test_refresh_keeps_old_refresh_token_when_not_rotated(
        self,
    ) -> None:
        """When refresh response has no refresh_token, keep existing one."""
        from pydantic import HttpUrl

        from core.auth.models import OAuthProviderConfig
        from core.auth.oauth_client import OAuthClient
        from core.db.oauth_models import OAuthToken

        config = OAuthProviderConfig(
            provider_name="mcp_test",
            authorization_url=HttpUrl("https://example.com/auth"),
            token_url=HttpUrl("https://example.com/token"),
            client_id="test-client",
            client_secret=None,
            scopes=None,
            redirect_uri=HttpUrl("https://example.com/callback"),
        )

        session_factory = MagicMock()
        client = OAuthClient(session_factory, {"mcp_test": config})

        token = MagicMock(spec=OAuthToken)
        token.context_id = uuid4()
        token.get_refresh_token.return_value = "existing-refresh-token"

        mock_session = AsyncMock()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new-access-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            # No refresh_token in response
        }
        mock_response.raise_for_status = MagicMock()

        with patch("core.auth.oauth_client.httpx.AsyncClient") as mock_httpx:
            mock_httpx_instance = AsyncMock()
            mock_httpx_instance.post.return_value = mock_response
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx_instance)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)

            await client._refresh_token(mock_session, token, "mcp_test")

        # Refresh token should NOT have been updated
        token.set_refresh_token.assert_not_called()
        token.set_access_token.assert_called_once_with("new-access-token")


# -- OAuth 2.1 Compliance Tests (Phase 9) --


class TestOAuth21Compliance:
    """Tests for OAuth 2.1 compliance (PKCE, no implicit grant)."""

    @pytest.mark.asyncio
    async def test_authorization_url_always_includes_pkce(self) -> None:
        """get_authorization_url should always include PKCE parameters."""
        from pydantic import HttpUrl

        from core.auth.models import OAuthProviderConfig
        from core.auth.oauth_client import OAuthClient

        config = OAuthProviderConfig(
            provider_name="mcp_test",
            authorization_url=HttpUrl("https://example.com/auth"),
            token_url=HttpUrl("https://example.com/token"),
            client_id="test-client",
            client_secret=None,
            scopes="read write",
            redirect_uri=HttpUrl("https://example.com/callback"),
        )

        session_factory = MagicMock()
        client = OAuthClient(session_factory, {"mcp_test": config})

        context_id = uuid4()
        user_id = uuid4()

        # Mock the session_factory context manager
        mock_session = AsyncMock()
        session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        auth_url, state = await client.get_authorization_url("mcp_test", context_id, user_id)

        # Verify PKCE params in URL
        assert "code_challenge=" in auth_url
        assert "code_challenge_method=S256" in auth_url
        # Verify response_type is always 'code' (not 'token')
        assert "response_type=code" in auth_url

    @pytest.mark.asyncio
    async def test_pkce_code_verifier_stored_in_state(self) -> None:
        """OAuthState should store code_verifier for later exchange."""
        from pydantic import HttpUrl

        from core.auth.models import OAuthProviderConfig
        from core.auth.oauth_client import OAuthClient

        config = OAuthProviderConfig(
            provider_name="mcp_pkce",
            authorization_url=HttpUrl("https://example.com/auth"),
            token_url=HttpUrl("https://example.com/token"),
            client_id="test-client",
            client_secret=None,
            scopes=None,
            redirect_uri=HttpUrl("https://example.com/callback"),
        )

        session_factory = MagicMock()
        client = OAuthClient(session_factory, {"mcp_pkce": config})

        mock_session = AsyncMock()
        session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        await client.get_authorization_url("mcp_pkce", uuid4(), uuid4())

        # Verify session.add was called with OAuthState containing code_verifier
        add_call = mock_session.add.call_args[0][0]
        assert hasattr(add_call, "code_verifier")
        assert len(add_call.code_verifier) > 40  # PKCE verifier is 43-128 chars

    def test_pkce_params_use_s256_method(self) -> None:
        """PKCE should use S256 method (not plain)."""
        from core.auth.oauth_client import OAuthClient

        verifier, challenge = OAuthClient._generate_pkce_params()

        # Verify S256: challenge = base64url(sha256(verifier))
        import base64
        import hashlib

        expected = hashlib.sha256(verifier.encode()).digest()
        expected_b64 = base64.urlsafe_b64encode(expected).decode().rstrip("=")
        assert challenge == expected_b64

    @pytest.mark.asyncio
    async def test_exchange_always_sends_code_verifier(self) -> None:
        """Token exchange should always include code_verifier (PKCE)."""
        from pydantic import HttpUrl

        from core.auth.models import OAuthProviderConfig
        from core.auth.oauth_client import OAuthClient
        from core.db.oauth_models import OAuthState

        config = OAuthProviderConfig(
            provider_name="mcp_exchange",
            authorization_url=HttpUrl("https://example.com/auth"),
            token_url=HttpUrl("https://example.com/token"),
            client_id="test-client",
            client_secret=None,
            scopes=None,
            redirect_uri=HttpUrl("https://example.com/callback"),
        )

        session_factory = MagicMock()
        client = OAuthClient(session_factory, {"mcp_exchange": config})

        # Create mock OAuth state with code_verifier
        mock_state = MagicMock(spec=OAuthState)
        mock_state.provider = "mcp_exchange"
        mock_state.context_id = uuid4()
        mock_state.user_id = uuid4()
        mock_state.code_verifier = "test-code-verifier-12345678901234567890"
        mock_state.expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5)

        mock_session = AsyncMock()
        mock_session.get.return_value = mock_state

        # Mock no existing token
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        # Mock httpx response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new-token",
            "token_type": "Bearer",
            "expires_in": 3600,
        }
        mock_response.raise_for_status = MagicMock()

        with patch("core.auth.oauth_client.httpx.AsyncClient") as mock_httpx:
            mock_httpx_instance = AsyncMock()
            mock_httpx_instance.post.return_value = mock_response
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_httpx_instance)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)

            await client.exchange_code_for_token("auth-code", "test-state")

            # Verify code_verifier was sent in the token request
            call_kwargs = mock_httpx_instance.post.call_args
            token_data = call_kwargs.kwargs.get("data", call_kwargs[1].get("data", {}))
            assert token_data["code_verifier"] == mock_state.code_verifier
            assert token_data["grant_type"] == "authorization_code"
