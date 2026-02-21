"""Admin endpoints for Azure DevOps team configuration management."""

# ruff: noqa: E501
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from shared.sanitize import sanitize_log
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.db.models import AdoTeamConfig
from interfaces.http.admin_auth import AdminUser, verify_admin_user
from interfaces.http.admin_shared import UTF8HTMLResponse, render_admin_page
from interfaces.http.csrf import require_csrf

LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/platformadmin/ado-config",
    tags=["platform-admin", "ado-config"],
)

VALID_DEFAULT_TYPES = {"Feature", "User Story", "Bug"}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AdoTeamConfigOut(BaseModel):
    """ADO team config for API responses."""

    id: uuid.UUID
    alias: str | None
    display_name: str | None
    area_path: str
    owner: str | None
    default_type: str
    default_tags: list[str]
    is_default: bool
    sort_order: int


class AdoTeamIn(BaseModel):
    """Request body for creating/updating a team."""

    alias: str
    display_name: str | None = None
    area_path: str
    owner: str | None = None
    default_type: str = "User Story"
    default_tags: list[str] = []
    sort_order: int = 0


class AdoDefaultsIn(BaseModel):
    """Request body for updating global defaults."""

    area_path: str
    default_type: str = "Feature"


# ---------------------------------------------------------------------------
# HTML dashboard
# ---------------------------------------------------------------------------


