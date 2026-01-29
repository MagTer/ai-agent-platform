"""OAuth 2.0 token storage models.

This module defines database models for OAuth 2.0 tokens and temporary state storage.
- OAuthToken: Stores access tokens, refresh tokens, and metadata per (context, provider)
- OAuthState: Temporary storage for PKCE code_verifier and state during OAuth flows
"""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.models import Base

if TYPE_CHECKING:
    from core.db.models import User


def _utc_now() -> datetime:
    """Return naive UTC datetime for SQLAlchemy defaults.

    Returns naive datetime to match TIMESTAMP WITHOUT TIME ZONE columns.
    """
    return datetime.now(UTC).replace(tzinfo=None)


class OAuthToken(Base):
    """OAuth 2.0 access token storage.

    Stores OAuth tokens for different providers (Homey, GitHub, etc.) per context.
    Supports automatic token refresh using refresh_token.

    Attributes:
        id: Primary key
        context_id: Foreign key to Context (multi-tenant isolation)
        user_id: Foreign key to User (optional, for user-specific tokens)
        provider: OAuth provider name (e.g., "homey", "github")
        access_token: Bearer token for API authentication
        refresh_token: Token for automatic refresh (optional)
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
    access_token: Mapped[str] = mapped_column(String)
    refresh_token: Mapped[str | None] = mapped_column(String, nullable=True)
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
