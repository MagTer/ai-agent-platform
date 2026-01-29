"""Pydantic models for OAuth 2.0 flows.

This module defines request/response models for OAuth 2.0 Authorization Code Grant.
"""

from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl


class OAuthProviderConfig(BaseModel):
    """Configuration for an OAuth 2.0 provider.

    Attributes:
        provider_name: Unique identifier for the provider (e.g., "homey", "github")
        authorization_url: OAuth authorization endpoint URL
        token_url: OAuth token endpoint URL
        client_id: OAuth client ID
        client_secret: OAuth client secret (optional for public clients)
        scopes: Space-separated list of OAuth scopes (optional)
        redirect_uri: Callback URL for OAuth flow
    """

    provider_name: str = Field(..., description="Provider identifier")
    authorization_url: HttpUrl = Field(..., description="Authorization endpoint")
    token_url: HttpUrl = Field(..., description="Token endpoint")
    client_id: str = Field(..., description="OAuth client ID")
    client_secret: str | None = Field(None, description="OAuth client secret (optional)")
    scopes: str | None = Field(None, description="Space-separated scopes")
    redirect_uri: HttpUrl = Field(..., description="Callback URL")


class TokenResponse(BaseModel):
    """OAuth 2.0 token response from provider.

    Attributes:
        access_token: Bearer token for API authentication
        token_type: Token type (typically "Bearer")
        expires_in: Token lifetime in seconds
        refresh_token: Token for automatic refresh (optional)
        scope: Space-separated granted scopes (optional)
    """

    access_token: str = Field(..., description="Bearer access token")
    token_type: str = Field(default="Bearer", description="Token type")
    expires_in: int = Field(..., description="Token lifetime in seconds")
    refresh_token: str | None = Field(None, description="Refresh token (optional)")
    scope: str | None = Field(None, description="Granted scopes")


class AuthorizeRequest(BaseModel):
    """Request to start OAuth authorization flow.

    Attributes:
        provider: OAuth provider name (e.g., "homey")
        context_id: Context UUID for multi-tenant isolation
    """

    provider: str = Field(..., description="OAuth provider name")
    context_id: str = Field(..., description="Context UUID")


class AuthorizeResponse(BaseModel):
    """Response with OAuth authorization URL.

    Attributes:
        authorization_url: URL for user to visit and authorize
        state: Random state string for CSRF protection
        message: User-friendly message for agent to display
    """

    authorization_url: str = Field(..., description="Authorization URL to visit")
    state: str = Field(..., description="CSRF protection state")
    message: str = Field(..., description="User-friendly message for agent")


class TokenStatusResponse(BaseModel):
    """Response with OAuth token status.

    Attributes:
        provider: OAuth provider name
        context_id: Context UUID
        has_token: Whether a token exists
        is_valid: Whether the token is valid (not expired)
        expires_at: Token expiration timestamp (if exists)
    """

    provider: str = Field(..., description="OAuth provider name")
    context_id: str = Field(..., description="Context UUID")
    has_token: bool = Field(..., description="Whether token exists")
    is_valid: bool = Field(..., description="Whether token is valid")
    expires_at: datetime | None = Field(None, description="Token expiration")


class OAuthError(Exception):
    """OAuth 2.0 error with error code and description.

    Attributes:
        error: OAuth error code (e.g., "invalid_grant", "access_denied")
        description: Human-readable error description
    """

    def __init__(self, error: str, description: str):
        self.error = error
        self.description = description
        super().__init__(f"{error}: {description}")
