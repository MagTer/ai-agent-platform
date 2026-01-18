"""Admin authentication using Entra ID headers from Open WebUI."""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.header_auth import UserIdentity, extract_user_from_headers
from core.db.engine import get_db
from core.db.models import User


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
    """Extract and verify admin user from Entra ID headers.

    Headers expected (forwarded by Open WebUI):
        X-OpenWebUI-User-Email: user@example.com (required)
        X-OpenWebUI-User-Name: John Doe (optional)
        X-OpenWebUI-User-Id: uuid (optional)
        X-OpenWebUI-User-Role: admin (required for admin access)

    Returns:
        AdminUser with identity and database record

    Raises:
        HTTPException 401: Missing or invalid user headers
        HTTPException 403: User is not an admin
    """
    # Extract identity from headers
    identity = extract_user_from_headers(request)
    if not identity:
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"User {identity.email} not found. Login via Open WebUI first.",
        )

    if not db_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled.",
        )

    # Check admin role (from header OR database)
    # Trust header role if present, otherwise use DB role
    effective_role = identity.role or db_user.role
    if effective_role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required. Your role: " + effective_role,
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


__all__ = ["AdminUser", "get_admin_user", "verify_admin_user", "verify_admin_api_key"]
