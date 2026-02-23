"""Direct Entra ID OAuth authentication for admin portal."""

from __future__ import annotations

import html
import logging
import secrets
from typing import Any
from urllib.parse import unquote, urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.admin_session import (
    COOKIE_NAME,
    create_admin_jwt,
    get_jwt_from_request,
    verify_admin_jwt,
)
from core.db.engine import get_db
from core.db.models import Context, User, UserContext
from core.observability.security_logger import (
    AUTH_FAILURE,
    AUTH_SUCCESS,
    get_client_ip,
    log_security_event,
)
from core.runtime.config import Settings, get_settings

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platformadmin/auth",
    tags=["platform-admin-auth"],
)

# State cookie for CSRF protection
STATE_COOKIE_NAME = "oauth_state"

# JWKS client cache - one per tenant
_jwks_clients: dict[str, PyJWKClient] = {}


def _get_jwks_client(tenant_id: str) -> PyJWKClient:
    """Get or create a cached JWKS client for the given tenant.

    The PyJWKClient handles JWKS caching internally with a default TTL.
    We cache the client instance itself to avoid creating new clients for each request.

    Args:
        tenant_id: The Entra ID tenant ID

    Returns:
        PyJWKClient configured for the tenant
    """
    if tenant_id not in _jwks_clients:
        jwks_url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        _jwks_clients[tenant_id] = PyJWKClient(jwks_url, cache_keys=True)
    return _jwks_clients[tenant_id]


async def verify_entra_id_token(
    id_token: str,
    client_id: str,
    tenant_id: str,
) -> dict[str, Any]:
    """Verify an Entra ID token's signature and claims.

    Uses Microsoft's JWKS to verify the token signature, then validates:
    - aud (audience) matches the client ID
    - iss (issuer) matches the expected Microsoft issuer
    - exp (expiration) is not in the past

    Args:
        id_token: The JWT ID token from Entra ID
        client_id: The application's client ID (expected audience)
        tenant_id: The Entra ID tenant ID

    Returns:
        dict: The verified token claims

    Raises:
        jwt.InvalidTokenError: If verification fails
    """
    # Get JWKS client (fetches signing keys from Microsoft)
    jwks_client = _get_jwks_client(tenant_id)

    # Get the signing key for this token
    # PyJWKClient handles the JWKS fetch and caching
    signing_key = jwks_client.get_signing_key_from_jwt(id_token)

    # Verify signature and claims
    # RS256 is the algorithm Microsoft uses for ID tokens
    issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=client_id,
        issuer=issuer,
        options={
            "verify_signature": True,
            "verify_aud": True,
            "verify_iss": True,
            "verify_exp": True,
        },
    )

    return claims


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
    is_secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    redirect_response.set_cookie(
        key=STATE_COOKIE_NAME,
        value=state,
        max_age=600,  # 10 minutes
        httponly=True,
        secure=is_secure,
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
        # SECURITY: Escape error messages to prevent XSS attacks
        safe_error = html.escape(error or "Unknown error")
        safe_description = html.escape(error_description or "")
        return HTMLResponse(
            content=f"""
            <html>
                <body style="font-family: sans-serif; text-align: center; padding: 50px;">
                    <h1>Authentication Failed</h1>
                    <p>Error: {safe_error}</p>
                    <p>{safe_description}</p>
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
    if not stored_state or not secrets.compare_digest(stored_state, state):
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

    # Decode ID token with signature verification
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

    # Verify ID token signature using Microsoft's JWKS
    try:
        id_claims = await verify_entra_id_token(
            id_token=id_token,
            client_id=settings.entra_client_id,
            tenant_id=settings.entra_tenant_id,
        )
    except Exception as e:
        LOGGER.error(f"Failed to verify ID token: {e}")
        log_security_event(
            event_type=AUTH_FAILURE,
            ip_address=client_ip,
            endpoint=request.url.path,
            details={"reason": "Failed to verify ID token", "error": str(e)},
            severity="ERROR",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to verify ID token",
        ) from e

    # Extract user info from ID token
    email = id_claims.get("email") or id_claims.get("preferred_username")
    # URL-decode name in case it comes encoded (e.g., %C3%B6 instead of ö)
    raw_name = id_claims.get("name") or email
    name = unquote(raw_name) if raw_name else email
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

    # Determine role from Entra ID App Roles claim
    admin_roles = {r.strip() for r in settings.entra_admin_roles.split(",") if r.strip()}
    token_roles: list[str] = id_claims.get("roles", [])
    is_admin = bool(admin_roles & set(token_roles))
    resolved_role = "admin" if is_admin else "user"

    # Look up or create user in database
    stmt = select(User).where(User.email == email)
    result = await session.execute(stmt)
    db_user = result.scalar_one_or_none()

    if not db_user:
        # Create new user with role derived from Entra ID
        db_user = User(
            email=email,
            display_name=name,
            role=resolved_role,
            is_active=True,
        )
        session.add(db_user)
        await session.flush()  # Get db_user.id

        # Auto-provision personal context (same as get_or_create_user)
        context = Context(
            name=f"Personal - {email}",
            type="personal",
            config={"owner_email": email},
            default_cwd="/tmp",  # noqa: S108
        )
        session.add(context)
        await session.flush()  # Get context.id

        user_context = UserContext(
            user_id=db_user.id,
            context_id=context.id,
            role="owner",
            is_default=True,
        )
        session.add(user_context)
        await session.flush()

        LOGGER.info(f"Created new user from Entra ID: {email} (role={resolved_role})")
        log_security_event(
            event_type="USER_CREATED",
            user_email=email,
            ip_address=client_ip,
            endpoint=request.url.path,
            details={
                "provider": "entra_id",
                "oid": oid,
                "role": resolved_role,
                "token_roles": token_roles,
            },
            severity="INFO",
        )
    else:
        # Sync role and display name from Entra ID on every login
        if db_user.display_name != name:
            db_user.display_name = name
        if db_user.role != resolved_role:
            old_role = db_user.role
            db_user.role = resolved_role
            LOGGER.info(f"Role synced from Entra ID for {email}: {old_role} -> {resolved_role}")
            log_security_event(
                event_type="ROLE_SYNCED",
                user_email=email,
                user_id=str(db_user.id),
                ip_address=client_ip,
                endpoint=request.url.path,
                details={
                    "old_role": old_role,
                    "new_role": resolved_role,
                    "token_roles": token_roles,
                },
                severity="INFO",
            )

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
    is_secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(
        key=COOKIE_NAME,
        value=jwt_token,
        max_age=86400,  # 24 hours
        httponly=True,
        secure=is_secure,
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
