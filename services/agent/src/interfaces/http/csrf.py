"""CSRF protection utilities for the admin portal.

Uses double-submit cookie pattern:
1. Generate a cryptographically signed token on first GET request
2. Store it in a SameSite=Strict cookie
3. Require the same token in a custom header for POST/DELETE requests
4. Validate the header token matches the cookie token

No server-side state needed - token is signed with HMAC using the admin JWT secret.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets

from fastapi import Cookie, Header, HTTPException, Request, Response, status

LOGGER = logging.getLogger(__name__)

# Cookie and header configuration
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_TOKEN_LENGTH = 32  # bytes (64 hex chars)


def generate_csrf_token(secret_key: str) -> str:
    """Generate a cryptographically signed CSRF token.

    Args:
        secret_key: Secret key for HMAC signing (use admin_jwt_secret)

    Returns:
        Hex-encoded token: random_value||signature
    """
    # Generate random bytes
    random_value = secrets.token_hex(CSRF_TOKEN_LENGTH)

    # Sign with HMAC-SHA256
    signature = hmac.new(
        secret_key.encode("utf-8"),
        random_value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    # Combine: random||signature
    return f"{random_value}||{signature}"


def validate_csrf_token(token: str, secret_key: str) -> bool:
    """Validate a CSRF token's signature.

    Args:
        token: The token to validate (random_value||signature)
        secret_key: Secret key for HMAC verification

    Returns:
        True if valid, False otherwise
    """
    if not token or "||" not in token:
        return False

    try:
        random_value, provided_signature = token.split("||", 1)

        # Recompute signature
        expected_signature = hmac.new(
            secret_key.encode("utf-8"),
            random_value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # Constant-time comparison to prevent timing attacks
        return hmac.compare_digest(expected_signature, provided_signature)
    except Exception:
        LOGGER.warning("CSRF token validation failed", exc_info=True)
        return False


def set_csrf_cookie(response: Response, token: str, secure: bool = False) -> None:
    """Set CSRF token cookie on response.

    Args:
        response: FastAPI Response object
        token: The CSRF token to store
        secure: Whether to set Secure flag (HTTPS only)
    """
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=False,  # Must be readable by JavaScript
        secure=secure,  # Set to True in production (HTTPS)
        samesite="strict",  # Prevent CSRF via cross-site requests
        max_age=3600 * 24,  # 24 hours
        path="/platformadmin/",  # Scope to admin portal only
    )


async def require_csrf(
    request: Request,
    csrf_cookie: str | None = Cookie(None, alias=CSRF_COOKIE_NAME),
    csrf_header: str | None = Header(None, alias=CSRF_HEADER_NAME),
) -> None:
    """FastAPI dependency to validate CSRF tokens.

    Usage:
        @router.post("/endpoint", dependencies=[Depends(require_csrf)])
        async def my_endpoint(...):
            ...

    Raises:
        HTTPException: If CSRF validation fails
    """
    from core.core.config import get_settings

    settings = get_settings()

    # Skip in test environment
    if settings.environment == "test":
        return

    # Check secret is configured
    if not settings.admin_jwt_secret:
        LOGGER.error("CSRF protection requires AGENT_ADMIN_JWT_SECRET to be set")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="CSRF protection not configured",
        )

    # Validate cookie exists
    if not csrf_cookie:
        client_ip = request.client.host if request.client else "unknown"
        LOGGER.warning(f"CSRF validation failed: missing cookie from {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing in cookie",
        )

    # Validate header exists
    if not csrf_header:
        client_ip = request.client.host if request.client else "unknown"
        LOGGER.warning(f"CSRF validation failed: missing header from {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing in header",
        )

    # Validate cookie signature
    if not validate_csrf_token(csrf_cookie, settings.admin_jwt_secret):
        client_ip = request.client.host if request.client else "unknown"
        LOGGER.warning(f"CSRF validation failed: invalid signature from {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token signature",
        )

    # Validate tokens match (double-submit check)
    if not hmac.compare_digest(csrf_cookie, csrf_header):
        client_ip = request.client.host if request.client else "unknown"
        LOGGER.warning(f"CSRF validation failed: token mismatch from {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token mismatch",
        )

    # All checks passed
    LOGGER.debug("CSRF validation succeeded")


__all__ = [
    "CSRF_COOKIE_NAME",
    "CSRF_HEADER_NAME",
    "generate_csrf_token",
    "require_csrf",
    "set_csrf_cookie",
    "validate_csrf_token",
]