@router.get("/", dependencies=[Depends(verify_admin_user)])
async def ado_config_dashboard(
    admin: AdminUser = Depends(verify_admin_user),
    session: AsyncSession = Depends(get_db),
) -> UTF8HTMLResponse:
    """ADO team configuration management dashboard."""
    content = """
<div class="page-title">ADO Configuration</div>

<div class="card" id="defaultsCard">
    <div class="card-header">
        <h3 class="card-title">Global Defaults</h3>
        <button class="btn btn-sm" onclick="openDefaultsModal()">Edit Defaults</button>
    </div>
    <div id="defaultsContent">
        <p style="color: var(--text-muted); font-size: 13px;">Loading...</p>
    </div>
</div>

<div class="card">
    <div class="card-header">
        <h3 class="card-title">Team Mappings</h3>
        <button class="btn btn-primary btn-sm" onclick="openAddTeamModal()">+ Add Team</button>
    </div>
    <div id="teamsTableContainer">
        <p style="color: var(--text-muted); font-size: 13px; padding: 12px;">Loading...</p>
    </div>
</div>

<!-- Defaults modal -->
<div id="defaultsModal" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,0.5); z-index:1000; align-items:center; justify-content:center;">
    <div style="background:var(--bg-card); border-radius:8px; padding:24px; width:480px; max-width:95vw;">
        <h3 style="margin-bottom:16px; font-size:15px;">Edit Global Defaults</h3>
        <form id="defaultsForm" onsubmit="saveDefaults(event)">
            <div style="margin-bottom:12px;">
                <label style="display:block; font-size:12px; font-weight:600; margin-bottom:6px; color:var(--text-muted);">Area Path *</label>
                <input id="defaultAreaPath" type="text" required style="width:100%; padding:8px 12px; border:1px solid var(--border); border-radius:6px; font-size:13px;">
            </div>
            <div style="margin-bottom:20px;">
                <label style="display:block; font-size:12px; font-weight:600; margin-bottom:6px; color:var(--text-muted);">Default Type *</label>
                <select id="defaultType" style="width:100%; padding:8px 12px; border:1px solid var(--border); border-radius:6px; font-size:13px; background:var(--bg-card);">
                    <option value="Feature">Feature</option>
                    <option value="User Story">User Story</option>
                    <option value="Bug">Bug</option>
                </select>
            </div>
            <div style="display:flex; gap:8px; justify-content:flex-end;">
                <button type="button" class="btn btn-sm" onclick="closeDefaultsModal()">Cancel</button>
                <button type="submit" class="btn btn-primary btn-sm">Save</button>
            </div>
        </form>
    </div>
</div>

<!-- Add/Edit team modal -->
<div id="teamModal" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,0.5); z-index:1000; align-items:center; justify-content:center;">
    <div style="background:var(--bg-card); border-radius:8px; padding:24px; width:520px; max-width:95vw;">
        <h3 id="teamModalTitle" style="margin-bottom:16px; font-size:15px;">Add Team</h3>
        <form id="teamForm" onsubmit="saveTeam(event)">
            <input type="hidden" id="teamOriginalAlias">
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:12px;">
                <div>
                    <label style="display:block; font-size:12px; font-weight:600; margin-bottom:6px; color:var(--text-muted);">Alias * (e.g. "infra")</label>
                    <input id="teamAlias" type="text" required style="width:100%; padding:8px 12px; border:1px solid var(--border); border-radius:6px; font-size:13px;" placeholder="infra">
                </div>
                <div>
                    <label style="display:block; font-size:12px; font-weight:600; margin-bottom:6px; color:var(--text-muted);">Display Name</label>
                    <input id="teamDisplayName" type="text" style="width:100%; padding:8px 12px; border:1px solid var(--border); border-radius:6px; font-size:13px;" placeholder="Infrastructure">
                </div>
            </div>
            <div style="margin-bottom:12px;">
                <label style="display:block; font-size:12px; font-weight:600; margin-bottom:6px; color:var(--text-muted);">Area Path *</label>
                <input id="teamAreaPath" type="text" required style="width:100%; padding:8px 12px; border:1px solid var(--border); border-radius:6px; font-size:13px;" placeholder="Web Teams\\Common\\Infra">
            </div>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:12px;">
                <div>
                    <label style="display:block; font-size:12px; font-weight:600; margin-bottom:6px; color:var(--text-muted);">Owner</label>
                    <input id="teamOwner" type="text" style="width:100%; padding:8px 12px; border:1px solid var(--border); border-radius:6px; font-size:13px;" placeholder="Jane Smith">
                </div>
                <div>
                    <label style="display:block; font-size:12px; font-weight:600; margin-bottom:6px; color:var(--text-muted);">Default Type *</label>
                    <select id="teamDefaultType" style="width:100%; padding:8px 12px; border:1px solid var(--border); border-radius:6px; font-size:13px; background:var(--bg-card);">
                        <option value="User Story">User Story</option>
                        <option value="Feature">Feature</option>
                        <option value="Bug">Bug</option>
                    </select>
                </div>
            </div>
            <div style="margin-bottom:12px;">
                <label style="display:block; font-size:12px; font-weight:600; margin-bottom:6px; color:var(--text-muted);">Default Tags (comma-separated)</label>
                <input id="teamDefaultTags" type="text" style="width:100%; padding:8px 12px; border:1px solid var(--border); border-radius:6px; font-size:13px;" placeholder="Security, SecurityIncidentHigh">
            </div>
            <div style="margin-bottom:20px;">
                <label style="display:block; font-size:12px; font-weight:600; margin-bottom:6px; color:var(--text-muted);">Sort Order</label>
                <input id="teamSortOrder" type="number" value="0" style="width:100px; padding:8px 12px; border:1px solid var(--border); border-radius:6px; font-size:13px;">
            </div>
            <div style="display:flex; gap:8px; justify-content:flex-end;">
                <button type="button" class="btn btn-sm" onclick="closeTeamModal()">Cancel</button>
                <button type="submit" class="btn btn-primary btn-sm" id="teamSaveBtn">Save</button>
            </div>
        </form>
    </div>
</div>
"""

    extra_js = """
    // Load all data on page load
    loadDefaults();
    loadTeams();

    async function loadDefaults() {
        const resp = await fetchWithErrorHandling('/platformadmin/ado-config/defaults');
        if (!resp) return;
        const data = await resp.json();
        const el = document.getElementById('defaultsContent');
        if (!data.area_path) {
            el.innerHTML = '<p style="color: var(--text-muted); font-size: 13px;">No global defaults configured yet.</p>';
        } else {
            el.innerHTML = `
                <table style="max-width:500px;">
                    <tr><td style="font-size:12px; font-weight:600; color:var(--text-muted); padding:6px 12px 6px 0; white-space:nowrap;">Area Path</td><td style="font-size:13px;">${escapeHtml(data.area_path)}</td></tr>
                    <tr><td style="font-size:12px; font-weight:600; color:var(--text-muted); padding:6px 12px 6px 0; white-space:nowrap;">Default Type</td><td style="font-size:13px;">${escapeHtml(data.default_type)}</td></tr>
                </table>`;
        }
    }

    async function loadTeams() {
        const resp = await fetchWithErrorHandling('/platformadmin/ado-config/teams');
        if (!resp) return;
        const teams = await resp.json();
        const container = document.getElementById('teamsTableContainer');
        if (!teams.length) {
            container.innerHTML = '<div class="empty-state"><div class="empty-state-icon">&#128203;</div><p>No teams configured. Click "+ Add Team" to get started.</p></div>';
            return;
        }
        let rows = teams.map(t => `
            <tr>
                <td style="font-size:13px; font-weight:500;">${escapeHtml(t.alias || '')}</td>
                <td style="font-size:13px;">${escapeHtml(t.display_name || '')}</td>
                <td style="font-size:13px; font-family:monospace; font-size:11px;">${escapeHtml(t.area_path)}</td>
                <td style="font-size:13px;">${escapeHtml(t.owner || '')}</td>
                <td><span class="badge badge-info">${escapeHtml(t.default_type)}</span></td>
                <td style="font-size:11px; color:var(--text-muted);">${t.default_tags.map(g => escapeHtml(g)).join(', ') || '-'}</td>
                <td style="font-size:13px;">${t.sort_order}</td>
                <td>
                    <button class="btn btn-sm" onclick="openEditTeamModal(${JSON.stringify(t).replace(/"/g, '&quot;')})">Edit</button>
                    <button class="btn btn-sm" style="color:var(--error); border-color:var(--error); margin-left:4px;" onclick="deleteTeam('${escapeHtml(t.alias || '')}')">Delete</button>
                </td>
            </tr>`).join('');
        container.innerHTML = `
            <table>
                <thead>
                    <tr>
                        <th>Alias</th><th>Display Name</th><th>Area Path</th><th>Owner</th><th>Default Type</th><th>Tags</th><th>Order</th><th>Actions</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>`;
    }

    // --- Defaults modal ---
    async function openDefaultsModal() {
        const resp = await fetchWithErrorHandling('/platformadmin/ado-config/defaults');
        if (!resp) return;
        const data = await resp.json();
        document.getElementById('defaultAreaPath').value = data.area_path || '';
        document.getElementById('defaultType').value = data.default_type || 'Feature';
        document.getElementById('defaultsModal').style.display = 'flex';
    }
    function closeDefaultsModal() {
        document.getElementById('defaultsModal').style.display = 'none';
    }
    async function saveDefaults(event) {
        event.preventDefault();
        const body = {
            area_path: document.getElementById('defaultAreaPath').value.trim(),
            default_type: document.getElementById('defaultType').value,
        };
        const resp = await fetchWithErrorHandling('/platformadmin/ado-config/defaults', {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        if (resp) {
            closeDefaultsModal();
            loadDefaults();
            showToast('Defaults saved', 'success');
        }
    }

    // --- Team modal ---
    function openAddTeamModal() {
        document.getElementById('teamModalTitle').textContent = 'Add Team';
        document.getElementById('teamOriginalAlias').value = '';
        document.getElementById('teamForm').reset();
        document.getElementById('teamAlias').readOnly = false;
        document.getElementById('teamModal').style.display = 'flex';
    }
    function openEditTeamModal(team) {
        document.getElementById('teamModalTitle').textContent = 'Edit Team: ' + team.alias;
        document.getElementById('teamOriginalAlias').value = team.alias;
        document.getElementById('teamAlias').value = team.alias;
        document.getElementById('teamAlias').readOnly = true;
        document.getElementById('teamDisplayName').value = team.display_name || '';
        document.getElementById('teamAreaPath').value = team.area_path;
        document.getElementById('teamOwner').value = team.owner || '';
        document.getElementById('teamDefaultType').value = team.default_type;
        document.getElementById('teamDefaultTags').value = (team.default_tags || []).join(', ');
        document.getElementById('teamSortOrder').value = team.sort_order;
        document.getElementById('teamModal').style.display = 'flex';
    }
    function closeTeamModal() {
        document.getElementById('teamModal').style.display = 'none';
    }
    async function saveTeam(event) {
        event.preventDefault();
        const originalAlias = document.getElementById('teamOriginalAlias').value;
        const alias = document.getElementById('teamAlias').value.trim();
        const tagsRaw = document.getElementById('teamDefaultTags').value;
        const tags = tagsRaw ? tagsRaw.split(',').map(t => t.trim()).filter(Boolean) : [];
        const body = {
            alias: alias,
            display_name: document.getElementById('teamDisplayName').value.trim() || null,
            area_path: document.getElementById('teamAreaPath').value.trim(),
            owner: document.getElementById('teamOwner').value.trim() || null,
            default_type: document.getElementById('teamDefaultType').value,
            default_tags: tags,
            sort_order: parseInt(document.getElementById('teamSortOrder').value) || 0,
        };
        let resp;
        if (originalAlias) {
            resp = await fetchWithErrorHandling(`/platformadmin/ado-config/teams/${encodeURIComponent(originalAlias)}`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body),
            });
        } else {
            resp = await fetchWithErrorHandling('/platformadmin/ado-config/teams', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body),
            });
        }
        if (resp) {
            closeTeamModal();
            loadTeams();
            showToast(originalAlias ? 'Team updated' : 'Team added', 'success');
        }
    }
    async function deleteTeam(alias) {
        if (!confirm(`Delete team "${alias}"? This cannot be undone.`)) return;
        const resp = await fetchWithErrorHandling(`/platformadmin/ado-config/teams/${encodeURIComponent(alias)}`, {
            method: 'DELETE',
        });
        if (resp) {
            loadTeams();
            showToast('Team deleted', 'success');
        }
    }

    // Close modals on backdrop click
    document.getElementById('defaultsModal').addEventListener('click', function(e) {
        if (e.target === this) closeDefaultsModal();
    });
    document.getElementById('teamModal').addEventListener('click', function(e) {
        if (e.target === this) closeTeamModal();
    });
    """

    return UTF8HTMLResponse(
        render_admin_page(
            title="ADO Configuration",
            active_page="/platformadmin/ado-config/",
            content=content,
            user_name=admin.display_name or admin.email,
            user_email=admin.email,
            breadcrumbs=[("ADO Configuration", "#")],
            extra_js=extra_js,
        )
    )


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@router.get("/teams", dependencies=[Depends(verify_admin_user)])
async def list_teams(
    session: AsyncSession = Depends(get_db),
) -> list[AdoTeamConfigOut]:
    """List all configured teams (excludes global defaults row)."""
    stmt = (
        select(AdoTeamConfig)
        .where(AdoTeamConfig.is_default.is_(False))
        .order_by(AdoTeamConfig.sort_order, AdoTeamConfig.alias)
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [
        AdoTeamConfigOut(
            id=row.id,
            alias=row.alias,
            display_name=row.display_name,
            area_path=row.area_path,
            owner=row.owner,
            default_type=row.default_type,
            default_tags=row.default_tags or [],
            is_default=row.is_default,
            sort_order=row.sort_order,
        )
        for row in rows
    ]


@router.post("/teams", dependencies=[Depends(verify_admin_user), Depends(require_csrf)])
async def create_team(
    body: AdoTeamIn,
    session: AsyncSession = Depends(get_db),
) -> AdoTeamConfigOut:
    """Create a new team mapping."""
    alias = body.alias.strip().lower()
    if not alias:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Alias is required"
        )
    if body.default_type not in VALID_DEFAULT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"default_type must be one of: {', '.join(sorted(VALID_DEFAULT_TYPES))}",
        )

    # Check uniqueness
    existing = await session.execute(select(AdoTeamConfig).where(AdoTeamConfig.alias == alias))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Team alias '{alias}' already exists",
        )

    row = AdoTeamConfig(
        alias=alias,
        display_name=body.display_name,
        area_path=body.area_path,
        owner=body.owner,
        default_type=body.default_type,
        default_tags=body.default_tags,
        is_default=False,
        sort_order=body.sort_order,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    LOGGER.info("Created ADO team config: %s", sanitize_log(alias))
    return AdoTeamConfigOut(
        id=row.id,
        alias=row.alias,
        display_name=row.display_name,
        area_path=row.area_path,
        owner=row.owner,
        default_type=row.default_type,
        default_tags=row.default_tags or [],
        is_default=row.is_default,
        sort_order=row.sort_order,
    )


@router.put(
    "/teams/{alias}",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def update_team(
    alias: str,
    body: AdoTeamIn,
    session: AsyncSession = Depends(get_db),
) -> AdoTeamConfigOut:
    """Update an existing team mapping."""
    if body.default_type not in VALID_DEFAULT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"default_type must be one of: {', '.join(sorted(VALID_DEFAULT_TYPES))}",
        )

    result = await session.execute(
        select(AdoTeamConfig).where(
            AdoTeamConfig.alias == alias, AdoTeamConfig.is_default.is_(False)
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Team '{alias}' not found"
        )

    row.display_name = body.display_name
    row.area_path = body.area_path
    row.owner = body.owner
    row.default_type = body.default_type
    row.default_tags = body.default_tags
    row.sort_order = body.sort_order
    await session.commit()
    await session.refresh(row)
    LOGGER.info("Updated ADO team config: %s", sanitize_log(alias))
    return AdoTeamConfigOut(
        id=row.id,
        alias=row.alias,
        display_name=row.display_name,
        area_path=row.area_path,
        owner=row.owner,
        default_type=row.default_type,
        default_tags=row.default_tags or [],
        is_default=row.is_default,
        sort_order=row.sort_order,
    )


@router.delete(
    "/teams/{alias}",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_team(
    alias: str,
    session: AsyncSession = Depends(get_db),
) -> None:
    """Delete a team mapping."""
    result = await session.execute(
        select(AdoTeamConfig).where(
            AdoTeamConfig.alias == alias, AdoTeamConfig.is_default.is_(False)
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Team '{alias}' not found"
        )

    await session.delete(row)
    await session.commit()
    LOGGER.info("Deleted ADO team config: %s", sanitize_log(alias))


@router.get("/defaults", dependencies=[Depends(verify_admin_user)])
async def get_defaults(
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get global defaults row."""
    result = await session.execute(select(AdoTeamConfig).where(AdoTeamConfig.is_default.is_(True)))
    row = result.scalar_one_or_none()
    if not row:
        return {"area_path": "", "default_type": "Feature"}
    return {"area_path": row.area_path, "default_type": row.default_type}


@router.put(
    "/defaults",
    dependencies=[Depends(verify_admin_user), Depends(require_csrf)],
)
async def update_defaults(
    body: AdoDefaultsIn,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create or update the global defaults row."""
    if body.default_type not in VALID_DEFAULT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"default_type must be one of: {', '.join(sorted(VALID_DEFAULT_TYPES))}",
        )

    result = await session.execute(select(AdoTeamConfig).where(AdoTeamConfig.is_default.is_(True)))
    row = result.scalar_one_or_none()
    if row:
        row.area_path = body.area_path
        row.default_type = body.default_type
    else:
        row = AdoTeamConfig(
            alias=None,
            area_path=body.area_path,
            default_type=body.default_type,
            default_tags=[],
            is_default=True,
            sort_order=0,
        )
        session.add(row)

    await session.commit()
    LOGGER.info(
        "Updated ADO global defaults: area_path=%s, default_type=%s",
        sanitize_log(body.area_path),
        sanitize_log(body.default_type),
    )
    return {"area_path": body.area_path, "default_type": body.default_type}
