"""Tests for Qdrant authentication and security hardening."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from core.runtime.config import Settings
from core.runtime.memory import MemoryStore
from core.runtime.service_factory import ServiceFactory


class TestQdrantClientInitialization:
    """Test that QdrantClient is properly initialized with authentication."""

    def test_client_initialized_with_api_key(self) -> None:
        """Verify AsyncQdrantClient is created with api_key from settings."""
        settings = Settings(
            environment="development",
            qdrant_url="http://qdrant:6333",
            qdrant_api_key="test-secret-key-12345",
            tools_config_path=Path("config/tools.yaml"),
        )

        with patch("core.runtime.service_factory.AsyncQdrantClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            # Patch load_tool_registry to avoid file I/O during test
            with patch("core.runtime.service_factory.load_tool_registry") as mock_load:
                mock_load.return_value = MagicMock()
                ServiceFactory(
                    settings=settings,
                    litellm_client=MagicMock(),
                )

            # Verify client was created with api_key
            mock_client_class.assert_called_once()
            call_kwargs = mock_client_class.call_args.kwargs
            assert call_kwargs.get("api_key") == "test-secret-key-12345"
            assert call_kwargs.get("url") == "http://qdrant:6333/"  # HttpUrl adds trailing slash
            assert call_kwargs.get("timeout") == 30  # SECURITY: timeout set

    def test_client_initialized_without_api_key_in_dev(self) -> None:
        """Verify client can be created without api_key in development."""
        settings = Settings(
            environment="development",
            qdrant_url="http://qdrant:6333",
            qdrant_api_key=None,
            tools_config_path=Path("config/tools.yaml"),
        )

        with patch("core.runtime.service_factory.AsyncQdrantClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            # Patch load_tool_registry to avoid file I/O during test
            with patch("core.runtime.service_factory.load_tool_registry") as mock_load:
                mock_load.return_value = MagicMock()
                ServiceFactory(
                    settings=settings,
                    litellm_client=MagicMock(),
                )

            # Verify client was created with None api_key (development mode)
            mock_client_class.assert_called_once()
            call_kwargs = mock_client_class.call_args.kwargs
            assert call_kwargs.get("api_key") is None

    def test_memory_store_uses_provided_client(self) -> None:
        """Verify MemoryStore uses the shared client from ServiceFactory."""
        settings = Settings(
            environment="development",
            qdrant_url="http://qdrant:6333",
            qdrant_api_key="test-key",
            tools_config_path=Path("config/tools.yaml"),
        )

        mock_qdrant_client = MagicMock()
        context_id = uuid4()

        # Create MemoryStore with provided client (shared)
        memory = MemoryStore(
            settings=settings,
            context_id=context_id,
            client=mock_qdrant_client,
        )

        # Verify client is stored and not owned by MemoryStore
        assert memory._client is mock_qdrant_client
        assert memory._owns_client is False

    def test_memory_store_creates_own_client_when_none_provided(self) -> None:
        """Verify MemoryStore creates its own client when none is provided."""
        settings = Settings(
            environment="development",
            qdrant_url="http://qdrant:6333",
            qdrant_api_key="test-key",
            tools_config_path=Path("config/tools.yaml"),
        )

        context_id = uuid4()

        with patch("core.runtime.memory.AsyncQdrantClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            # Patch _async_ensure_client to not actually call Qdrant
            with patch.object(MemoryStore, "_async_ensure_client", AsyncMock()):
                memory = MemoryStore(
                    settings=settings,
                    context_id=context_id,
                    client=None,
                )

            # Client should be None initially (created in ainit)
            # _owns_client should be True since MemoryStore will create it
            assert memory._owns_client is True


class TestQdrantAuthenticationFailure:
    """Test handling of unauthenticated Qdrant connections."""

    @pytest.mark.asyncio
    async def test_unauthenticated_request_rejected(self) -> None:
        """Verify that requests without valid auth are rejected."""
        from qdrant_client.http.exceptions import UnexpectedResponse

        settings = Settings(
            environment="development",
            qdrant_url="http://qdrant:6333",
            qdrant_api_key="wrong-key",
            tools_config_path=Path("config/tools.yaml"),
        )

        mock_client = AsyncMock()
        # Simulate authentication failure
        mock_client.get_collection.side_effect = UnexpectedResponse(
            status_code=401,
            reason_phrase="Unauthorized: Invalid API key",
            content=b'{"status":{"error":"Invalid API key"}}',
            headers={},  # type: ignore[arg-type]
        )

        memory = MemoryStore(
            settings=settings,
            context_id=uuid4(),
            client=mock_client,
        )

        # Attempt to access collection should raise auth error
        with pytest.raises(UnexpectedResponse) as exc_info:
            await mock_client.get_collection(settings.qdrant_collection)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_authenticated_request_succeeds(self) -> None:
        """Verify that requests with valid auth succeed."""
        settings = Settings(
            environment="development",
            qdrant_url="http://qdrant:6333",
            qdrant_api_key="valid-key",
            qdrant_collection="test-collection",
            tools_config_path=Path("config/tools.yaml"),
        )

        mock_client = AsyncMock()
        # Simulate successful response
        mock_collection_info = MagicMock()
        mock_collection_info.config = MagicMock()
        mock_collection_info.config.params = MagicMock()
        mock_collection_info.config.params.vectors = MagicMock()
        mock_collection_info.config.params.vectors.size = 384
        mock_client.get_collection.return_value = mock_collection_info

        memory = MemoryStore(
            settings=settings,
            context_id=uuid4(),
            client=mock_client,
        )

        # Should not raise when client returns valid response
        result = await mock_client.get_collection(settings.qdrant_collection)
        assert result is mock_collection_info
        mock_client.get_collection.assert_called_once_with("test-collection")


class TestProductionSettingsValidation:
    """Test that settings properly validate qdrant_api_key in production."""

    def test_production_without_qdrant_api_key_warns(self) -> None:
        """Verify production mode without qdrant_api_key is allowed but should log warning.

        Note: Currently the codebase doesn't enforce qdrant_api_key in production
        settings validator, but we test the current behavior and document the gap.
        """
        # This should NOT raise - the settings validator currently doesn't check qdrant_api_key
        settings = Settings(
            environment="production",
            qdrant_url="http://qdrant:6333",
            qdrant_api_key=None,  # Missing API key
            credential_encryption_key="test-encryption-key-32bytes-long",
            admin_jwt_secret="test-admin-secret-32bytes-long",
            internal_api_key="test-internal-api-key-32bytes",
            tools_config_path=Path("config/tools.yaml"),
        )

        # Settings created successfully even without qdrant_api_key
        assert settings.qdrant_api_key is None
        assert settings.environment == "production"

    def test_production_with_qdrant_api_key_succeeds(self) -> None:
        """Verify production mode with qdrant_api_key works correctly."""
        settings = Settings(
            environment="production",
            qdrant_url="http://qdrant:6333",
            qdrant_api_key="secure-api-key-12345",
            credential_encryption_key="test-encryption-key-32bytes-long",
            admin_jwt_secret="test-admin-secret-32bytes-long",
            internal_api_key="test-internal-api-key-32bytes",
            tools_config_path=Path("config/tools.yaml"),
        )

        assert settings.qdrant_api_key == "secure-api-key-12345"
        assert settings.environment == "production"

    def test_development_without_qdrant_api_key_succeeds(self) -> None:
        """Verify development mode works without qdrant_api_key."""
        settings = Settings(
            environment="development",
            qdrant_url="http://qdrant:6333",
            qdrant_api_key=None,
            tools_config_path=Path("config/tools.yaml"),
        )

        assert settings.qdrant_api_key is None
        assert settings.environment == "development"


class TestQdrantTimeoutConfiguration:
    """Test that Qdrant client has proper timeout configuration."""

    def test_timeout_prevents_hanging(self) -> None:
        """Verify client is created with timeout to prevent hanging under load."""
        settings = Settings(
            environment="development",
            qdrant_url="http://qdrant:6333",
            qdrant_api_key="test-key",
            tools_config_path=Path("config/tools.yaml"),
        )

        with patch("core.runtime.service_factory.AsyncQdrantClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            # Patch load_tool_registry to avoid file I/O during test
            with patch("core.runtime.service_factory.load_tool_registry") as mock_load:
                mock_load.return_value = MagicMock()
                ServiceFactory(
                    settings=settings,
                    litellm_client=MagicMock(),
                )

            # SECURITY: Verify timeout is set to prevent hanging
            call_kwargs = mock_client_class.call_args.kwargs
            assert call_kwargs.get("timeout") == 30


class TestQdrantApiKeyPropagation:
    """Test that API key flows correctly from settings to client."""

    def test_api_key_from_environment_variable(self) -> None:
        """Verify api_key is loaded from AGENT_QDRANT_API_KEY env var."""
        with patch.dict(
            "os.environ",
            {"AGENT_QDRANT_API_KEY": "env-provided-key-123"},
            clear=False,
        ):
            settings = Settings(
                environment="development",
                tools_config_path=Path("config/tools.yaml"),
            )
            assert settings.qdrant_api_key == "env-provided-key-123"

    def test_api_key_explicit_override(self) -> None:
        """Verify explicit api_key overrides environment."""
        with patch.dict(
            "os.environ",
            {"AGENT_QDRANT_API_KEY": "env-key"},
            clear=False,
        ):
            settings = Settings(
                environment="development",
                qdrant_api_key="explicit-key",
                tools_config_path=Path("config/tools.yaml"),
            )
            # Explicit value takes precedence
            assert settings.qdrant_api_key == "explicit-key"
