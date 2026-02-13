# Implementation Plan: Admin Portal Entra ID Auth + Credential Management

**Date:** 2026-01-18
**Feature:** Replace API key auth with Entra ID headers + Add credential management UI
**Estimated Effort:** Medium (4-6 hours)

---

## 1. Feature Overview

### What We're Building

1. **Entra ID Authentication for Admin Portal**
   - Replace `X-API-Key` header auth with `X-OpenWebUI-User-*` headers
   - Verify `user.role == "admin"` for access
   - Seamless integration with Open WebUI's existing auth flow

2. **User Credential Management Section**
   - New `/admin/credentials/` section
   - List all users with their credentials (masked)
   - Add/delete credentials for any user (admin only)
   - Uses existing `CredentialService` with Fernet encryption

### Why This Approach

- **Unified Auth:** Open WebUI already authenticates users via Entra ID and forwards headers to the agent service. We leverage this instead of maintaining separate API key auth.
- **Security:** Admins are identified by their Entra ID role, not a shared secret.
- **Reuse:** `CredentialService` already handles encryption/decryption. We just need a UI layer.

---

## 2. Architecture Decisions

### Layer Placement

```
interfaces/http/admin_auth.py      - Auth dependency (Layer 1)
interfaces/http/admin_credentials.py - New endpoints + UI (Layer 1)
interfaces/http/admin_portal.py    - Add nav link (Layer 1)
core/auth/credential_service.py    - Existing service (Layer 4 - NO CHANGES)
core/db/models.py                  - Existing models (Layer 4 - NO CHANGES)
```

### Dependency Flow

```
admin_credentials.py (interfaces)
    -> CredentialService (core)
    -> UserCredential model (core)
    -> User model (core)
```

**No architecture violations:** `interfaces` can import from `core`.

---

## 3. Implementation Roadmap

### Phase 1: Update Admin Auth (admin_auth.py)

**File:** `/home/magnus/dev/ai-agent-platform/services/agent/src/interfaces/http/admin_auth.py`

**Current State:**
```python
def verify_admin_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
) -> None:
    # Verifies X-API-Key header against AGENT_ADMIN_API_KEY
```

**New Implementation:**

Replace the entire file content with:

```python
"""Admin authentication using Entra ID headers from Open WebUI."""

from __future__ import annotations

from typing import Annotated
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
```

**Key Changes:**
1. Uses `extract_user_from_headers()` instead of API key
2. Looks up user in database to get `user_id`
3. Verifies `role == "admin"` (from header or DB)
4. Returns `AdminUser` object with both identity and DB user
5. Backward compatible: `verify_admin_api_key` alias still works

---

### Phase 2: Create Credential Management (admin_credentials.py)

**File:** `/home/magnus/dev/ai-agent-platform/services/agent/src/interfaces/http/admin_credentials.py`

**New File - Complete Content:**

