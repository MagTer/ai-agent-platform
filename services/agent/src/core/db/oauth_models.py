"""OAuth 2.0 token storage models.

This module defines database models for OAuth 2.0 tokens and temporary state storage.
- OAuthToken: Stores access tokens, refresh tokens, and metadata per (context, provider)
- OAuthState: Temporary storage for PKCE code_verifier and state during OAuth flows

Security: OAuth tokens are encrypted at rest using Fernet symmetric encryption.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.models import Base

if TYPE_CHECKING:
    from core.db.models import User

LOGGER = logging.getLogger(__name__)

# Cache for Fernet instances to avoid re-creating on every encrypt/decrypt
_fernet_cache: dict[str, Fernet] = {}


def _get_fernet(key: str) -> Fernet:
    """Get cached Fernet instance for the given key.

    Args:
        key: Encryption key string

    Returns:
        Fernet instance (cached or newly created)
    """
    if key not in _fernet_cache:
        _fernet_cache[key] = Fernet(key.encode())
    return _fernet_cache[key]


def _utc_now() -> datetime:
    """Return naive UTC datetime for SQLAlchemy defaults.

    Returns naive datetime to match TIMESTAMP WITHOUT TIME ZONE columns.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def _get_encryption_key() -> str | None:
    """Get encryption key from settings.

    Returns:
        Encryption key or None if not configured (dev mode).
    """
    from core.runtime.config import get_settings

    key = get_settings().credential_encryption_key
    return key if key else None


def encrypt_token(plaintext: str) -> str:
    """Encrypt a token value using Fernet encryption.

    Args:
        plaintext: Token value to encrypt

    Returns:
        Encrypted token (base64-encoded) or plaintext if no key configured
    """
    key = _get_encryption_key()
    if not key:
        LOGGER.warning("No encryption key configured - storing OAuth token in plaintext (dev mode)")
        return plaintext

    try:
        fernet = _get_fernet(key)
        return fernet.encrypt(plaintext.encode()).decode()
    except Exception as e:
        LOGGER.error(f"Failed to encrypt OAuth token: {e}")
        raise


def decrypt_token(encrypted: str) -> str:
    """Decrypt a token value using Fernet encryption.

    Args:
        encrypted: Encrypted token value

    Returns:
        Decrypted token or raises error if decryption fails
    """
    key = _get_encryption_key()
    if not key:
        # If no key configured, assume plaintext (dev mode)
        return encrypted

    try:
        fernet = _get_fernet(key)
        return fernet.decrypt(encrypted.encode()).decode()
    except InvalidToken:
        # Token is likely stored in plaintext from before encryption was enabled.
        # Return as-is so existing OAuth connections keep working.
        LOGGER.warning(
            "OAuth token appears to be plaintext (pre-encryption). "
            "It will be re-encrypted on next refresh/save."
        )
        return encrypted
    except Exception as e:
        LOGGER.error(f"Unexpected error decrypting OAuth token: {e}")
        raise


class OAuthToken(Base):
    """OAuth 2.0 access token storage.

    Stores OAuth tokens for different providers (Homey, GitHub, etc.) per context.
    Supports automatic token refresh using refresh_token.

    Security: Tokens are encrypted at rest using Fernet encryption. Use the
    set_access_token/get_access_token and set_refresh_token/get_refresh_token
    methods to ensure proper encryption/decryption.

    Attributes:
        id: Primary key
        context_id: Foreign key to Context (multi-tenant isolation)
        user_id: Foreign key to User (optional, for user-specific tokens)
        provider: OAuth provider name (e.g., "homey", "github")
        access_token: Encrypted bearer token for API authentication (DO NOT ACCESS DIRECTLY)
        refresh_token: Encrypted token for automatic refresh (DO NOT ACCESS DIRECTLY)
        token_type: Token type (typically "Bearer")
        expires_at: Absolute expiration timestamp
        scope: Space-separated OAuth scopes (optional)
        metadata: Additional provider-specific data (JSONB)
        created_at: Token creation timestamp
        updated_at: Last update timestamp (e.g., after refresh)
    """

    __tablename__ = "oauth_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    context_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contexts.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String, index=True)
    # NOTE: These columns store ENCRYPTED values. Use get_*/set_* methods.
    access_token: Mapped[str] = mapped_column(
        String
    )  # noqa: S105 - column name, not hardcoded secret
    refresh_token: Mapped[str | None] = mapped_column(
        String, nullable=True
    )  # noqa: S105 - column name
    token_type: Mapped[str] = mapped_column(String, default="Bearer")
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    scope: Mapped[str | None] = mapped_column(String, nullable=True)
    token_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    # Relationships
    user: Mapped["User | None"] = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        UniqueConstraint("context_id", "provider", "user_id", name="uq_context_provider_user"),
    )

    def get_access_token(self) -> str:
        """Get decrypted access token.

        Returns:
            Decrypted access token

        Raises:
            ValueError: If decryption fails (likely due to key rotation)
        """
        return decrypt_token(self.access_token)

    def set_access_token(self, plaintext: str) -> None:
        """Set access token (automatically encrypts).

        Args:
            plaintext: Plain text access token to encrypt and store
        """
        self.access_token = encrypt_token(plaintext)

    def get_refresh_token(self) -> str | None:
        """Get decrypted refresh token.

        Returns:
            Decrypted refresh token or None if not set

        Raises:
            ValueError: If decryption fails (likely due to key rotation)
        """
        if self.refresh_token is None:
            return None
        return decrypt_token(self.refresh_token)

    def set_refresh_token(self, plaintext: str | None) -> None:
        """Set refresh token (automatically encrypts).

        Args:
            plaintext: Plain text refresh token to encrypt and store, or None
        """
        if plaintext is None:
            self.refresh_token = None
        else:
            self.refresh_token = encrypt_token(plaintext)

    def has_refresh_token(self) -> bool:
        """Check if refresh token is set.

        Returns:
            True if refresh token exists, False otherwise
        """
        return self.refresh_token is not None


class OAuthState(Base):
    """Temporary OAuth state for CSRF protection and PKCE.

    Stores state parameter and PKCE code_verifier during OAuth Authorization Code flow.
    Expires after 10 minutes for security.

    Attributes:
        state: Random state string (primary key, used for CSRF protection)
        context_id: Foreign key to Context
        user_id: Optional foreign key to User (for user-specific OAuth flows)
        provider: OAuth provider name
        code_verifier: PKCE code verifier (stored for token exchange)
        expires_at: State expiration timestamp (10 minutes)
        created_at: State creation timestamp
    """

    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String, primary_key=True)
    context_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contexts.id"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String)
    code_verifier: Mapped[str] = mapped_column(String)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
