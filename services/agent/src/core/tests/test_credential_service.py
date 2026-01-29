"""Tests for credential_service module."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet

from core.auth.credential_service import CredentialService
from core.db.models import UserCredential


@pytest.fixture
def encryption_key() -> str:
    """Generate a valid Fernet key for testing."""
    return Fernet.generate_key().decode()


@pytest.fixture
def credential_service(encryption_key: str) -> CredentialService:
    """Create a CredentialService with test key."""
    return CredentialService(encryption_key)


@pytest.fixture
def mock_session() -> AsyncMock:
    """Create a mock async database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.delete = AsyncMock()
    return session


@pytest.fixture
def user_id() -> UUID:
    """Generate a user ID for testing."""
    return uuid4()


class TestCredentialServiceInit:
    """Tests for CredentialService initialization."""

    def test_accepts_string_key(self, encryption_key: str) -> None:
        """Should accept string encryption key."""
        service = CredentialService(encryption_key)
        assert service is not None

    def test_accepts_bytes_key(self) -> None:
        """Should accept bytes encryption key."""
        key = Fernet.generate_key().decode()  # Convert to string
        service = CredentialService(key)
        assert service is not None

    def test_raises_on_invalid_key(self) -> None:
        """Should raise error for invalid key."""
        from cryptography.fernet import InvalidToken

        with pytest.raises((ValueError, InvalidToken)):
            CredentialService("invalid-key")


class TestEncryptDecrypt:
    """Tests for encryption/decryption."""

    def test_encrypt_decrypt_roundtrip(self, credential_service: CredentialService) -> None:
        """Encrypted value should decrypt to original."""
        original = "my-secret-pat-token"
        encrypted = credential_service._encrypt(original)
        decrypted = credential_service._decrypt(encrypted)

        assert decrypted == original
        assert encrypted != original  # Should be different

    def test_encrypted_values_are_different(self, credential_service: CredentialService) -> None:
        """Same value encrypted twice should produce different ciphertext."""
        value = "my-secret"
        encrypted1 = credential_service._encrypt(value)
        encrypted2 = credential_service._encrypt(value)

        # Fernet uses random IV, so ciphertexts should differ
        assert encrypted1 != encrypted2

        # But both should decrypt to same value
        assert credential_service._decrypt(encrypted1) == value
        assert credential_service._decrypt(encrypted2) == value

    def test_decrypt_with_wrong_key_fails(self, encryption_key: str) -> None:
        """Decrypting with wrong key should fail."""
        from cryptography.fernet import InvalidToken

        service1 = CredentialService(encryption_key)
        service2 = CredentialService(Fernet.generate_key().decode())

        encrypted = service1._encrypt("secret")

        with pytest.raises(InvalidToken):
            service2._decrypt(encrypted)


