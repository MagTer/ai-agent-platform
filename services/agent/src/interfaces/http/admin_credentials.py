# ruff: noqa: E501
"""Admin endpoints for credential management (context-scoped)."""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from shared.sanitize import sanitize_log
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.credential_service import CredentialService
from core.db.engine import get_db
from core.db.models import Context, UserCredential
from core.observability.security_logger import (
    CREDENTIAL_CREATED,
    CREDENTIAL_DELETED,
    get_client_ip,
    log_security_event,
)
from core.runtime.config import Settings, get_settings
from interfaces.http.admin_auth import AdminUser, require_admin_or_redirect, verify_admin_user
from interfaces.http.admin_shared import UTF8HTMLResponse, render_admin_page
from interfaces.http.csrf import require_csrf

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platformadmin/credentials",
    tags=["admin", "credentials"],
)

# Supported credential types with display info
CREDENTIAL_TYPES: dict[str, dict[str, Any]] = {
    "azure_devops_pat": {
        "name": "Azure DevOps PAT",
        "description": "Personal Access Token for Azure DevOps",
        "placeholder": "Enter your Azure DevOps PAT",
        "metadata_fields": [
            {
                "name": "organization_url",
                "label": "DevOps URL (including project)",
                "placeholder": "https://dev.azure.com/YourOrg/YourProject",
                "pattern": r"^https://dev\.azure\.com/[^/]+(/[^/]+)?/?$",
                "pattern_error": "Must be a valid Azure DevOps URL (e.g., https://dev.azure.com/Org/Project)",
                "required": True,
            }
        ],
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
    context_id: str
    context_name: str
    credential_type: str
    credential_type_name: str
    metadata: dict
    created_at: str
    updated_at: str


class CredentialCreateRequest(BaseModel):
    """Request to create a credential."""

    context_id: str
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


@router.get("/", response_class=UTF8HTMLResponse)
async def credentials_dashboard(admin: AdminUser = Depends(require_admin_or_redirect)) -> str:
    """Credential management dashboard."""
    # Generate credential type options for the form
    type_options = "".join(
        f'<option value="{key}">{info["name"]}</option>' for key, info in CREDENTIAL_TYPES.items()
    )

    content = f"""
        <h1 class="page-title">Credentials</h1>

        <div class="stats-grid" style="margin-bottom: 24px;">
            <div class="stat-box">
                <div class="stat-value" id="totalContexts">0</div>
                <div class="stat-label">Contexts with Credentials</div>
            </div>
            <div class="stat-box">
                <div class="stat-value" id="totalCredentials">0</div>
                <div class="stat-label">Total Credentials</div>
            </div>
        </div>

        <div class="card">
            <div class="card-header">
                <span class="card-title">All Credentials</span>
                <div>
                    <button class="btn btn-primary" onclick="openAddModal()">+ Add Credential</button>
                    <button class="btn" onclick="loadCredentials()">Refresh</button>
                </div>
            </div>
            <table id="credentialsTable">
                <thead>
                    <tr>
                        <th>Context</th>
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

        <!-- Add Credential Modal -->
        <div class="modal" id="addModal">
            <div class="modal-content">
                <div class="modal-header">
                    <h3>Add Credential</h3>
                    <button class="btn" onclick="closeAddModal()">&times;</button>
                </div>
                <form id="addForm" onsubmit="submitCredential(event)">
                    <div class="form-group">
                        <label for="contextId">Context</label>
                        <select id="contextId" required>
                            <option value="">Select context...</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label for="credType">Credential Type</label>
                        <select id="credType" required onchange="updateMetadataFields()">
                            <option value="">Select type...</option>
                            {type_options}
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

    """

    extra_css = """
        .badge-type { background: #ede9fe; color: #6d28d9; }
        .masked { font-family: monospace; color: var(--text-muted); }
        .meta-tag { display: inline-block; background: #f1f5f9; padding: 2px 8px; border-radius: 4px; font-size: 11px; margin-right: 4px; }
        .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 100; }
        .modal.active { display: flex; align-items: center; justify-content: center; }
        .modal-content { background: white; border-radius: 8px; padding: 24px; width: 90%; max-width: 500px; }
        .modal-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
        .modal-header h3 { margin: 0; }
        .form-group { margin-bottom: 16px; }
        .form-group label { display: block; font-size: 13px; font-weight: 500; margin-bottom: 6px; }
        .form-group input, .form-group select, .form-group textarea { width: 100%; padding: 8px 12px; border: 1px solid var(--border); border-radius: 4px; font-size: 14px; box-sizing: border-box; }
        .form-group textarea { min-height: 80px; font-family: monospace; }
        .form-group small { color: var(--text-muted); font-size: 12px; display: block; margin-top: 4px; }
    """

    # Build JavaScript with embedded CREDENTIAL_TYPES (proper JSON serialization)
    cred_types_js = json.dumps(CREDENTIAL_TYPES)
    extra_js = f"""
        const CREDENTIAL_TYPES = {cred_types_js};

        async function loadCredentials() {{
            const res = await fetchWithErrorHandling('/platformadmin/credentials/list');
            if (!res) {{
                document.getElementById('credentialsBody').innerHTML = '<tr><td colspan="5" style="color: var(--error); text-align: center;">Failed to load credentials</td></tr>';
                return;
            }}
            const data = await res.json();
            renderCredentials(data);
        }}

        async function loadContexts() {{
            const res = await fetchWithErrorHandling('/platformadmin/contexts');
            if (!res) {{
                console.error('Failed to load contexts');
                return;
            }}
            const data = await res.json();
            const contexts = data.contexts || [];
            const select = document.getElementById('contextId');
            select.innerHTML = '<option value="">Select context...</option>' +
                contexts.map(c => `<option value="${{c.id}}">${{escapeHtml(c.name)}} (${{c.type}})</option>`).join('');
        }}

        function renderCredentials(data) {{
            document.getElementById('totalContexts').textContent = data.contexts_with_credentials || 0;
            document.getElementById('totalCredentials').textContent = data.total_credentials || 0;

            const tbody = document.getElementById('credentialsBody');
            const creds = data.credentials || [];

            if (creds.length === 0) {{
                tbody.innerHTML = '<tr><td colspan="5" class="loading">No credentials found</td></tr>';
                return;
            }}

            tbody.innerHTML = creds.map(c => {{
                const meta = Object.entries(c.metadata || {{}})
                    .map(([k, v]) => `<span class="meta-tag">${{escapeHtml(k)}}: ${{escapeHtml(v)}}</span>`)
                    .join('') || '<span class="masked">-</span>';
                const created = new Date(c.created_at).toLocaleDateString();

                return `
                    <tr>
                        <td>
                            <div>${{escapeHtml(c.context_name)}}</div>
                        </td>
                        <td><span class="badge badge-type">${{escapeHtml(c.credential_type_name)}}</span></td>
                        <td>${{meta}}</td>
                        <td>${{created}}</td>
                        <td>
                            <button class="btn btn-danger btn-sm" onclick="deleteCredential(this, '${{c.id}}', '${{escapeHtml(c.credential_type)}}')">Delete</button>
                        </td>
                    </tr>
                `;
            }}).join('');
        }}

        function openAddModal() {{
            loadContexts();
            document.getElementById('addModal').classList.add('active');
        }}

        function closeAddModal() {{
            document.getElementById('addModal').classList.remove('active');
            document.getElementById('addForm').reset();
            document.getElementById('metadataFields').innerHTML = '';
        }}

        function updateMetadataFields() {{
            const type = document.getElementById('credType').value;
            const container = document.getElementById('metadataFields');
            container.innerHTML = '';

            if (type && CREDENTIAL_TYPES[type]) {{
                const fields = CREDENTIAL_TYPES[type].metadata_fields || [];
                fields.forEach(fieldDef => {{
                    const name = typeof fieldDef === 'string' ? fieldDef : fieldDef.name;
                    const label = typeof fieldDef === 'string'
                        ? fieldDef.replace('_', ' ').replace(/\\b\\w/g, l => l.toUpperCase())
                        : fieldDef.label;
                    const placeholder = typeof fieldDef === 'string'
                        ? `Enter ${{fieldDef}}`
                        : (fieldDef.placeholder || '');
                    const pattern = typeof fieldDef === 'object' && fieldDef.pattern ? fieldDef.pattern : '';
                    const patternError = typeof fieldDef === 'object' && fieldDef.pattern_error ? fieldDef.pattern_error : 'Invalid format';
                    const required = typeof fieldDef === 'object' && fieldDef.required;

                    container.innerHTML += `
                        <div class="form-group">
                            <label for="meta_${{name}}">${{label}}${{required ? ' *' : ''}}</label>
                            <input type="text" id="meta_${{name}}" name="meta_${{name}}"
                                   placeholder="${{placeholder}}"
                                   ${{pattern ? `data-pattern="${{pattern}}" data-pattern-error="${{patternError}}"` : ''}}
                                   ${{required ? 'required' : ''}}>
                            <div class="field-error" id="error_${{name}}" style="display:none; color: var(--error); font-size: 12px; margin-top: 4px;"></div>
                        </div>
                    `;
                }});
            }}
        }}

        async function submitCredential(e) {{
            e.preventDefault();
            const submitBtn = e.target.querySelector('button[type="submit"]');
            const originalText = submitBtn.textContent;

            const contextId = document.getElementById('contextId').value;
            const credType = document.getElementById('credType').value;
            const credValue = document.getElementById('credValue').value;

            // Collect and validate metadata
            const metadata = {{}};
            let hasError = false;

            if (CREDENTIAL_TYPES[credType]) {{
                const fields = CREDENTIAL_TYPES[credType].metadata_fields || [];
                for (const fieldDef of fields) {{
                    const name = typeof fieldDef === 'string' ? fieldDef : fieldDef.name;
                    const el = document.getElementById('meta_' + name);
                    const errorEl = document.getElementById('error_' + name);

                    if (el) {{
                        const value = el.value.trim();

                        // Check required
                        if (typeof fieldDef === 'object' && fieldDef.required && !value) {{
                            if (errorEl) {{
                                errorEl.textContent = 'This field is required';
                                errorEl.style.display = 'block';
                            }}
                            hasError = true;
                            continue;
                        }}

                        // Check pattern
                        if (value && typeof fieldDef === 'object' && fieldDef.pattern) {{
                            const regex = new RegExp(fieldDef.pattern);
                            if (!regex.test(value)) {{
                                if (errorEl) {{
                                    errorEl.textContent = fieldDef.pattern_error || 'Invalid format';
                                    errorEl.style.display = 'block';
                                }}
                                hasError = true;
                                continue;
                            }}
                        }}

                        // Clear error and save value
                        if (errorEl) errorEl.style.display = 'none';
                        if (value) metadata[name] = value;
                    }}
                }}
            }}

            if (hasError) {{
                showToast('Please fix validation errors', 'error');
                return;
            }}

            submitBtn.disabled = true;
            submitBtn.textContent = 'Saving...';

            const res = await fetchWithErrorHandling('/platformadmin/credentials/create', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    context_id: contextId,
                    credential_type: credType,
                    value: credValue,
                    metadata: Object.keys(metadata).length > 0 ? metadata : null
                }})
            }});

            submitBtn.disabled = false;
            submitBtn.textContent = originalText;

            if (res) {{
                showToast('Credential saved successfully', 'success');
                closeAddModal();
                loadCredentials();
            }}
        }}

        async function deleteCredential(btn, credId, credType) {{
            if (!confirm(`Delete this ${{credType}} credential? This cannot be undone.`)) return;

            const originalText = btn.textContent;
            btn.disabled = true;
            btn.textContent = 'Deleting...';

            const res = await fetchWithErrorHandling(`/platformadmin/credentials/${{credId}}`, {{ method: 'DELETE' }});

            btn.disabled = false;
            btn.textContent = originalText;

            if (res) {{
                showToast('Credential deleted', 'success');
                loadCredentials();
            }}
        }}

        function escapeHtml(str) {{
            if (!str) return '';
            const div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        }}

        // Initial load
        loadCredentials();
    """

    return render_admin_page(
        title="Credentials",
        active_page="/platformadmin/credentials/",
        content=content,
        user_name=admin.display_name or admin.email.split("@")[0],
        user_email=admin.email,
        breadcrumbs=[("Credentials", "#")],
        extra_css=extra_css,
        extra_js=extra_js,
    )


# --- API Endpoints ---


@router.get("/list")
async def list_all_credentials(
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """List all credentials (admin only).

    Returns credentials with context info.
    """
    # Get all credentials with context info
    stmt = (
        select(UserCredential, Context)
        .join(Context, UserCredential.context_id == Context.id)
        .order_by(Context.name, UserCredential.credential_type)
    )
    result = await session.execute(stmt)
    rows = result.all()

    credentials = []
    context_ids = set()
    for cred, ctx in rows:
        context_ids.add(str(ctx.id))
        credentials.append(
            CredentialInfo(
                id=str(cred.id),
                context_id=str(ctx.id),
                context_name=ctx.name,
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
        "contexts_with_credentials": len(context_ids),
    }


@router.get("/types")
async def list_credential_types(
    admin: AdminUser = Depends(verify_admin_user),
) -> dict:
    """List available credential types."""
    return {"types": CREDENTIAL_TYPES}


@router.post(
    "/create", response_model=CredentialCreateResponse, dependencies=[Depends(require_csrf)]
)
async def create_credential(
    request: CredentialCreateRequest,
    http_request: Request,
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
    cred_service: CredentialService = Depends(_get_credential_service),
) -> CredentialCreateResponse:
    """Create or update a credential for a context."""
    # Validate credential type
    if request.credential_type not in CREDENTIAL_TYPES:
        valid_types = ", ".join(CREDENTIAL_TYPES.keys())
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid credential type. Valid types: {valid_types}",
        )

    # Parse context_id
    try:
        context_uuid = UUID(request.context_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid context_id format",
        ) from e

    # Verify context exists
    ctx_stmt = select(Context).where(Context.id == context_uuid)
    ctx_result = await session.execute(ctx_stmt)
    ctx = ctx_result.scalar_one_or_none()

    if not ctx:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Context {request.context_id} not found",
        )

    # Store credential (encrypts automatically)
    credential = await cred_service.store_credential(
        context_id=context_uuid,
        credential_type=request.credential_type,
        value=request.value,
        metadata=request.metadata,
        session=session,
    )

    await session.commit()

    LOGGER.info(
        "Admin %s created %s credential for context %s",
        sanitize_log(admin.email),
        sanitize_log(request.credential_type),
        sanitize_log(ctx.name),
    )

    # Log security event
    log_security_event(
        event_type=CREDENTIAL_CREATED,
        user_email=admin.email,
        user_id=str(admin.user_id),
        ip_address=get_client_ip(http_request) if http_request else None,
        endpoint="/platformadmin/credentials/create",
        details={
            "credential_type": request.credential_type,
            "target_context_name": ctx.name,
            "target_context_id": str(context_uuid),
        },
        severity="INFO",
    )

    return CredentialCreateResponse(
        success=True,
        message=f"Credential {request.credential_type} saved for context {ctx.name}",
        credential_id=str(credential.id),
    )


@router.delete(
    "/{credential_id}",
    response_model=CredentialDeleteResponse,
    dependencies=[Depends(require_csrf)],
)
async def delete_credential(
    credential_id: UUID,
    http_request: Request,
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

    # Get context name for logging
    ctx_stmt = select(Context).where(Context.id == credential.context_id)
    ctx_result = await session.execute(ctx_stmt)
    ctx = ctx_result.scalar_one_or_none()
    context_name = ctx.name if ctx else "unknown"

    cred_type = credential.credential_type

    # Delete
    await session.delete(credential)
    await session.commit()

    LOGGER.info(f"Admin {admin.email} deleted {cred_type} credential for context {context_name}")

    # Log security event
    log_security_event(
        event_type=CREDENTIAL_DELETED,
        user_email=admin.email,
        user_id=str(admin.user_id),
        ip_address=get_client_ip(http_request) if http_request else None,
        endpoint=f"/platformadmin/credentials/{credential_id}",
        details={
            "credential_type": cred_type,
            "target_context_name": context_name,
            "target_context_id": str(credential.context_id),
        },
        severity="INFO",
    )

    return CredentialDeleteResponse(
        success=True,
        message=f"Deleted {cred_type} credential for context {context_name}",
    )


__all__ = ["router", "CREDENTIAL_TYPES"]
