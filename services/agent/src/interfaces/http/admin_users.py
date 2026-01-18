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
from interfaces.http.admin_auth import verify_admin_api_key

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/users",
    tags=["admin", "users"],
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
async def users_dashboard() -> str:
    """User management dashboard."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Users - Admin</title>
    <style>
        :root { --primary: #3b82f6; --bg: #f8fafc; --card: #fff; --border: #e2e8f0; --text: #1e293b; --muted: #64748b; --success: #10b981; --error: #ef4444; }
        body { font-family: system-ui, sans-serif; margin: 0; background: var(--bg); color: var(--text); }
        .header { background: linear-gradient(135deg, #1e293b, #334155); color: white; padding: 24px; }
        .header h1 { margin: 0 0 4px 0; font-size: 20px; }
        .header p { margin: 0; opacity: 0.8; font-size: 13px; }
        .nav { padding: 8px 24px; background: var(--card); border-bottom: 1px solid var(--border); }
        .nav a { color: var(--primary); text-decoration: none; font-size: 13px; }
        .container { max-width: 1200px; margin: 24px auto; padding: 0 24px; }
        .stats { display: flex; gap: 16px; margin-bottom: 24px; }
        .stat-box { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px 20px; flex: 1; }
        .stat-value { font-size: 28px; font-weight: 600; color: var(--primary); }
        .stat-label { color: var(--muted); font-size: 13px; margin-top: 4px; }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 16px; }
        .card h2 { margin: 0 0 16px 0; font-size: 16px; display: flex; justify-content: space-between; align-items: center; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid var(--border); }
        th { background: #f8f9fa; font-weight: 600; font-size: 13px; }
        tr:hover { background: #f8f9fa; }
        .badge { display: inline-block; padding: 3px 10px; border-radius: 4px; font-size: 11px; font-weight: 500; }
        .badge-admin { background: #dbeafe; color: #1e40af; }
        .badge-user { background: #e5e7eb; color: #374151; }
        .status-active { color: var(--success); }
        .status-inactive { color: var(--error); }
        .loading { color: var(--muted); font-style: italic; padding: 20px; text-align: center; }
        .btn { padding: 6px 12px; border-radius: 4px; font-size: 12px; cursor: pointer; border: 1px solid var(--border); background: var(--card); }
        .btn:hover { background: var(--bg); }
    </style>
</head>
<body>
    <div class="header">
        <h1>User Management</h1>
        <p>Manage user accounts and permissions</p>
    </div>
    <div class="nav"><a href="/admin/">&larr; Back to Admin Portal</a></div>
    <div class="container">
        <div class="stats">
            <div class="stat-box">
                <div class="stat-value" id="totalUsers">0</div>
                <div class="stat-label">Total Users</div>
            </div>
            <div class="stat-box">
                <div class="stat-value" id="adminUsers">0</div>
                <div class="stat-label">Admins</div>
            </div>
            <div class="stat-box">
                <div class="stat-value" id="activeUsers">0</div>
                <div class="stat-label">Active Users</div>
            </div>
        </div>
        <div class="card">
            <h2>
                <span>All Users</span>
                <button class="btn" onclick="loadUsers()">Refresh</button>
            </h2>
            <table id="usersTable">
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
    </div>
    <script>
        async function loadUsers() {
            try {
                const res = await fetch('/admin/users/list');
                const users = await res.json();
                renderUsers(users);
            } catch (e) {
                document.getElementById('usersBody').innerHTML = '<tr><td colspan="7" style="color: var(--error); text-align: center;">Failed to load users</td></tr>';
            }
        }

        function renderUsers(users) {
            const totalUsers = users.length;
            const adminUsers = users.filter(u => u.role === 'admin').length;
            const activeUsers = users.filter(u => u.is_active).length;

            document.getElementById('totalUsers').textContent = totalUsers;
            document.getElementById('adminUsers').textContent = adminUsers;
            document.getElementById('activeUsers').textContent = activeUsers;

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
                const lastLogin = new Date(u.last_login_at).toLocaleString();
                const created = new Date(u.created_at).toLocaleDateString();

                return `
                    <tr>
                        <td>${escapeHtml(u.email)}</td>
                        <td>${escapeHtml(u.display_name) || '-'}</td>
                        <td>${roleBadge}</td>
                        <td>${status}</td>
                        <td>${u.context_count}</td>
                        <td>${lastLogin}</td>
                        <td>${created}</td>
                    </tr>
                `;
            }).join('');
        }

        function escapeHtml(str) {
            if (!str) return '';
            const div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        }

        loadUsers();
    </script>
</body>
</html>"""


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