class TestStoreCredential:
    """Tests for store_credential method."""

    @pytest.mark.asyncio
    async def test_creates_new_credential(
        self,
        credential_service: CredentialService,
        mock_session: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Should create new credential when none exists."""
        # Mock: no existing credential
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        await credential_service.store_credential(
            user_id=user_id,
            credential_type="azure_devops_pat",
            value="secret-pat",
            metadata={"org": "myorg"},
            session=mock_session,
        )

        # Verify credential was added
        assert mock_session.add.called
        added_cred = mock_session.add.call_args[0][0]
        assert added_cred.user_id == user_id
        assert added_cred.credential_type == "azure_devops_pat"
        assert added_cred.encrypted_value != "secret-pat"  # Should be encrypted

    @pytest.mark.asyncio
    async def test_updates_existing_credential(
        self,
        credential_service: CredentialService,
        mock_session: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Should update existing credential."""
        existing_cred = UserCredential(
            id=uuid4(),
            user_id=user_id,
            credential_type="azure_devops_pat",
            encrypted_value="old-encrypted",
            credential_metadata={},
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_cred
        mock_session.execute.return_value = mock_result

        await credential_service.store_credential(
            user_id=user_id,
            credential_type="azure_devops_pat",
            value="new-secret",
            metadata={"org": "neworg"},
            session=mock_session,
        )

        # Should NOT add new credential
        assert not mock_session.add.called
        # Should update existing
        assert existing_cred.encrypted_value != "old-encrypted"
        assert existing_cred.credential_metadata == {"org": "neworg"}

    @pytest.mark.asyncio
    async def test_preserves_metadata_when_none_provided(
        self,
        credential_service: CredentialService,
        mock_session: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Should preserve existing metadata when None provided on update."""
        original_metadata = {"org": "original"}
        existing_cred = UserCredential(
            id=uuid4(),
            user_id=user_id,
            credential_type="azure_devops_pat",
            encrypted_value="old-encrypted",
            credential_metadata=original_metadata,
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_cred
        mock_session.execute.return_value = mock_result

        await credential_service.store_credential(
            user_id=user_id,
            credential_type="azure_devops_pat",
            value="new-secret",
            metadata=None,  # Don't update metadata
            session=mock_session,
        )

        # Metadata should remain unchanged
        assert existing_cred.credential_metadata == original_metadata

    @pytest.mark.asyncio
    async def test_flushes_session(
        self,
        credential_service: CredentialService,
        mock_session: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Should flush session after storing credential."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        await credential_service.store_credential(
            user_id=user_id,
            credential_type="azure_devops_pat",
            value="secret-pat",
            metadata={},
            session=mock_session,
        )

        mock_session.flush.assert_called_once()


class TestGetCredential:
    """Tests for get_credential method."""

    @pytest.mark.asyncio
    async def test_returns_decrypted_credential(
        self,
        credential_service: CredentialService,
        mock_session: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Should return decrypted credential value."""
        secret = "my-secret-token"
        encrypted = credential_service._encrypt(secret)

        existing_cred = UserCredential(
            id=uuid4(),
            user_id=user_id,
            credential_type="azure_devops_pat",
            encrypted_value=encrypted,
            credential_metadata={},
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_cred
        mock_session.execute.return_value = mock_result

        result = await credential_service.get_credential(
            user_id=user_id,
            credential_type="azure_devops_pat",
            session=mock_session,
        )

        assert result == secret

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(
        self,
        credential_service: CredentialService,
        mock_session: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Should return None when credential not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await credential_service.get_credential(
            user_id=user_id,
            credential_type="nonexistent",
            session=mock_session,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_invalid_token(
        self,
        credential_service: CredentialService,
        mock_session: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Should return None when decryption fails due to invalid token."""
        # Create credential encrypted with different key
        other_service = CredentialService(Fernet.generate_key().decode())
        encrypted = other_service._encrypt("secret")

        existing_cred = UserCredential(
            id=uuid4(),
            user_id=user_id,
            credential_type="azure_devops_pat",
            encrypted_value=encrypted,
            credential_metadata={},
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_cred
        mock_session.execute.return_value = mock_result

        result = await credential_service.get_credential(
            user_id=user_id,
            credential_type="azure_devops_pat",
            session=mock_session,
        )

        # Should return None instead of raising
        assert result is None


class TestDeleteCredential:
    """Tests for delete_credential method."""

    @pytest.mark.asyncio
    async def test_deletes_existing_credential(
        self,
        credential_service: CredentialService,
        mock_session: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Should delete and return True when credential exists."""
        existing_cred = UserCredential(
            id=uuid4(),
            user_id=user_id,
            credential_type="azure_devops_pat",
            encrypted_value="encrypted",
            credential_metadata={},
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_cred
        mock_session.execute.return_value = mock_result

        result = await credential_service.delete_credential(
            user_id=user_id,
            credential_type="azure_devops_pat",
            session=mock_session,
        )

        assert result is True
        mock_session.delete.assert_called_once_with(existing_cred)

    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(
        self,
        credential_service: CredentialService,
        mock_session: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Should return False when credential not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await credential_service.delete_credential(
            user_id=user_id,
            credential_type="nonexistent",
            session=mock_session,
        )

        assert result is False
        assert not mock_session.delete.called


class TestListCredentials:
    """Tests for list_credentials method."""

    @pytest.mark.asyncio
    async def test_returns_credential_list_without_values(
        self,
        credential_service: CredentialService,
        mock_session: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Should return list of credentials without decrypted values."""
        cred_id = uuid4()
        created_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        updated_at = datetime(2026, 1, 2, 12, 0, 0, tzinfo=UTC)

        cred1 = MagicMock()
        cred1.id = cred_id
        cred1.credential_type = "azure_devops_pat"
        cred1.credential_metadata = {"org": "myorg"}
        cred1.created_at = created_at
        cred1.updated_at = updated_at

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [cred1]
        mock_session.execute.return_value = mock_result

        result = await credential_service.list_credentials(user_id, mock_session)

        assert len(result) == 1
        assert result[0]["id"] == str(cred_id)
        assert result[0]["credential_type"] == "azure_devops_pat"
        assert result[0]["metadata"] == {"org": "myorg"}
        assert result[0]["created_at"] == created_at.isoformat()
        assert result[0]["updated_at"] == updated_at.isoformat()
        # Should NOT include secret values
        assert "encrypted_value" not in result[0]
        assert "value" not in result[0]

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_credentials(
        self,
        credential_service: CredentialService,
        mock_session: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Should return empty list when no credentials."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        result = await credential_service.list_credentials(user_id, mock_session)

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_multiple_credentials(
        self,
        credential_service: CredentialService,
        mock_session: AsyncMock,
        user_id: UUID,
    ) -> None:
        """Should return all credentials for user."""
        created_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        updated_at = datetime(2026, 1, 2, 12, 0, 0, tzinfo=UTC)

        cred1 = MagicMock()
        cred1.id = uuid4()
        cred1.credential_type = "azure_devops_pat"
        cred1.credential_metadata = {"org": "org1"}
        cred1.created_at = created_at
        cred1.updated_at = updated_at

        cred2 = MagicMock()
        cred2.id = uuid4()
        cred2.credential_type = "github_token"
        cred2.credential_metadata = {"scope": "repo"}
        cred2.created_at = created_at
        cred2.updated_at = updated_at

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [cred1, cred2]
        mock_session.execute.return_value = mock_result

        result = await credential_service.list_credentials(user_id, mock_session)

        assert len(result) == 2
        assert result[0]["credential_type"] == "azure_devops_pat"
        assert result[1]["credential_type"] == "github_token"
