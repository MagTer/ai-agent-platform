"""OAuth 2.0 API endpoints for authorization flows.

This module provides HTTP endpoints for OAuth 2.0 Authorization Code Grant:
- /auth/oauth/authorize - Start OAuth flow, return authorization URL
- /auth/oauth/callback - Handle OAuth provider callback (NO AUTH - external redirect)
- /auth/oauth/revoke - Revoke OAuth token
- /auth/oauth/status - Check OAuth token status
"""

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from core.auth.models import AuthorizeRequest, AuthorizeResponse, OAuthError
from core.observability.security_logger import (
    OAUTH_COMPLETED,
    OAUTH_FAILED,
    OAUTH_INITIATED,
    log_security_event,
)
from core.providers import get_token_manager
from interfaces.http.admin_auth import AuthenticatedUser, verify_user

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/oauth", tags=["oauth"])


@router.post("/authorize", response_model=AuthorizeResponse)
async def start_authorization(
    request: AuthorizeRequest,
    user: AuthenticatedUser = Depends(verify_user),
) -> AuthorizeResponse:
    """Generate OAuth authorization URL for user to visit.

    Agent calls this when tool fails due to missing OAuth credentials.
    Returns URL that user should click to authorize.

    Args:
        request: Authorization request with provider and context_id

    Returns:
        Authorization URL, state, and user-friendly message

    Raises:
        HTTPException: If provider not configured or authorization fails
    """
    try:
        token_manager = get_token_manager()
        authorization_url, state = await token_manager.get_authorization_url(
            provider=request.provider,
            context_id=UUID(request.context_id),
            user_id=user.user_id,
        )
        log_security_event(
            event_type=OAUTH_INITIATED,
            user_email=user.email,
            user_id=str(user.user_id),
            endpoint="/auth/oauth/authorize",
            details={"provider": request.provider, "context_id": request.context_id},
        )
    except ValueError as e:
        LOGGER.error(f"OAuth authorization failed: {e}")
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        LOGGER.error(f"Unexpected error during authorization: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e

    # Generate user-friendly message for agent to display
    provider_name = request.provider.capitalize()
    message = (
        f"To authorize {provider_name}, please click this link:\n\n"
        f"[Authorize {provider_name}]({authorization_url})\n\n"
        f"You'll be redirected to log in and approve access. "
        f"I'll automatically continue once you complete authorization."
    )

    return AuthorizeResponse(
        authorization_url=authorization_url,
        state=state,
        message=message,
    )


@router.get("/callback")
async def oauth_callback(
    code: str = Query(..., description="Authorization code from provider"),
    state: str = Query(..., description="State parameter for CSRF protection"),
    error: str | None = Query(None, description="Error if user denied"),
) -> HTMLResponse:
    """Handle OAuth provider callback.

    Provider redirects here after user approves/denies authorization.
    Exchanges authorization code for access token and stores in database.

    Args:
        code: Authorization code from provider
        state: State parameter for CSRF validation
        error: Error code if user denied authorization

    Returns:
        HTML page showing success or error
    """
    # Handle user denial
    if error:
        log_security_event(
            event_type=OAUTH_FAILED,
            endpoint="/auth/oauth/callback",
            details={"error": error, "reason": "user_denied"},
            severity="WARNING",
        )
        return HTMLResponse(
            content="""
            <!DOCTYPE html>
            <html>
                <head>
                    <title>Authorization Cancelled</title>
                    <style>
                        body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
                        h1 { color: #d32f2f; }
                    </style>
                </head>
                <body>
                    <h1>Authorization Cancelled</h1>
                    <p>You cancelled the authorization. You can close this window.</p>
                </body>
            </html>
            """,
            status_code=400,
        )

    try:
        # Exchange code for tokens
        token_manager = get_token_manager()
        await token_manager.exchange_code_for_token(
            authorization_code=code,
            state=state,
        )

        # Return success page
        log_security_event(
            event_type=OAUTH_COMPLETED,
            endpoint="/auth/oauth/callback",
            details={"state": state[:8] + "..."},  # Partial state for correlation
        )
        return HTMLResponse(
            content="""
            <!DOCTYPE html>
            <html>
                <head>
                    <title>Authorization Successful</title>
                    <style>
                        body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
                        h1 { color: #2e7d32; }
                        p { font-size: 18px; }
                    </style>
                </head>
                <body>
                    <h1>Authorization Successful!</h1>
                    <p>You've successfully completed the authorization.</p>
                    <p>You can close this window and return to your conversation.</p>
                    <script>
                        // Auto-close after 3 seconds
                        setTimeout(() => window.close(), 3000);
                    </script>
                </body>
            </html>
            """
        )

    except OAuthError as e:
        log_security_event(
            event_type=OAUTH_FAILED,
            endpoint="/auth/oauth/callback",
            details={"error": e.error, "description": e.description},
            severity="ERROR",
        )
        # ruff: noqa: E501
        return HTMLResponse(
            content=f"""
            <!DOCTYPE html>
            <html>
                <head>
                    <title>Authorization Failed</title>
                    <style>
                        body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                        h1 {{ color: #d32f2f; }}
                        .error {{ background: #ffebee; padding: 20px; margin: 20px auto; max-width: 500px; border-radius: 5px; }}
                    </style>
                </head>
                <body>
                    <h1>Authorization Failed</h1>
                    <div class="error">
                        <p><strong>Error:</strong> {e.error}</p>
                        <p>{e.description}</p>
                    </div>
                    <p>Please try again or contact support.</p>
                </body>
            </html>
            """,
            status_code=400,
        )
    except Exception as e:
        LOGGER.error(f"Unexpected error during OAuth callback: {e}")
        return HTMLResponse(
            content="""
            <!DOCTYPE html>
            <html>
                <head>
                    <title>Error</title>
                    <style>
                        body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
                        h1 { color: #d32f2f; }
                    </style>
                </head>
                <body>
                    <h1>Internal Server Error</h1>
                    <p>An unexpected error occurred. Please try again later.</p>
                </body>
            </html>
            """,
            status_code=500,
        )


class RevokeRequest(BaseModel):
    """Request to revoke OAuth token."""

    provider: str
    context_id: str


@router.post("/revoke", dependencies=[Depends(verify_user)])
async def revoke_token(request: RevokeRequest) -> dict[str, str]:
    """Revoke and delete OAuth token.

    Args:
        request: Revoke request with provider and context_id

    Returns:
        Success message

    Raises:
        HTTPException: If revocation fails
    """
    try:
        token_manager = get_token_manager()
        await token_manager.revoke_token(
            provider=request.provider,
            context_id=UUID(request.context_id),
        )
        return {"status": "revoked", "provider": request.provider}
    except Exception as e:
        LOGGER.error(f"Failed to revoke token: {e}")
        raise HTTPException(status_code=500, detail="Failed to revoke token") from e


class StatusRequest(BaseModel):
    """Request to check OAuth token status."""

    provider: str
    context_id: str


@router.post("/status", dependencies=[Depends(verify_user)])
async def check_token_status(request: StatusRequest) -> dict[str, Any]:
    """Check OAuth token status.

    Args:
        request: Status request with provider and context_id

    Returns:
        Token status information

    Raises:
        HTTPException: If status check fails
    """
    try:
        token_manager = get_token_manager()
        token = await token_manager.get_token(
            provider=request.provider,
            context_id=UUID(request.context_id),
        )

        return {
            "provider": request.provider,
            "context_id": request.context_id,
            "has_token": token is not None,
            "is_valid": token is not None,
        }
    except Exception as e:
        LOGGER.error(f"Failed to check token status: {e}")
        raise HTTPException(status_code=500, detail="Failed to check token status") from e
