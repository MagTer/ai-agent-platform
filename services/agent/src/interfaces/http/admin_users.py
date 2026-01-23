"""Admin endpoints for user management."""

# ruff: noqa: E501
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.db.models import User, UserContext
from interfaces.http.admin_auth import AdminUser, require_admin_or_redirect, verify_admin_api_key
from interfaces.http.admin_shared import render_admin_page

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platformadmin/users",
    tags=["platform-admin", "users"],
)


class UserResponse(BaseModel):
    """User information for admin display."""

    id: str
    email: str
    display_name: str | None
    role: str
    is_active: bool
    created_at: str
    last_login_at: str
    context_count: int


class UserUpdateRequest(BaseModel):
    """Request to update user properties."""

    role: str | None = None
    is_active: bool | None = None


# --- HTML Dashboard ---


@router.get("/", response_class=HTMLResponse)
async def users_dashboard(admin: AdminUser = Depends(require_admin_or_redirect)) -> str:
    """User management dashboard."""
    content = """
        <h1 class="page-title">User Management</h1>

        <div class="stats-grid">
            <div class="stat-box">
                <div class="stat-value" id="totalUsers">-</div>
                <div class="stat-label">Total Users</div>
            </div>
            <div class="stat-box">
                <div class="stat-value" id="adminUsers">-</div>
                <div class="stat-label">Admins</div>
            </div>
            <div class="stat-box">
                <div class="stat-value" id="activeUsers">-</div>
                <div class="stat-label">Active Users</div>
            </div>
        </div>

        <div class="card">
            <div class="card-header">
                <span class="card-title">All Users</span>
                <button class="btn btn-sm" onclick="loadUsers()">Refresh</button>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Email</th>
                        <th>Name</th>
                        <th>Role</th>
                        <th>Status</th>
                        <th>Contexts</th>
                        <th>Last Login</th>
                        <th>Created</th>
                    </tr>
                </thead>
                <tbody id="usersBody">
                    <tr><td colspan="7" class="loading">Loading...</td></tr>
                </tbody>
            </table>
        </div>
    """

    extra_css = """
        .badge-admin { background: #dbeafe; color: #1e40af; }
        .badge-user { background: #e5e7eb; color: #374151; }
        .status-active { color: var(--success); }
        .status-inactive { color: var(--error); }
    """

    extra_js = """
        async function loadUsers() {
            try {
                const res = await fetch('/platformadmin/users/list');
                const users = await res.json();
                renderUsers(users);
            } catch (e) {
                document.getElementById('usersBody').innerHTML = '<tr><td colspan="7" style="color: var(--error); text-align: center;">Failed to load users</td></tr>';
            }
        }

        function renderUsers(users) {
            document.getElementById('totalUsers').textContent = users.length;
            document.getElementById('adminUsers').textContent = users.filter(u => u.role === 'admin').length;
            document.getElementById('activeUsers').textContent = users.filter(u => u.is_active).length;

            const tbody = document.getElementById('usersBody');
            if (users.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" class="loading">No users found</td></tr>';
                return;
            }

            tbody.innerHTML = users.map(u => {
                const roleBadge = u.role === 'admin'
                    ? '<span class="badge badge-admin">Admin</span>'
                    : '<span class="badge badge-user">User</span>';
                const status = u.is_active
                    ? '<span class="status-active">Active</span>'
                    : '<span class="status-inactive">Inactive</span>';
                return `<tr>
                    <td>${escapeHtml(u.email)}</td>
                    <td>${escapeHtml(u.display_name) || '-'}</td>
                    <td>${roleBadge}</td>
                    <td>${status}</td>
                    <td>${u.context_count}</td>
                    <td>${new Date(u.last_login_at).toLocaleString()}</td>
                    <td>${new Date(u.created_at).toLocaleDateString()}</td>
                </tr>`;
            }).join('');
        }

        function escapeHtml(str) {
            if (!str) return '';
            const div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        }

        loadUsers();
    """

    return render_admin_page(
        title="Users",
        active_page="/platformadmin/users/",
        content=content,
        user_name=admin.display_name or admin.email.split("@")[0],
        user_email=admin.email,
        breadcrumbs=[("Users", "#")],
        extra_css=extra_css,
        extra_js=extra_js,
    )


