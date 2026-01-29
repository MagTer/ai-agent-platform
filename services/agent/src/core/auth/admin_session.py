"""JWT session management for admin portal authentication."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import jwt
from fastapi import Request

if TYPE_CHECKING:
    from uuid import UUID

LOGGER = logging.getLogger(__name__)

# Cookie and JWT configuration
COOKIE_NAME = "admin_session"
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24


def create_admin_jwt(
    user_id: UUID,
    email: str,
    name: str,
    role: str,
    secret_key: str,
) -> str:
    """Create a JWT token for admin session.

    Args:
        user_id: Database user ID
        email: User email
        name: User display name
        role: User role (should be "admin")
        secret_key: Secret key for signing JWT

    Returns:
        Encoded JWT token string
    """
    now = datetime.utcnow()
    expiry = now + timedelta(hours=JWT_EXPIRY_HOURS)

    payload = {
        "sub": str(user_id),
        "email": email,
        "name": name,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(expiry.timestamp()),
    }

    return jwt.encode(payload, secret_key, algorithm=JWT_ALGORITHM)


def verify_admin_jwt(token: str, secret_key: str) -> dict[str, str] | None:
    """Verify and decode a JWT token.

    Args:
        token: JWT token string
        secret_key: Secret key for verifying signature

    Returns:
        Decoded payload dict if valid, None otherwise
    """
    try:
        payload = jwt.decode(token, secret_key, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        LOGGER.debug("JWT token expired")
        return None
    except jwt.InvalidTokenError as e:
        LOGGER.debug(f"Invalid JWT token: {e}")
        return None


def get_jwt_from_request(request: Request) -> str | None:
    """Extract JWT token from request cookies.

    Args:
        request: FastAPI request object

    Returns:
        JWT token string if present, None otherwise
    """
    return request.cookies.get(COOKIE_NAME)


__all__ = [
    "COOKIE_NAME",
    "JWT_EXPIRY_HOURS",
    "create_admin_jwt",
    "verify_admin_jwt",
    "get_jwt_from_request",
]
