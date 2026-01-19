"""Direct Entra ID OAuth authentication for admin portal."""

from __future__ import annotations

import logging
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.admin_session import (
    COOKIE_NAME,
    create_admin_jwt,
    get_jwt_from_request,
    verify_admin_jwt,
)
from core.core.config import Settings, get_settings
from core.db.engine import get_db
from core.db.models import User
from core.observability.security_logger import (
    AUTH_FAILURE,
    AUTH_SUCCESS,
    get_client_ip,
    log_security_event,
)

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platformadmin/auth",
    tags=["platform-admin-auth"],
)

# State cookie for CSRF protection
STATE_COOKIE_NAME = "oauth_state"


@router.get("/login")
async def oauth_login(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Redirect to Entra ID OAuth authorization endpoint.

    Generates a random state parameter for CSRF protection and stores it in a cookie.
    """
    if not settings.entra_client_id or not settings.entra_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Entra ID OAuth not configured. "
                "Set AGENT_ENTRA_CLIENT_ID and AGENT_ENTRA_TENANT_ID."
            ),
        )

    # Generate random state for CSRF protection
    state = secrets.token_urlsafe(32)

    # Build authorization URL
    # Detect correct scheme from X-Forwarded-Proto header (set by Traefik)
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host") or request.url.netloc
    base_url = f"{forwarded_proto}://{host}"
    redirect_uri = f"{base_url}/platformadmin/auth/callback"

    authorization_url = (
        f"https://login.microsoftonline.com/{settings.entra_tenant_id}/oauth2/v2.0/authorize"
    )

    params = {
        "client_id": settings.entra_client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": "openid profile email",
        "state": state,
        "response_mode": "query",
    }

    auth_url = f"{authorization_url}?{urlencode(params)}"

    # Store state in cookie for verification
    redirect_response = RedirectResponse(url=auth_url, status_code=302)
    redirect_response.set_cookie(
        key=STATE_COOKIE_NAME,
        value=state,
        max_age=600,  # 10 minutes
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
    )

    log_security_event(
        event_type="OAUTH_LOGIN_INITIATED",
        ip_address=get_client_ip(request),
        endpoint=request.url.path,
        details={"provider": "entra_id"},
        severity="INFO",
    )

    return redirect_response


@router.get("/callback")
async def oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Handle OAuth callback from Entra ID.

    Exchanges authorization code for tokens, verifies ID token,
    creates/updates user in database, and sets JWT session cookie.
    """
    client_ip = get_client_ip(request)

    # Check for OAuth errors
    if error:
        log_security_event(
            event_type=AUTH_FAILURE,
            ip_address=client_ip,
            endpoint=request.url.path,
            details={"error": error, "description": error_description},
            severity="WARNING",
        )
        return HTMLResponse(
            content=f"""
            <html>
                <body style="font-family: sans-serif; text-align: center; padding: 50px;">
                    <h1>Authentication Failed</h1>
                    <p>Error: {error}</p>
                    <p>{error_description or ''}</p>
                    <a href="/platformadmin/auth/login">Try Again</a>
                </body>
            </html>
            """,
            status_code=400,
        )

    if not code or not state:
        log_security_event(
            event_type=AUTH_FAILURE,
            ip_address=client_ip,
            endpoint=request.url.path,
            details={"reason": "Missing code or state parameter"},
            severity="WARNING",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing code or state parameter",
        )

    # Verify state (CSRF protection)
    stored_state = request.cookies.get(STATE_COOKIE_NAME)
    if not stored_state or stored_state != state:
        log_security_event(
            event_type=AUTH_FAILURE,
            ip_address=client_ip,
            endpoint=request.url.path,
            details={"reason": "Invalid state parameter (CSRF check failed)"},
            severity="WARNING",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid state parameter",
        )

    # Validate configuration
    if (
        not settings.entra_client_id
        or not settings.entra_tenant_id
        or not settings.admin_jwt_secret
    ):
        log_security_event(
            event_type=AUTH_FAILURE,
            ip_address=client_ip,
            endpoint=request.url.path,
            details={"reason": "Incomplete Entra ID OAuth configuration"},
            severity="ERROR",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OAuth not properly configured",
        )

    # Exchange code for tokens
    token_url = f"https://login.microsoftonline.com/{settings.entra_tenant_id}/oauth2/v2.0/token"
    # Detect correct scheme from X-Forwarded-Proto header (set by Traefik)
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host") or request.url.netloc
    base_url = f"{forwarded_proto}://{host}"
    redirect_uri = f"{base_url}/platformadmin/auth/callback"

    token_data = {
        "client_id": settings.entra_client_id,
        "client_secret": settings.entra_client_secret or "",
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    try:
        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                token_url,
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30.0,
            )
            token_response.raise_for_status()
            tokens = token_response.json()
    except httpx.HTTPError as e:
        LOGGER.error(f"Token exchange failed: {e}")
        log_security_event(
            event_type=AUTH_FAILURE,
            ip_address=client_ip,
            endpoint=request.url.path,
            details={"reason": "Token exchange failed", "error": str(e)},
            severity="ERROR",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to exchange authorization code for tokens",
        ) from e

    # Decode ID token (no verification needed - tokens come directly from Microsoft)
    id_token = tokens.get("id_token")
    if not id_token:
        log_security_event(
            event_type=AUTH_FAILURE,
            ip_address=client_ip,
            endpoint=request.url.path,
            details={"reason": "No id_token in response"},
            severity="ERROR",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No ID token received from Entra ID",
        )

    # Decode without verification (we trust the response from Microsoft's token endpoint)
    import jwt

    try:
        id_claims = jwt.decode(id_token, options={"verify_signature": False})
    except Exception as e:
        LOGGER.error(f"Failed to decode ID token: {e}")
        log_security_event(
            event_type=AUTH_FAILURE,
            ip_address=client_ip,
            endpoint=request.url.path,
            details={"reason": "Failed to decode ID token", "error": str(e)},
            severity="ERROR",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to decode ID token",
        ) from e

    # Extract user info from ID token
    email = id_claims.get("email") or id_claims.get("preferred_username")
    name = id_claims.get("name") or email
    oid = id_claims.get("oid")  # Object ID (unique user identifier)

    if not email:
        log_security_event(
            event_type=AUTH_FAILURE,
            ip_address=client_ip,
            endpoint=request.url.path,
            details={"reason": "No email in ID token"},
            severity="ERROR",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No email found in ID token",
        )

    email = email.lower().strip()

    # Look up or create user in database
    stmt = select(User).where(User.email == email)
    result = await session.execute(stmt)
    db_user = result.scalar_one_or_none()

    if not db_user:
        # Create new user
        db_user = User(
            email=email,
            display_name=name,
            role="user",  # Default to user role
            is_active=True,
        )
        session.add(db_user)
        await session.flush()

        LOGGER.info(f"Created new user from Entra ID: {email}")
        log_security_event(
            event_type="USER_CREATED",
            user_email=email,
            ip_address=client_ip,
            endpoint=request.url.path,
            details={"provider": "entra_id", "oid": oid},
            severity="INFO",
        )
    else:
        # Update existing user info if needed
        if db_user.display_name != name:
            db_user.display_name = name

    await session.commit()

    # Check if user is admin
    if db_user.role != "admin":
        log_security_event(
            event_type=AUTH_FAILURE,
            user_email=email,
            user_id=str(db_user.id),
            ip_address=client_ip,
            endpoint=request.url.path,
            details={"reason": f"Admin access required. User role: {db_user.role}"},
            severity="WARNING",
        )
        return HTMLResponse(
            content=f"""
            <html>
                <body style="font-family: sans-serif; text-align: center; padding: 50px;">
                    <h1>Access Denied</h1>
                    <p>Admin access required. Your role: {db_user.role}</p>
                    <a href="/">Return to Home</a>
                </body>
            </html>
            """,
            status_code=403,
        )

    # Check if user is active
    if not db_user.is_active:
        log_security_event(
            event_type=AUTH_FAILURE,
            user_email=email,
            user_id=str(db_user.id),
            ip_address=client_ip,
            endpoint=request.url.path,
            details={"reason": "User account is disabled"},
            severity="WARNING",
        )
        return HTMLResponse(
            content="""
            <html>
                <body style="font-family: sans-serif; text-align: center; padding: 50px;">
                    <h1>Account Disabled</h1>
                    <p>Your account has been disabled. Please contact an administrator.</p>
                    <a href="/">Return to Home</a>
                </body>
            </html>
            """,
            status_code=403,
        )

    # Create JWT session
    jwt_token = create_admin_jwt(
        user_id=db_user.id,
        email=db_user.email,
        name=db_user.display_name or db_user.email,
        role=db_user.role,
        secret_key=settings.admin_jwt_secret,
    )

    # Set JWT cookie and redirect to admin portal
    response = RedirectResponse(url="/platformadmin/", status_code=302)
    response.set_cookie(
        key=COOKIE_NAME,
        value=jwt_token,
        max_age=86400,  # 24 hours
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
    )

    # Clear state cookie
    response.delete_cookie(STATE_COOKIE_NAME)

    log_security_event(
        event_type=AUTH_SUCCESS,
        user_email=email,
        user_id=str(db_user.id),
        ip_address=client_ip,
        endpoint=request.url.path,
        details={"provider": "entra_id", "role": db_user.role},
        severity="INFO",
    )

    return response


@router.get("/logout")
async def oauth_logout(request: Request) -> Response:
    """Clear JWT session cookie and redirect to home."""
    response = RedirectResponse(url="/", status_code=302)
    response.delete_cookie(COOKIE_NAME)

    log_security_event(
        event_type="LOGOUT",
        ip_address=get_client_ip(request),
        endpoint=request.url.path,
        details={},
        severity="INFO",
    )

    return response


@router.get("/me")
async def get_current_user(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Return current user info from JWT session.

    Used by frontend JavaScript to check authentication status.
    """
    if not settings.admin_jwt_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWT authentication not configured",
        )

    jwt_token = get_jwt_from_request(request)
    if not jwt_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    payload = verify_admin_jwt(jwt_token, settings.admin_jwt_secret)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )

    return {
        "user_id": payload.get("sub"),
        "email": payload.get("email"),
        "name": payload.get("name"),
        "role": payload.get("role"),
    }


__all__ = ["router"]