# --- API Endpoints ---


@router.get(
    "/list", dependencies=[Depends(verify_admin_api_key)], response_model=list[UserResponse]
)
async def list_users(
    session: AsyncSession = Depends(get_db),
    limit: int = 100,
    offset: int = 0,
) -> list[UserResponse]:
    """List all users with their context count.

    Args:
        session: Database session
        limit: Maximum number of users to return
        offset: Number of users to skip

    Returns:
        List of users with context counts

    Security:
        Requires admin API key via X-API-Key header
    """
    # Query users with context count
    stmt = (
        select(
            User,
            func.count(UserContext.id).label("context_count"),
        )
        .outerjoin(UserContext, UserContext.user_id == User.id)
        .group_by(User.id)
        .order_by(User.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    rows = result.all()

    return [
        UserResponse(
            id=str(user.id),
            email=user.email,
            display_name=user.display_name,
            role=user.role,
            is_active=user.is_active,
            created_at=user.created_at.isoformat(),
            last_login_at=user.last_login_at.isoformat(),
            context_count=context_count,
        )
        for user, context_count in rows
    ]


@router.get("/{user_id}", dependencies=[Depends(verify_admin_api_key)], response_model=UserResponse)
async def get_user(
    user_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Get a specific user.

    Args:
        user_id: User UUID
        session: Database session

    Returns:
        User information with context count

    Raises:
        HTTPException: 404 if user not found

    Security:
        Requires admin API key via X-API-Key header
    """
    stmt = (
        select(
            User,
            func.count(UserContext.id).label("context_count"),
        )
        .outerjoin(UserContext, UserContext.user_id == User.id)
        .where(User.id == user_id)
        .group_by(User.id)
    )
    result = await session.execute(stmt)
    row = result.one_or_none()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user, context_count = row
    return UserResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at.isoformat(),
        last_login_at=user.last_login_at.isoformat(),
        context_count=context_count,
    )


@router.patch(
    "/{user_id}", dependencies=[Depends(verify_admin_api_key)], response_model=UserResponse
)
async def update_user(
    user_id: UUID,
    update: UserUpdateRequest,
    session: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Update user role or active status.

    Args:
        user_id: User UUID
        update: User update parameters
        session: Database session

    Returns:
        Updated user information

    Raises:
        HTTPException: 404 if user not found
        HTTPException: 400 if invalid role specified

    Security:
        Requires admin API key via X-API-Key header
    """
    stmt = select(User).where(User.id == user_id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if update.role is not None:
        if update.role not in ("user", "admin"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Role must be 'user' or 'admin'",
            )
        user.role = update.role

    if update.is_active is not None:
        user.is_active = update.is_active

    await session.commit()
    LOGGER.info(f"Updated user {user_id}: role={user.role}, is_active={user.is_active}")

    # Get context count for response
    count_stmt = select(func.count(UserContext.id)).where(UserContext.user_id == user_id)
    count_result = await session.execute(count_stmt)
    context_count = count_result.scalar() or 0

    return UserResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at.isoformat(),
        last_login_at=user.last_login_at.isoformat(),
        context_count=context_count,
    )


@router.delete("/{user_id}", dependencies=[Depends(verify_admin_api_key)])
async def delete_user(
    user_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Delete a user and their contexts.

    This will cascade delete all UserContext associations.

    Args:
        user_id: User UUID
        session: Database session

    Returns:
        Success confirmation

    Raises:
        HTTPException: 404 if user not found

    Security:
        Requires admin API key via X-API-Key header
    """
    stmt = select(User).where(User.id == user_id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    await session.delete(user)
    await session.commit()
    LOGGER.info(f"Deleted user {user_id}")

    return {"status": "deleted", "user_id": str(user_id)}


__all__ = ["router"]
