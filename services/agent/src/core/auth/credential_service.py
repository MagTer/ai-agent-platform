"""Encrypted credential storage service using Fernet symmetric encryption."""

import logging
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import UserCredential

LOGGER = logging.getLogger(__name__)


class CredentialService:
    """Service for storing and retrieving encrypted user credentials."""

    def __init__(self, encryption_key: str):
        """Initialize with Fernet encryption key.

        Args:
            encryption_key: Base64-encoded Fernet key (32 bytes).
                           Generate with: Fernet.generate_key()
        """
        key_bytes = encryption_key.encode() if isinstance(encryption_key, str) else encryption_key
        self._fernet = Fernet(key_bytes)

    def _encrypt(self, value: str) -> str:
        """Encrypt a string value."""
        return self._fernet.encrypt(value.encode()).decode()

    def _decrypt(self, encrypted_value: str) -> str:
        """Decrypt an encrypted string value."""
        return self._fernet.decrypt(encrypted_value.encode()).decode()

    async def store_credential(
        self,
        user_id: UUID,
        credential_type: str,
        value: str,
        metadata: dict | None,
        session: AsyncSession,
    ) -> UserCredential:
        """Store or update an encrypted credential for a user.

        Args:
            user_id: User's UUID
            credential_type: Type of credential (e.g., 'azure_devops_pat')
            value: Plain text credential value (will be encrypted)
            metadata: Optional non-sensitive metadata
            session: Database session

        Returns:
            UserCredential object
        """
        # Check for existing credential
        stmt = select(UserCredential).where(
            UserCredential.user_id == user_id,
            UserCredential.credential_type == credential_type,
        )
        result = await session.execute(stmt)
        credential = result.scalar_one_or_none()

        encrypted_value = self._encrypt(value)

        if credential:
            # Update existing
            credential.encrypted_value = encrypted_value
            if metadata is not None:
                credential.credential_metadata = metadata
            LOGGER.info(f"Updated credential {credential_type} for user {user_id}")
        else:
            # Create new
            credential = UserCredential(
                user_id=user_id,
                credential_type=credential_type,
                encrypted_value=encrypted_value,
                credential_metadata=metadata or {},
            )
            session.add(credential)
            LOGGER.info(f"Stored new credential {credential_type} for user {user_id}")

        await session.flush()
        return credential

    async def get_credential(
        self,
        user_id: UUID,
        credential_type: str,
        session: AsyncSession,
    ) -> str | None:
        """Retrieve and decrypt a credential.

        Args:
            user_id: User's UUID
            credential_type: Type of credential
            session: Database session

        Returns:
            Decrypted credential value, or None if not found or decryption fails
        """
        stmt = select(UserCredential).where(
            UserCredential.user_id == user_id,
            UserCredential.credential_type == credential_type,
        )
        result = await session.execute(stmt)
        credential = result.scalar_one_or_none()

        if not credential:
            return None

        try:
            return self._decrypt(credential.encrypted_value)
        except InvalidToken:
            # Provide actionable error context
            created_at = credential.created_at.isoformat() if credential.created_at else "unknown"
            LOGGER.error(
                "Failed to decrypt credential '%s' for user %s. "
                "Credential was stored at %s. "
                "This typically means the encryption key was rotated since "
                "the credential was stored. "
                "The user should re-enter their credential through the admin portal.",
                credential_type,
                user_id,
                created_at,
            )
            return None

    async def delete_credential(
        self,
        user_id: UUID,
        credential_type: str,
        session: AsyncSession,
    ) -> bool:
        """Delete a credential.

        Returns:
            True if deleted, False if not found
        """
        stmt = select(UserCredential).where(
            UserCredential.user_id == user_id,
            UserCredential.credential_type == credential_type,
        )
        result = await session.execute(stmt)
        credential = result.scalar_one_or_none()

        if credential:
            await session.delete(credential)
            LOGGER.info(f"Deleted credential {credential_type} for user {user_id}")
            return True
        return False

    async def list_credentials(
        self,
        user_id: UUID,
        session: AsyncSession,
    ) -> list[dict]:
        """List all credentials for a user (without decrypted values).

        Returns:
            List of credential info dicts with type, metadata, timestamps
        """
        stmt = select(UserCredential).where(UserCredential.user_id == user_id)
        result = await session.execute(stmt)
        credentials = result.scalars().all()

        return [
            {
                "id": str(cred.id),
                "credential_type": cred.credential_type,
                "metadata": cred.credential_metadata,
                "created_at": cred.created_at.isoformat(),
                "updated_at": cred.updated_at.isoformat(),
            }
            for cred in credentials
        ]