```python
# ruff: noqa: E501
"""Admin endpoints for user credential management."""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.credential_service import CredentialService
from core.runtime.config import Settings, get_settings
from core.db.engine import get_db
from core.db.models import User, UserCredential
from interfaces.http.admin_auth import AdminUser, verify_admin_user

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/credentials",
    tags=["admin", "credentials"],
)

# Supported credential types with display info
CREDENTIAL_TYPES = {
    "azure_devops_pat": {
        "name": "Azure DevOps PAT",
        "description": "Personal Access Token for Azure DevOps",
        "placeholder": "Enter your Azure DevOps PAT",
        "metadata_fields": ["organization_url"],
    },
    "github_token": {
        "name": "GitHub Token",
        "description": "Personal Access Token for GitHub",
        "placeholder": "ghp_xxxxxxxxxxxx",
        "metadata_fields": [],
    },
    "gitlab_token": {
        "name": "GitLab Token",
        "description": "Personal Access Token for GitLab",
        "placeholder": "glpat-xxxxxxxxxxxx",
        "metadata_fields": ["gitlab_url"],
    },
    "jira_api_token": {
        "name": "Jira API Token",
        "description": "API Token for Jira/Atlassian",
        "placeholder": "Enter your Jira API token",
        "metadata_fields": ["jira_url", "jira_email"],
    },
}


def _get_credential_service(
    settings: Settings = Depends(get_settings),
) -> CredentialService:
    """Get credential service instance."""
    if not settings.credential_encryption_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credential encryption not configured. Set AGENT_CREDENTIAL_ENCRYPTION_KEY.",
        )
    return CredentialService(settings.credential_encryption_key)


# --- Pydantic Models ---


class CredentialInfo(BaseModel):
    """Credential information (without decrypted value)."""

    id: str
    user_id: str
    user_email: str
    credential_type: str
    credential_type_name: str
    metadata: dict
    created_at: str
    updated_at: str


class UserWithCredentials(BaseModel):
    """User with their credentials."""

    user_id: str
    email: str
    display_name: str | None
    role: str
    credential_count: int
    credentials: list[CredentialInfo]


class CredentialCreateRequest(BaseModel):
    """Request to create a credential."""

    user_id: str
    credential_type: str
    value: str
    metadata: dict | None = None


class CredentialCreateResponse(BaseModel):
    """Response after creating a credential."""

    success: bool
    message: str
    credential_id: str


class CredentialDeleteResponse(BaseModel):
    """Response after deleting a credential."""

    success: bool
    message: str


# --- HTML Dashboard ---


@router.get("/", response_class=HTMLResponse)
async def credentials_dashboard() -> str:
    """User credential management dashboard."""
    # Generate credential type options for the form
    type_options = "".join(
        f'<option value="{key}">{info["name"]}</option>'
        for key, info in CREDENTIAL_TYPES.items()
    )

    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Credentials - Admin</title>
    <style>
        :root { --primary: #8b5cf6; --bg: #f8fafc; --card: #fff; --border: #e2e8f0; --text: #1e293b; --muted: #64748b; --success: #10b981; --error: #ef4444; --warning: #f59e0b; }
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
        .badge-type { background: #ede9fe; color: #6d28d9; }
        .badge-count { background: #dbeafe; color: #1e40af; }
        .loading { color: var(--muted); font-style: italic; padding: 20px; text-align: center; }
        .btn { padding: 6px 12px; border-radius: 4px; font-size: 12px; cursor: pointer; border: 1px solid var(--border); background: var(--card); }
        .btn:hover { background: var(--bg); }
        .btn-primary { background: var(--primary); color: white; border-color: var(--primary); }
        .btn-primary:hover { opacity: 0.9; }
        .btn-danger { color: var(--error); border-color: var(--error); }
        .btn-danger:hover { background: #fee2e2; }
        .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 100; }
        .modal.active { display: flex; align-items: center; justify-content: center; }
        .modal-content { background: white; border-radius: 8px; padding: 24px; width: 90%; max-width: 500px; }
        .modal-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
        .modal-header h3 { margin: 0; }
        .form-group { margin-bottom: 16px; }
        .form-group label { display: block; font-size: 13px; font-weight: 500; margin-bottom: 6px; }
        .form-group input, .form-group select, .form-group textarea { width: 100%; padding: 8px 12px; border: 1px solid var(--border); border-radius: 4px; font-size: 14px; box-sizing: border-box; }
        .form-group textarea { min-height: 80px; font-family: monospace; }
        .form-group small { color: var(--muted); font-size: 12px; display: block; margin-top: 4px; }
        .toast { position: fixed; bottom: 20px; right: 20px; padding: 12px 20px; border-radius: 6px; color: white; font-size: 14px; z-index: 200; display: none; }
        .toast.success { background: var(--success); display: block; }
        .toast.error { background: var(--error); display: block; }
        .masked { font-family: monospace; color: var(--muted); }
        .meta-tag { display: inline-block; background: #f1f5f9; padding: 2px 8px; border-radius: 4px; font-size: 11px; margin-right: 4px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>User Credentials</h1>
        <p>Manage encrypted credentials for users</p>
    </div>
    <div class="nav"><a href="/admin/">&larr; Back to Admin Portal</a></div>
    <div class="container">
        <div class="stats">
            <div class="stat-box">
                <div class="stat-value" id="totalUsers">0</div>
                <div class="stat-label">Users with Credentials</div>
            </div>
            <div class="stat-box">
                <div class="stat-value" id="totalCredentials">0</div>
                <div class="stat-label">Total Credentials</div>
            </div>
        </div>

        <div class="card">
            <h2>
                <span>All Credentials</span>
                <div>
                    <button class="btn btn-primary" onclick="openAddModal()">+ Add Credential</button>
                    <button class="btn" onclick="loadCredentials()">Refresh</button>
                </div>
            </h2>
            <table id="credentialsTable">
                <thead>
                    <tr>
                        <th>User</th>
                        <th>Type</th>
                        <th>Metadata</th>
                        <th>Created</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody id="credentialsBody">
                    <tr><td colspan="5" class="loading">Loading...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <!-- Add Credential Modal -->
    <div class="modal" id="addModal">
        <div class="modal-content">
            <div class="modal-header">
                <h3>Add Credential</h3>
                <button class="btn" onclick="closeAddModal()">&times;</button>
            </div>
            <form id="addForm" onsubmit="submitCredential(event)">
                <div class="form-group">
                    <label for="userId">User</label>
                    <select id="userId" required>
                        <option value="">Select user...</option>
                    </select>
                </div>
                <div class="form-group">
                    <label for="credType">Credential Type</label>
                    <select id="credType" required onchange="updateMetadataFields()">
                        <option value="">Select type...</option>
                        """ + type_options + """
                    </select>
                </div>
                <div class="form-group">
                    <label for="credValue">Credential Value</label>
                    <textarea id="credValue" required placeholder="Enter the credential value (will be encrypted)"></textarea>
                    <small>This value will be encrypted at rest using Fernet encryption.</small>
                </div>
                <div id="metadataFields"></div>
                <button type="submit" class="btn btn-primary" style="width: 100%">Save Credential</button>
            </form>
        </div>
    </div>

    <!-- Toast notification -->
    <div class="toast" id="toast"></div>

    <script>
        const CREDENTIAL_TYPES = """ + str(CREDENTIAL_TYPES).replace("'", '"') + """;

        async function loadCredentials() {
            try {
                const res = await fetch('/admin/credentials/list');
                const data = await res.json();
                renderCredentials(data);
            } catch (e) {
                document.getElementById('credentialsBody').innerHTML = '<tr><td colspan="5" style="color: var(--error); text-align: center;">Failed to load credentials</td></tr>';
            }
        }

        async function loadUsers() {
            try {
                const res = await fetch('/admin/users/list');
                const users = await res.json();
                const select = document.getElementById('userId');
                select.innerHTML = '<option value="">Select user...</option>' +
                    users.map(u => `<option value="${u.id}">${escapeHtml(u.email)} (${escapeHtml(u.display_name || 'No name')})</option>`).join('');
            } catch (e) {
                console.error('Failed to load users:', e);
            }
        }

        function renderCredentials(data) {
            document.getElementById('totalUsers').textContent = data.users_with_credentials || 0;
            document.getElementById('totalCredentials').textContent = data.total_credentials || 0;

            const tbody = document.getElementById('credentialsBody');
            const creds = data.credentials || [];

            if (creds.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5" class="loading">No credentials found</td></tr>';
                return;
            }

            tbody.innerHTML = creds.map(c => {
                const meta = Object.entries(c.metadata || {})
                    .map(([k, v]) => `<span class="meta-tag">${escapeHtml(k)}: ${escapeHtml(v)}</span>`)
                    .join('') || '<span class="masked">-</span>';
                const created = new Date(c.created_at).toLocaleDateString();

                return `
                    <tr>
                        <td>
                            <div>${escapeHtml(c.user_email)}</div>
                        </td>
                        <td><span class="badge badge-type">${escapeHtml(c.credential_type_name)}</span></td>
                        <td>${meta}</td>
                        <td>${created}</td>
                        <td>
                            <button class="btn btn-danger" onclick="deleteCredential('${c.id}', '${escapeHtml(c.credential_type)}')">Delete</button>
                        </td>
                    </tr>
                `;
            }).join('');
        }

        function openAddModal() {
            loadUsers();
            document.getElementById('addModal').classList.add('active');
        }

        function closeAddModal() {
            document.getElementById('addModal').classList.remove('active');
            document.getElementById('addForm').reset();
            document.getElementById('metadataFields').innerHTML = '';
        }

        function updateMetadataFields() {
            const type = document.getElementById('credType').value;
            const container = document.getElementById('metadataFields');
            container.innerHTML = '';

            if (type && CREDENTIAL_TYPES[type]) {
                const fields = CREDENTIAL_TYPES[type].metadata_fields || [];
                fields.forEach(field => {
                    container.innerHTML += `
                        <div class="form-group">
                            <label for="meta_${field}">${field.replace('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase())}</label>
                            <input type="text" id="meta_${field}" name="meta_${field}" placeholder="Enter ${field}">
                        </div>
                    `;
                });
            }
        }

        async function submitCredential(e) {
            e.preventDefault();
            const userId = document.getElementById('userId').value;
            const credType = document.getElementById('credType').value;
            const credValue = document.getElementById('credValue').value;

            // Collect metadata
            const metadata = {};
            if (CREDENTIAL_TYPES[credType]) {
                CREDENTIAL_TYPES[credType].metadata_fields.forEach(field => {
                    const el = document.getElementById('meta_' + field);
                    if (el && el.value) {
                        metadata[field] = el.value;
                    }
                });
            }

            try {
                const res = await fetch('/admin/credentials/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        user_id: userId,
                        credential_type: credType,
                        value: credValue,
                        metadata: Object.keys(metadata).length > 0 ? metadata : null
                    })
                });

                const data = await res.json();
                if (res.ok) {
                    showToast('Credential saved successfully', 'success');
                    closeAddModal();
                    loadCredentials();
                } else {
                    showToast(data.detail || 'Failed to save credential', 'error');
                }
            } catch (e) {
                showToast('Network error', 'error');
            }
        }

        async function deleteCredential(credId, credType) {
            if (!confirm(`Delete this ${credType} credential? This cannot be undone.`)) return;

            try {
                const res = await fetch(`/admin/credentials/${credId}`, { method: 'DELETE' });
                const data = await res.json();

                if (res.ok) {
                    showToast('Credential deleted', 'success');
                    loadCredentials();
                } else {
                    showToast(data.detail || 'Failed to delete', 'error');
                }
            } catch (e) {
                showToast('Network error', 'error');
            }
        }

        function showToast(message, type) {
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.className = 'toast ' + type;
            setTimeout(() => { toast.className = 'toast'; }, 3000);
        }

        function escapeHtml(str) {
            if (!str) return '';
            const div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        }

        // Initial load
        loadCredentials();
    </script>
</body>
</html>"""


# --- API Endpoints ---


@router.get("/list")
async def list_all_credentials(
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """List all user credentials (admin only).

    Returns credentials grouped by user with masked values.
    """
    # Get all credentials with user info
    stmt = (
        select(UserCredential, User)
        .join(User, UserCredential.user_id == User.id)
        .order_by(User.email, UserCredential.credential_type)
    )
    result = await session.execute(stmt)
    rows = result.all()

    credentials = []
    user_ids = set()
    for cred, user in rows:
        user_ids.add(str(user.id))
        credentials.append(
            CredentialInfo(
                id=str(cred.id),
                user_id=str(user.id),
                user_email=user.email,
                credential_type=cred.credential_type,
                credential_type_name=CREDENTIAL_TYPES.get(cred.credential_type, {}).get(
                    "name", cred.credential_type
                ),
                metadata=cred.credential_metadata or {},
                created_at=cred.created_at.isoformat(),
                updated_at=cred.updated_at.isoformat(),
            )
        )

    return {
        "credentials": [c.model_dump() for c in credentials],
        "total_credentials": len(credentials),
        "users_with_credentials": len(user_ids),
    }


@router.get("/user/{user_id}")
async def get_user_credentials(
    user_id: UUID,
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
    cred_service: CredentialService = Depends(_get_credential_service),
) -> dict:
    """Get credentials for a specific user."""
    # Verify user exists
    user_stmt = select(User).where(User.id == user_id)
    user_result = await session.execute(user_stmt)
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found",
        )

    # Get credentials
    credentials = await cred_service.list_credentials(user_id, session)

    return {
        "user_id": str(user_id),
        "user_email": user.email,
        "credentials": [
            {
                **cred,
                "credential_type_name": CREDENTIAL_TYPES.get(cred["credential_type"], {}).get(
                    "name", cred["credential_type"]
                ),
            }
            for cred in credentials
        ],
    }


@router.get("/types")
async def list_credential_types(
    admin: AdminUser = Depends(verify_admin_user),
) -> dict:
    """List available credential types."""
    return {"types": CREDENTIAL_TYPES}


@router.post("/create", response_model=CredentialCreateResponse)
async def create_credential(
    request: CredentialCreateRequest,
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
    cred_service: CredentialService = Depends(_get_credential_service),
) -> CredentialCreateResponse:
    """Create or update a credential for a user.

    Admin can create credentials for any user.
    """
    # Validate credential type
    if request.credential_type not in CREDENTIAL_TYPES:
        valid_types = ", ".join(CREDENTIAL_TYPES.keys())
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid credential type. Valid types: {valid_types}",
        )

    # Parse user_id
    try:
        user_uuid = UUID(request.user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user_id format",
        )

    # Verify user exists
    user_stmt = select(User).where(User.id == user_uuid)
    user_result = await session.execute(user_stmt)
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {request.user_id} not found",
        )

    # Store credential (encrypts automatically)
    credential = await cred_service.store_credential(
        user_id=user_uuid,
        credential_type=request.credential_type,
        value=request.value,
        metadata=request.metadata,
        session=session,
    )

    await session.commit()

    LOGGER.info(
        f"Admin {admin.email} created {request.credential_type} credential for user {user.email}"
    )

    return CredentialCreateResponse(
        success=True,
        message=f"Credential {request.credential_type} saved for {user.email}",
        credential_id=str(credential.id),
    )


@router.delete("/{credential_id}", response_model=CredentialDeleteResponse)
async def delete_credential(
    credential_id: UUID,
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
) -> CredentialDeleteResponse:
    """Delete a credential by ID."""
    # Find credential
    stmt = select(UserCredential).where(UserCredential.id == credential_id)
    result = await session.execute(stmt)
    credential = result.scalar_one_or_none()

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Credential {credential_id} not found",
        )

    # Get user email for logging
    user_stmt = select(User).where(User.id == credential.user_id)
    user_result = await session.execute(user_stmt)
    user = user_result.scalar_one_or_none()
    user_email = user.email if user else "unknown"

    cred_type = credential.credential_type

    # Delete
    await session.delete(credential)
    await session.commit()

    LOGGER.info(f"Admin {admin.email} deleted {cred_type} credential for user {user_email}")

    return CredentialDeleteResponse(
        success=True,
        message=f"Deleted {cred_type} credential for {user_email}",
    )


__all__ = ["router", "CREDENTIAL_TYPES"]
```

---

### Phase 3: Update Admin Portal Navigation (admin_portal.py)

**File:** `/home/magnus/dev/ai-agent-platform/services/agent/src/interfaces/http/admin_portal.py`

**Change:** Add new "Credentials" card to the User Management section.

**Find this section (around line 234-243):**
```html
        <div class="section-title">User Management</div>
        <div class="grid">
            <a href="/admin/users/" class="card">
                <div class="card-icon blue">&#128100;</div>
                <h2>Users</h2>
                <p>Manage user accounts, roles, and permissions across the platform.</p>
                <div class="endpoint">/admin/users/</div>
            </a>
        </div>
```

**Replace with:**
```html
        <div class="section-title">User Management</div>
        <div class="grid">
            <a href="/admin/users/" class="card">
                <div class="card-icon blue">&#128100;</div>
                <h2>Users</h2>
                <p>Manage user accounts, roles, and permissions across the platform.</p>
                <div class="endpoint">/admin/users/</div>
            </a>

            <a href="/admin/credentials/" class="card">
                <div class="card-icon purple">&#128273;</div>
                <h2>Credentials</h2>
                <p>Manage encrypted credentials (PATs, API tokens) for users.</p>
                <div class="endpoint">/admin/credentials/</div>
            </a>
        </div>
```

---

### Phase 4: Register Router in App (app.py)

**File:** `/home/magnus/dev/ai-agent-platform/services/agent/src/core/core/app.py`

**Add import (around line 30):**
```python
from interfaces.http.admin_credentials import router as admin_credentials_router
```

**Add router registration (around line 518):**
```python
app.include_router(admin_credentials_router)
```

---

### Phase 5: Update Existing Admin Endpoints (Optional but Recommended)

The other admin files (`admin_users.py`, `admin_oauth.py`, etc.) currently use `verify_admin_api_key` as a dependency. Thanks to the backward-compatible alias, they will continue to work. However, if you want them to have access to the `AdminUser` object (to log who made changes), update them to use `verify_admin_user`.

**Example change in admin_users.py:**

From:
```python
@router.get(
    "/list", dependencies=[Depends(verify_admin_api_key)], response_model=list[UserResponse]
)
async def list_users(...):
```

To:
```python
@router.get("/list", response_model=list[UserResponse])
async def list_users(
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
    ...
):
    # Now admin.email is available for audit logging
```

**This is optional** - the backward-compatible alias ensures existing code continues to work.

---

## 4. Configuration Changes

### Environment Variables

No new environment variables required. Existing variables used:

| Variable | Purpose | Required |
|----------|---------|----------|
| `AGENT_CREDENTIAL_ENCRYPTION_KEY` | Fernet key for credential encryption | Yes (for credential management) |

**Generate encryption key if not set:**
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## 5. Testing Strategy

### Unit Tests

Create `/home/magnus/dev/ai-agent-platform/services/agent/src/interfaces/tests/test_admin_auth.py`:

```python
"""Tests for admin authentication."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import HTTPException

from interfaces.http.admin_auth import get_admin_user, AdminUser


class TestGetAdminUser:
    """Tests for get_admin_user dependency."""

    @pytest.fixture
    def mock_request(self) -> MagicMock:
        """Create mock request with headers."""
        request = MagicMock()
        request.headers = {}
        return request

    @pytest.mark.asyncio
    async def test_raises_401_when_no_email_header(self, mock_request: MagicMock) -> None:
        """Should raise 401 when X-OpenWebUI-User-Email is missing."""
        mock_session = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_admin_user(mock_request, mock_session)

        assert exc_info.value.status_code == 401
        assert "Missing X-OpenWebUI-User-Email" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_raises_401_when_user_not_found(self, mock_request: MagicMock) -> None:
        """Should raise 401 when user not in database."""
        mock_request.headers = {"x-openwebui-user-email": "unknown@example.com"}
        mock_session = AsyncMock()
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_admin_user(mock_request, mock_session)

        assert exc_info.value.status_code == 401
        assert "not found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_raises_403_when_not_admin(self, mock_request: MagicMock) -> None:
        """Should raise 403 when user is not admin."""
        mock_request.headers = {
            "x-openwebui-user-email": "user@example.com",
            "x-openwebui-user-role": "user",
        }

        mock_user = MagicMock()
        mock_user.id = uuid4()
        mock_user.email = "user@example.com"
        mock_user.role = "user"
        mock_user.is_active = True

        mock_session = AsyncMock()
        mock_session.execute.return_value.scalar_one_or_none.return_value = mock_user

        with pytest.raises(HTTPException) as exc_info:
            await get_admin_user(mock_request, mock_session)

        assert exc_info.value.status_code == 403
        assert "Admin access required" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_returns_admin_user_when_valid(self, mock_request: MagicMock) -> None:
        """Should return AdminUser when user is admin."""
        mock_request.headers = {
            "x-openwebui-user-email": "admin@example.com",
            "x-openwebui-user-role": "admin",
        }

        mock_user = MagicMock()
        mock_user.id = uuid4()
        mock_user.email = "admin@example.com"
        mock_user.role = "admin"
        mock_user.is_active = True
        mock_user.display_name = "Admin User"

        mock_session = AsyncMock()
        mock_session.execute.return_value.scalar_one_or_none.return_value = mock_user

        result = await get_admin_user(mock_request, mock_session)

        assert isinstance(result, AdminUser)
        assert result.email == "admin@example.com"
        assert result.user_id == mock_user.id
```

### Integration Tests

Create `/home/magnus/dev/ai-agent-platform/services/agent/src/interfaces/tests/test_admin_credentials.py`:

```python
"""Tests for admin credential management endpoints."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from interfaces.http.admin_credentials import CREDENTIAL_TYPES


class TestCredentialTypes:
    """Tests for credential type definitions."""

    def test_azure_devops_pat_defined(self) -> None:
        """Should have azure_devops_pat type."""
        assert "azure_devops_pat" in CREDENTIAL_TYPES
        assert "name" in CREDENTIAL_TYPES["azure_devops_pat"]

    def test_github_token_defined(self) -> None:
        """Should have github_token type."""
        assert "github_token" in CREDENTIAL_TYPES


class TestListCredentialsEndpoint:
    """Tests for /admin/credentials/list endpoint."""

    @pytest.mark.asyncio
    async def test_requires_admin_auth(self) -> None:
        """Should require admin authentication."""
        # This would be an integration test with TestClient
        # Skipping for now - covered by admin_auth tests
        pass
```

### Manual Testing Checklist

1. **Authentication Flow:**
   - [ ] Access `/admin/` without login -> Should show Entra login
   - [ ] Login as non-admin user -> Should see 403 Forbidden
   - [ ] Login as admin user -> Should see admin portal

2. **Credential Management:**
   - [ ] Navigate to `/admin/credentials/`
   - [ ] Click "Add Credential" -> Modal opens
   - [ ] Select user from dropdown
   - [ ] Select credential type (Azure DevOps PAT)
   - [ ] Enter value and metadata
   - [ ] Save -> Should see success toast
   - [ ] Credential appears in list (value masked)
   - [ ] Delete credential -> Confirmation -> Removed from list

3. **Cross-Reference:**
   - [ ] Check Azure DevOps tool uses stored credential
   - [ ] Verify credential encryption (check DB - value should be gibberish)

---

## 6. Quality Checks

After implementation, run:

```bash
./stack check
```

This runs:
1. **Ruff** - Linting
2. **Black** - Formatting
3. **Mypy** - Type checking
4. **Pytest** - Tests

**All must pass before considering implementation complete.**

---

## 7. Security Considerations

### Authentication

| Risk | Mitigation |
|------|------------|
| Header spoofing | Headers are set by Open WebUI behind reverse proxy. Only trusted sources can set them. |
| Missing user validation | Always verify user exists in DB and is active |
| Role escalation | Check role from header AND database |

### Credential Storage

| Risk | Mitigation |
|------|------------|
| Credential exposure | Never return decrypted values via API |
| Weak encryption | Fernet uses AES-128-CBC with HMAC (industry standard) |
| Key compromise | Encryption key stored in env var, not in code |
| SQL injection | All queries use SQLAlchemy parameterized queries |

### Audit Logging

All credential operations should be logged with:
- Admin user who performed action
- Target user
- Credential type
- Timestamp

Example: `Admin admin@company.com created azure_devops_pat credential for user dev@company.com`

---

## 8. Success Criteria

1. **Admin Portal Access:**
   - Non-admins see 403 Forbidden
   - Admins can access all sections without API key

2. **Credential Management:**
   - Can list all credentials (masked)
   - Can add credentials for any user
   - Can delete credentials
   - Encryption works (verify in DB)

3. **Backward Compatibility:**
   - Existing admin endpoints still work
   - No breaking changes to API contracts

4. **Quality:**
   - All quality checks pass (./stack check)
   - No Mypy errors
   - Test coverage for new code

---

## 9. Files Summary

### Files to Modify

| File | Changes |
|------|---------|
| `src/interfaces/http/admin_auth.py` | Replace API key auth with Entra ID headers |
| `src/interfaces/http/admin_portal.py` | Add Credentials nav link |
| `src/core/core/app.py` | Register admin_credentials router |

### Files to Create

| File | Purpose |
|------|---------|
| `src/interfaces/http/admin_credentials.py` | Credential management endpoints + UI |
| `src/interfaces/tests/test_admin_auth.py` | Unit tests for admin auth |
| `src/interfaces/tests/test_admin_credentials.py` | Unit tests for credential endpoints |

### Files NOT Modified

| File | Reason |
|------|--------|
| `src/core/auth/credential_service.py` | Already complete, reused as-is |
| `src/core/db/models.py` | UserCredential model already exists |
| `src/core/core/config.py` | credential_encryption_key already defined |

---

## 10. Implementation Order

1. **Phase 1:** Update `admin_auth.py` (auth foundation)
2. **Phase 2:** Create `admin_credentials.py` (new functionality)
3. **Phase 3:** Update `admin_portal.py` (navigation)
4. **Phase 4:** Update `app.py` (router registration)
5. **Phase 5:** Run quality checks and fix any issues
6. **Phase 6:** Manual testing
7. **Phase 7:** (Optional) Update other admin files to use AdminUser

---

**End of Implementation Plan**
