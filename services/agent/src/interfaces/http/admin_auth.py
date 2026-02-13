"""Admin authentication using Entra ID headers from Open WebUI."""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.header_auth import UserIdentity, extract_user_from_headers
from core.db.engine import get_db
from core.db.models import User
from core.observability.security_logger import (
    AUTH_FAILURE,
    AUTH_SUCCESS,
    get_client_ip,
    log_security_event,
)


class AuthRedirectError(Exception):
    """Exception that signals a redirect to login is needed."""

    def __init__(self, redirect_url: str = "/platformadmin/auth/login") -> None:
        self.redirect_url = redirect_url
        super().__init__(f"Redirect to {redirect_url}")


class AdminUser:
    """Authenticated admin user with database record."""

    def __init__(self, identity: UserIdentity, db_user: User) -> None:
        self.identity = identity
        self.db_user = db_user

    @property
    def user_id(self) -> UUID:
        """Return the database user ID."""
        return self.db_user.id

    @property
    def email(self) -> str:
        """Return the user's email."""
        return self.db_user.email

    @property
    def display_name(self) -> str | None:
        """Return the user's display name."""
        return self.db_user.display_name


async def get_admin_user(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> AdminUser:
    """Extract and verify admin user from Entra ID headers or JWT cookie.

    Authentication methods (in order of priority):
    1. X-OpenWebUI-User-* headers (forwarded by Open WebUI)
    2. JWT session cookie (for direct OAuth access)

    Headers expected (forwarded by Open WebUI):
        X-OpenWebUI-User-Email: user@example.com (required)
        X-OpenWebUI-User-Name: John Doe (optional)
        X-OpenWebUI-User-Id: uuid (optional)
        X-OpenWebUI-User-Role: admin (required for admin access)

    Returns:
        AdminUser with identity and database record

    Raises:
        HTTPException 401: Missing or invalid authentication
        HTTPException 403: User is not an admin
    """
    # Try header authentication first (Open WebUI)
    identity = extract_user_from_headers(request)

    # If no headers, try JWT cookie (direct OAuth)
    if not identity:
        from core.auth.admin_session import get_jwt_from_request, verify_admin_jwt
        from core.runtime.config import get_settings

        settings = get_settings()
        if settings.admin_jwt_secret:
            jwt_token = get_jwt_from_request(request)
            if jwt_token:
                payload = verify_admin_jwt(jwt_token, settings.admin_jwt_secret)
                if payload:
                    # Create identity from JWT payload
                    from core.auth.header_auth import UserIdentity

                    identity = UserIdentity(
                        email=payload.get("email", ""),
                        name=payload.get("name"),
                        openwebui_id=None,
                        role=payload.get("role", "user"),
                    )

    if not identity:
        log_security_event(
            event_type=AUTH_FAILURE,
            ip_address=get_client_ip(request),
            endpoint=request.url.path,
            details={"reason": "Missing X-OpenWebUI-User-Email header"},
            severity="WARNING",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Missing X-OpenWebUI-User-Email header.",
            headers={"WWW-Authenticate": "OpenWebUI"},
        )

    # Look up user in database
    stmt = select(User).where(User.email == identity.email.lower())
    result = await session.execute(stmt)
    db_user = result.scalar_one_or_none()

    if not db_user:
        log_security_event(
            event_type=AUTH_FAILURE,
            user_email=identity.email,
            ip_address=get_client_ip(request),
            endpoint=request.url.path,
            details={"reason": "User not found in database"},
            severity="WARNING",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"User {identity.email} not found. Login via Open WebUI first.",
        )

    if not db_user.is_active:
        log_security_event(
            event_type=AUTH_FAILURE,
            user_email=identity.email,
            user_id=str(db_user.id),
            ip_address=get_client_ip(request),
            endpoint=request.url.path,
            details={"reason": "User account is disabled"},
            severity="WARNING",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled.",
        )

    # SECURITY: Database role is the ONLY source of truth for admin authorization.
    # Header/JWT role claims are NEVER trusted - they could be spoofed.
    # We log the claimed role for forensics but use db_user.role for the check.
    if db_user.role != "admin":
        log_security_event(
            event_type=AUTH_FAILURE,
            user_email=identity.email,
            user_id=str(db_user.id),
            ip_address=get_client_ip(request),
            endpoint=request.url.path,
            details={
                "reason": "Admin access required",
                "db_role": db_user.role,
                "claimed_role": identity.role,  # Log for forensics only
            },
            severity="WARNING",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Admin access required. Your role: {db_user.role}",
        )

    # Log successful admin authentication
    log_security_event(
        event_type=AUTH_SUCCESS,
        user_email=identity.email,
        user_id=str(db_user.id),
        ip_address=get_client_ip(request),
        endpoint=request.url.path,
        details={"role": db_user.role},
        severity="INFO",
    )

    return AdminUser(identity=identity, db_user=db_user)


def verify_admin_user(
    admin: AdminUser = Depends(get_admin_user),
) -> AdminUser:
    """Dependency that verifies admin access and returns AdminUser.

    Use this as a dependency in admin endpoints:

        @router.get("/admin/something")
        async def admin_endpoint(admin: AdminUser = Depends(verify_admin_user)):
            # admin.user_id, admin.email available
            ...

    Or for backward compatibility with dependency-only verification:

        @router.get("/admin/something", dependencies=[Depends(verify_admin_user)])
        async def admin_endpoint():
            ...
    """
    return admin


# Backward compatibility alias
verify_admin_api_key = verify_admin_user


async def get_admin_user_or_redirect(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> AdminUser:
    """Get admin user or raise AuthRedirectError for redirect to login.

    Use this for HTML pages that should redirect to login instead of 401.
    API endpoints should use get_admin_user instead.
    """
    # Try header authentication first (Open WebUI)
    identity = extract_user_from_headers(request)

    # If no headers, try JWT cookie (direct OAuth)
    if not identity:
        from core.auth.admin_session import get_jwt_from_request, verify_admin_jwt
        from core.runtime.config import get_settings

        settings = get_settings()
        if settings.admin_jwt_secret:
            jwt_token = get_jwt_from_request(request)
            if jwt_token:
                payload = verify_admin_jwt(jwt_token, settings.admin_jwt_secret)
                if payload:
                    identity = UserIdentity(
                        email=payload.get("email", ""),
                        name=payload.get("name"),
                        openwebui_id=None,
                        role=payload.get("role", "user"),
                    )

    if not identity:
        raise AuthRedirectError()

    # Look up user in database
    stmt = select(User).where(User.email == identity.email.lower())
    result = await session.execute(stmt)
    db_user = result.scalar_one_or_none()

    if not db_user:
        raise AuthRedirectError()

    if not db_user.is_active:
        raise AuthRedirectError()

    # SECURITY: Database role is authoritative - never trust header/JWT claims
    if db_user.role != "admin":
        raise AuthRedirectError()

    return AdminUser(identity=identity, db_user=db_user)


def require_admin_or_redirect(
    admin: AdminUser = Depends(get_admin_user_or_redirect),
) -> AdminUser:
    """Dependency for HTML pages that redirects to login if not authenticated.

    Use this for admin portal HTML pages:

        @router.get("/admin/dashboard", response_class=HTMLResponse)
        async def dashboard(admin: AdminUser = Depends(require_admin_or_redirect)):
            ...

    For API endpoints that should return 401, use verify_admin_user instead.
    """
    return admin


class AuthenticatedUser:
    """Authenticated user (not necessarily admin)."""

    def __init__(self, identity: UserIdentity, db_user: User) -> None:
        self.identity = identity
        self.db_user = db_user

    @property
    def user_id(self) -> UUID:
        """Return the database user ID."""
        return self.db_user.id

    @property
    def email(self) -> str:
        """Return the user's email."""
        return self.db_user.email


async def get_authenticated_user(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> AuthenticatedUser:
    """Extract and verify any authenticated user from headers.

    Unlike get_admin_user, this does NOT require admin role.
    Use for endpoints that any logged-in user can access.

    Raises:
        HTTPException 401: Missing or invalid user headers
        HTTPException 403: User account is disabled
    """
    identity = extract_user_from_headers(request)
    if not identity:
        log_security_event(
            event_type=AUTH_FAILURE,
            ip_address=get_client_ip(request),
            endpoint=request.url.path,
            details={"reason": "Missing X-OpenWebUI-User-Email header"},
            severity="WARNING",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Missing X-OpenWebUI-User-Email header.",
            headers={"WWW-Authenticate": "OpenWebUI"},
        )

    stmt = select(User).where(User.email == identity.email.lower())
    result = await session.execute(stmt)
    db_user = result.scalar_one_or_none()

    if not db_user:
        log_security_event(
            event_type=AUTH_FAILURE,
            user_email=identity.email,
            ip_address=get_client_ip(request),
            endpoint=request.url.path,
            details={"reason": "User not found in database"},
            severity="WARNING",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"User {identity.email} not found. Login via Open WebUI first.",
        )

    if not db_user.is_active:
        log_security_event(
            event_type=AUTH_FAILURE,
            user_email=identity.email,
            user_id=str(db_user.id),
            ip_address=get_client_ip(request),
            endpoint=request.url.path,
            details={"reason": "User account is disabled"},
            severity="WARNING",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled.",
        )

    return AuthenticatedUser(identity=identity, db_user=db_user)


def verify_user(
    user: AuthenticatedUser = Depends(get_authenticated_user),
) -> AuthenticatedUser:
    """Dependency that verifies user is authenticated.

    Use this for endpoints that any logged-in user can access:

        @router.get("/something", dependencies=[Depends(verify_user)])
        async def endpoint():
            ...
    """
    return user


__all__ = [
    "AdminUser",
    "AuthenticatedUser",
    "AuthRedirectError",
    "get_admin_user",
    "get_admin_user_or_redirect",
    "get_authenticated_user",
    "require_admin_or_redirect",
    "verify_admin_user",
    "verify_admin_api_key",
    "verify_user",
]
