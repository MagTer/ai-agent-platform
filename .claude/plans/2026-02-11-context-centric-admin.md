# Context-Centric Admin Portal Restructure

**Date:** 2026-02-11
**Author:** Architect (Opus)
**Status:** Ready for Implementation

---

## 1. Feature Overview

Restructure the Admin Portal so that **Context** becomes the primary management unit. Currently, 5+ separate pages (Permissions, Workspaces, MCP Servers, OAuth Tokens, Conversations) manage entities that all belong to a Context. The new design consolidates these into a tabbed Context detail view, reducing navigation complexity and making multi-tenant management intuitive.

Additionally, this plan introduces a **context selection mechanism** for chat users in OpenWebUI, allowing users with multiple contexts to switch between them.

### Target URL Structure

```
/platformadmin/contexts/                    <- List all contexts (enhanced)
/platformadmin/contexts/{id}/               <- Context detail with tabs:
    Overview | Permissions | Workspaces | MCP Servers | OAuth Tokens | Conversations

/platformadmin/users/                       <- User list (enhanced)
/platformadmin/users/{id}/                  <- User detail with tabs:
    Contexts | Credentials
```

### What Changes

| Component | Before | After |
|-----------|--------|-------|
| Contexts list | Basic list with counts | Enhanced with owner, type badges, quick actions |
| Permissions | Separate `/permissions/` page | Tab inside context detail |
| Workspaces | Separate `/workspaces/` page | Tab inside context detail |
| MCP Servers | Separate `/mcp/` page | Tab inside context detail |
| OAuth Tokens | Separate `/oauth/` page | Tab inside context detail |
| Conversations | Only in context JSON detail | Tab inside context detail |
| Users | Basic list | Enhanced with detail page + context/credential tabs |
| Navigation | 11 items | 7 items (consolidated) |
| Context switching | None (auto-resolve personal) | New endpoint + admin UI |

### What Does NOT Change

- `/platformadmin/credentials/` stays as a separate page (per-user, not per-context)
- All API endpoints keep their existing paths (backward compatible)
- Database schema does not change (no migrations needed)
- Auth model unchanged (Entra ID + JWT)

---

## 2. Architecture Decisions

### 2.1 Router Strategy

We will **keep existing API routers** but add new HTML-serving routes for the context detail view. The existing API endpoints (e.g., `/platformadmin/permissions/contexts/{id}`, `/platformadmin/workspaces/list?context_id=X`) will be reused by the new tabbed UI via JavaScript fetch calls.

**Rationale:** This avoids breaking any existing API consumers while providing the new UI. The JavaScript in the context detail tabs will call the existing API endpoints.

### 2.2 Template Strategy

The context detail page will be a **single HTML template** (`admin_context_detail.html`) loaded from `interfaces/http/templates/`. It will contain all tab content rendered client-side by fetching data from existing API endpoints.

**Why a template file:** The combined HTML+CSS+JS for 6 tabs will exceed 500 lines / 40KB, triggering the template extraction rule from CLAUDE.md.

### 2.3 Context Selection for Chat

For users with multiple contexts (personal + shared), we need a mechanism to switch the active context when chatting via OpenWebUI. The approach:

1. Add an `active_context_id` column to the `UserContext` table (nullable, one per user)
2. Create a new API endpoint: `PUT /platformadmin/users/me/active-context`
3. Modify `ContextService.resolve_for_authenticated_user()` to check `active_context_id` first
4. Add a context selector in the admin portal user section

**Why this approach over alternatives:**
- OpenWebUI does not support custom headers per-request (ruled out header-based switching)
- A database flag is persistent across sessions and devices
- The admin portal already has authenticated user context, making it natural to host the selector

### 2.4 Layer Compliance

All changes are in the `interfaces/` layer (HTTP admin pages) and `core/` layer (context service, models). No cross-layer violations:
- `interfaces/http/admin_contexts.py` imports from `core/db/models`, `core/db/engine` -- allowed
- `core/context/service.py` imports from `core/db/models`, `core/auth/` -- allowed
- No module-to-module imports

---

## 3. Implementation Roadmap

### Phase 1: Database Schema Update (active context switching)

**Engineer tasks:**

1. **Add `active_context_id` column to `users` table**

   File: `services/agent/src/core/db/models.py`

   In the `User` class, add after `last_login_at`:
   ```python
   active_context_id: Mapped[uuid.UUID | None] = mapped_column(
       ForeignKey("contexts.id", ondelete="SET NULL"), nullable=True
   )
   ```

   This nullable FK means: if set, the user is currently using this context for chat. If NULL, falls back to their default (personal) context.

2. **Create Alembic migration**

   File: `services/agent/alembic/versions/20260211_add_active_context_to_users.py`

   ```python
   """Add active_context_id to users table.

   Revision ID: 20260211_active_ctx
   Revises: 20260210_composite_indices
   Create Date: 2026-02-11
   """

   from collections.abc import Sequence

   import sqlalchemy as sa
   from alembic import op
   from sqlalchemy.dialects.postgresql import UUID

   revision: str = "20260211_active_ctx"
   down_revision: str | Sequence[str] | None = "20260210_composite_indices"
   branch_labels: str | Sequence[str] | None = None
   depends_on: str | Sequence[str] | None = None


   def upgrade() -> None:
       """Add active_context_id to users table."""
       op.add_column(
           "users",
           sa.Column(
               "active_context_id",
               UUID(as_uuid=True),
               sa.ForeignKey("contexts.id", ondelete="SET NULL"),
               nullable=True,
           ),
       )
       op.create_index(
           "ix_users_active_context_id", "users", ["active_context_id"]
       )


   def downgrade() -> None:
       """Remove active_context_id from users table."""
       op.drop_index("ix_users_active_context_id", table_name="users")
       op.drop_column("users", "active_context_id")
   ```

3. **Update ContextService to respect active context**

   File: `services/agent/src/core/context/service.py`

   Replace the `resolve_for_authenticated_user` method. The current implementation always resolves to the user's default (personal) context. The new version checks `active_context_id` first:

   ```python
   @staticmethod
   async def resolve_for_authenticated_user(identity: UserIdentity, session: AsyncSession) -> UUID:
       """Resolve context for an authenticated user.

       Priority:
       1. User's active_context_id (if set and user has access)
       2. User's default (personal) context
       3. Fallback: create personal context
       """
       user = await get_or_create_user(identity, session)

       # Check active context first
       if user.active_context_id:
           # Verify user still has access to this context
           access_stmt = select(UserContext).where(
               UserContext.user_id == user.id,
               UserContext.context_id == user.active_context_id,
           )
           access_result = await session.execute(access_stmt)
           if access_result.scalar_one_or_none():
               LOGGER.debug(
                   "Using active context %s for user %s",
                   user.active_context_id,
                   user.email,
               )
               return user.active_context_id

           # Active context no longer accessible -- clear it
           LOGGER.warning(
               "User %s active_context_id %s no longer accessible, clearing",
               user.email,
               user.active_context_id,
           )
           user.active_context_id = None
           await session.flush()

       # Fall back to default context
       context = await get_user_default_context(user, session)
       if context:
           LOGGER.debug("Using personal context %s for user %s", context.id, user.email)
           return context.id

       # Fallback: create context if somehow missing
       LOGGER.warning("User %s has no default context, creating one", user.email)
       context = Context(
           name=f"personal_{user.id}",
           type="personal",
           config={"owner_email": user.email},
           default_cwd="/tmp",  # noqa: S108
       )
       session.add(context)
       await session.flush()
       return context.id
   ```

   **IMPORTANT:** This requires adding `from core.db.models import UserContext` to the imports at the top of the file. The existing imports are:
   ```python
   from core.db.models import Context, Conversation
   ```
   Change to:
   ```python
   from core.db.models import Context, Conversation, UserContext
   ```

   Also add `from sqlalchemy import select` if not already present (it is already imported).

4. **Add active context switching API endpoint**

   File: `services/agent/src/interfaces/http/admin_users.py`

   Add the following endpoint and Pydantic model. Add these imports to the top:
   ```python
   from core.db.models import Context, User, UserContext
   ```
   (Replace existing `from core.db.models import User, UserContext`)

   Add the Pydantic model after `UserUpdateRequest`:
   ```python
   class SetActiveContextRequest(BaseModel):
       """Request to set the active context for the current user."""
       context_id: str | None  # UUID string or null to clear
   ```

   Add the endpoint after the existing endpoints but before `__all__`:
   ```python
   @router.put("/me/active-context", dependencies=[Depends(require_csrf)])
   async def set_active_context(
       request: SetActiveContextRequest,
       admin: AdminUser = Depends(verify_admin_user),
       session: AsyncSession = Depends(get_db),
   ) -> dict[str, str | bool]:
       """Set the active context for the current user.

       This determines which context is used when chatting via OpenWebUI.
       Set context_id to null to revert to default (personal) context.
       """
       user_stmt = select(User).where(User.id == admin.user_id)
       user_result = await session.execute(user_stmt)
       user = user_result.scalar_one_or_none()

       if not user:
           raise HTTPException(status_code=404, detail="User not found")

       if request.context_id is None:
           # Clear active context (revert to default)
           user.active_context_id = None
           await session.commit()
           return {"success": True, "message": "Active context cleared. Using default personal context."}

       # Validate context exists and user has access
       try:
           ctx_uuid = UUID(request.context_id)
       except ValueError as e:
           raise HTTPException(status_code=400, detail="Invalid context_id format") from e

       ctx_stmt = select(Context).where(Context.id == ctx_uuid)
       ctx_result = await session.execute(ctx_stmt)
       ctx = ctx_result.scalar_one_or_none()

       if not ctx:
           raise HTTPException(status_code=404, detail="Context not found")

       # Verify user has access to this context
       access_stmt = select(UserContext).where(
           UserContext.user_id == admin.user_id,
           UserContext.context_id == ctx_uuid,
       )
       access_result = await session.execute(access_stmt)
       if not access_result.scalar_one_or_none():
           raise HTTPException(
               status_code=403,
               detail="You do not have access to this context",
           )

       user.active_context_id = ctx_uuid
       await session.commit()

       LOGGER.info(
           "User %s set active context to %s (%s)",
           sanitize_log(admin.email),
           sanitize_log(ctx_uuid),
           sanitize_log(ctx.name),
       )

       return {
           "success": True,
           "message": f"Active context set to '{ctx.name}'",
           "context_id": str(ctx_uuid),
           "context_name": ctx.name,
       }
   ```

   Also add a GET endpoint to retrieve the current user's context list with active indicator:
   ```python
   @router.get("/me/contexts")
   async def get_my_contexts(
       admin: AdminUser = Depends(verify_admin_user),
       session: AsyncSession = Depends(get_db),
   ) -> dict[str, object]:
       """Get the current user's accessible contexts with active context indicator."""
       user_stmt = select(User).where(User.id == admin.user_id)
       user_result = await session.execute(user_stmt)
       user = user_result.scalar_one_or_none()

       if not user:
           raise HTTPException(status_code=404, detail="User not found")

       # Get user's contexts via UserContext junction
       ctx_stmt = (
           select(Context, UserContext.role, UserContext.is_default)
           .join(UserContext, UserContext.context_id == Context.id)
           .where(UserContext.user_id == admin.user_id)
           .order_by(UserContext.is_default.desc(), Context.name)
       )
       ctx_result = await session.execute(ctx_stmt)
       rows = ctx_result.all()

       contexts = []
       for ctx, role, is_default in rows:
           contexts.append({
               "id": str(ctx.id),
               "name": ctx.name,
               "type": ctx.type,
               "role": role,
               "is_default": is_default,
               "is_active": (
                   str(ctx.id) == str(user.active_context_id)
                   if user.active_context_id
                   else is_default
               ),
           })

       return {
           "contexts": contexts,
           "active_context_id": str(user.active_context_id) if user.active_context_id else None,
       }
   ```

**Ops tasks (after Engineer completes):**
- Run `cd /home/magnus/dev/ai-agent-platform && ./stack check`
- Run the alembic migration: `cd services/agent && poetry run alembic upgrade head`
- Verify migration applied: check that `active_context_id` column exists in `users` table

**Files affected:**
- `services/agent/src/core/db/models.py` (modify -- add `active_context_id` to User)
- `services/agent/alembic/versions/20260211_add_active_context_to_users.py` (create)
- `services/agent/src/core/context/service.py` (modify -- update `resolve_for_authenticated_user`)
- `services/agent/src/interfaces/http/admin_users.py` (modify -- add 2 endpoints)

---

### Phase 2: Navigation Simplification

**Engineer tasks:**

1. **Update navigation items**

   File: `services/agent/src/interfaces/http/admin_shared.py`

   Replace the `ADMIN_NAV_ITEMS` list (lines 29-43) with:
   ```python
   ADMIN_NAV_ITEMS: list[NavItem] = [
       NavItem("Dashboard", "/platformadmin/", "&#127968;", "home"),
       NavItem("Diagnostics", "/platformadmin/diagnostics/", "&#128200;", "monitoring"),
       NavItem("Debug Logs", "/platformadmin/debug/", "&#128270;", "monitoring"),
       NavItem("Contexts", "/platformadmin/contexts/", "&#128451;", "features"),
       NavItem("Users", "/platformadmin/users/", "&#128100;", "users"),
       NavItem("Credentials", "/platformadmin/credentials/", "&#128273;", "users"),
       NavItem("Price Tracker", "/platformadmin/price-tracker/", "&#128181;", "features"),
       NavItem("Chat", "/", "&#128172;", "external"),
       NavItem("Open WebUI Admin", "/admin/", "&#128279;", "external"),
   ]
   ```

   **Removed items:** MCP Integrations, Permissions, Workspaces, OAuth Settings -- all now accessible via Context detail tabs.

2. **Update navigation active page matching for context detail**

   In the `get_admin_sidebar_html` function, update the `is_active` logic to also match context detail pages. Replace lines 474-478:

   Current:
   ```python
   is_active = (
       item.href.rstrip("/").endswith(active_page.rstrip("/"))
       if active_page != "/"
       else item.href == "/platformadmin/"
   )
   ```

   New:
   ```python
   is_active = False
   if active_page == "/":
       is_active = item.href == "/platformadmin/"
   elif active_page.startswith("/platformadmin/contexts/") and item.href == "/platformadmin/contexts/":
       # Context detail pages highlight the Contexts nav item
       is_active = True
   elif active_page.startswith("/platformadmin/users/") and item.href == "/platformadmin/users/":
       # User detail pages highlight the Users nav item
       is_active = True
   else:
       is_active = item.href.rstrip("/").endswith(active_page.rstrip("/"))
   ```

3. **Update admin portal dashboard cards**

   File: `services/agent/src/interfaces/http/admin_portal.py`

   Remove the standalone cards for OAuth Settings, and update Contexts card description. In the `content` variable, remove the OAuth card:
   ```html
   <a href="/platformadmin/oauth/" class="portal-card">
       ...
   </a>
   ```

   Update Contexts card description to:
   ```html
   <a href="/platformadmin/contexts/" class="portal-card">
       <div class="card-icon orange">&#128451;</div>
       <h2>Contexts</h2>
       <p>Manage contexts with permissions, workspaces, MCP servers, and OAuth tokens.</p>
   </a>
   ```

**Ops tasks:**
- Run `cd /home/magnus/dev/ai-agent-platform && ./stack check`

**Files affected:**
- `services/agent/src/interfaces/http/admin_shared.py` (modify)
- `services/agent/src/interfaces/http/admin_portal.py` (modify)

---

### Phase 3: Enhanced Context List Page

**Engineer tasks:**

1. **Rewrite the contexts list page**

   File: `services/agent/src/interfaces/http/admin_contexts.py`

   The existing list page is very basic. Replace the `contexts_dashboard` endpoint's content, extra_css, and extra_js with an enhanced version.

   The enhanced page should:
   - Show stat boxes (total contexts, personal, shared, virtual)
   - List contexts in a card grid with: name, type badge, owner name, entity counts (conversations, permissions, workspaces, MCP servers, OAuth tokens)
   - Each context card links to `/platformadmin/contexts/{id}/`
   - Include a "Create Context" button with modal form
   - Include "Active Context" indicator for the current user
   - Include context switcher dropdown in the header

   Replace the `content` HTML:
   ```python
   content = """
       <h1 class="page-title">Contexts</h1>

       <div class="stats-grid">
           <div class="stat-box">
               <div class="stat-value" id="totalContexts">0</div>
               <div class="stat-label">Total Contexts</div>
           </div>
           <div class="stat-box">
               <div class="stat-value" id="personalContexts">0</div>
               <div class="stat-label">Personal</div>
           </div>
           <div class="stat-box">
               <div class="stat-value" id="virtualContexts">0</div>
               <div class="stat-label">Virtual</div>
           </div>
       </div>

       <!-- Active Context Switcher -->
       <div class="card" style="margin-bottom: 16px;">
           <div class="card-header">
               <span class="card-title">Active Chat Context</span>
           </div>
           <div style="padding: 0 0 4px 0;">
               <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 12px;">
                   Select which context to use when chatting via OpenWebUI.
               </p>
               <select id="activeContextSelect" onchange="switchActiveContext(this.value)" style="padding: 8px; border: 1px solid var(--border); border-radius: 4px; font-size: 14px; min-width: 300px;">
                   <option value="">Loading...</option>
               </select>
               <span id="switchStatus" style="margin-left: 12px; font-size: 13px;"></span>
           </div>
       </div>

       <div class="card">
           <div class="card-header">
               <span>All Contexts <span id="count" class="badge badge-info">0</span></span>
               <div style="display: flex; gap: 8px;">
                   <button class="btn btn-primary" onclick="showCreateModal()">+ Create Context</button>
                   <button class="btn" onclick="loadContexts()">Refresh</button>
               </div>
           </div>
           <div id="contexts">
               <div class="loading">Loading...</div>
           </div>
       </div>

       <!-- Create Context Modal -->
       <div id="createModal" class="modal" style="display: none;">
           <div class="modal-content">
               <h3>Create Context</h3>
               <form id="createForm" onsubmit="createContext(event)">
                   <div class="form-group">
                       <label>Name</label>
                       <input type="text" id="ctxName" required placeholder="e.g., project-backend">
                   </div>
                   <div class="form-group">
                       <label>Type</label>
                       <select id="ctxType">
                           <option value="personal">Personal</option>
                           <option value="virtual">Virtual</option>
                           <option value="git_repo">Git Repo</option>
                           <option value="devops">DevOps</option>
                       </select>
                   </div>
                   <div class="modal-actions">
                       <button type="button" class="btn" onclick="hideCreateModal()">Cancel</button>
                       <button type="submit" class="btn btn-primary">Create</button>
                   </div>
               </form>
           </div>
       </div>
   """
   ```

   Add extra_css:
   ```python
   extra_css = """
       .context-card { display: block; padding: 16px; border: 1px solid var(--border); border-radius: 6px; margin-bottom: 8px; text-decoration: none; color: inherit; transition: all 0.15s; }
       .context-card:hover { border-color: var(--primary); background: #f8faff; }
       .context-header { display: flex; justify-content: space-between; align-items: flex-start; }
       .context-name { font-weight: 600; font-size: 15px; margin-bottom: 4px; color: var(--text); }
       .context-id { font-family: monospace; font-size: 11px; color: var(--text-muted); }
       .context-meta { font-size: 12px; color: var(--text-muted); display: flex; gap: 16px; margin-top: 8px; flex-wrap: wrap; }
       .context-owner { font-size: 12px; color: var(--primary); }
       .modal { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 1000; }
       .modal-content { background: var(--bg-card); padding: 24px; border-radius: 8px; width: 100%; max-width: 480px; }
       .modal-content h3 { margin: 0 0 16px 0; }
       .form-group { margin-bottom: 16px; }
       .form-group label { display: block; margin-bottom: 4px; font-size: 13px; font-weight: 500; }
       .form-group input, .form-group select { width: 100%; padding: 8px; border: 1px solid var(--border); border-radius: 4px; font-size: 14px; background: var(--bg); color: var(--text); }
       .modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
       .badge-type-personal { background: #dbeafe; color: #1e40af; }
       .badge-type-virtual { background: #e5e7eb; color: #374151; }
       .badge-type-git_repo { background: #d1fae5; color: #065f46; }
       .badge-type-devops { background: #ede9fe; color: #6d28d9; }
   """
   ```

   Add extra_js:
   ```python
   extra_js = """
       async function loadContexts() {
           const res = await fetchWithErrorHandling('/platformadmin/contexts');
           if (!res) {
               document.getElementById('contexts').innerHTML = '<div style="color: var(--error)">Failed to load</div>';
               return;
           }
           const data = await res.json();
           renderContexts(data);
       }

       function renderContexts(data) {
           const contexts = data.contexts || [];
           document.getElementById('count').textContent = data.total || 0;
           document.getElementById('totalContexts').textContent = data.total || 0;
           document.getElementById('personalContexts').textContent = contexts.filter(c => c.type === 'personal').length;
           document.getElementById('virtualContexts').textContent = contexts.filter(c => c.type === 'virtual').length;

           const el = document.getElementById('contexts');
           if (contexts.length === 0) {
               el.innerHTML = '<div class="empty-state">No contexts found</div>';
               return;
           }
           el.innerHTML = contexts.map(c => {
               const typeBadge = '<span class="badge badge-type-' + c.type + '">' + c.type + '</span>';
               return '<a href="/platformadmin/contexts/' + c.id + '/" class="context-card">' +
                   '<div class="context-header"><div>' +
                   '<div class="context-name">' + escapeHtml(c.name) + ' ' + typeBadge + '</div>' +
                   '<div class="context-id">' + c.id + '</div>' +
                   '</div></div>' +
                   '<div class="context-meta">' +
                   '<span>Conversations: ' + c.conversation_count + '</span>' +
                   '<span>OAuth: ' + c.oauth_token_count + '</span>' +
                   '<span>Permissions: ' + c.tool_permission_count + '</span>' +
                   '</div></a>';
           }).join('');
       }

       async function loadMyContexts() {
           const res = await fetchWithErrorHandling('/platformadmin/users/me/contexts');
           if (!res) return;
           const data = await res.json();
           const select = document.getElementById('activeContextSelect');
           const contexts = data.contexts || [];
           select.innerHTML = contexts.map(c => {
               const label = c.name + (c.is_default ? ' (personal)' : '') + ' [' + c.role + ']';
               return '<option value="' + c.id + '"' + (c.is_active ? ' selected' : '') + '>' + escapeHtml(label) + '</option>';
           }).join('');
       }

       async function switchActiveContext(contextId) {
           const statusEl = document.getElementById('switchStatus');
           statusEl.textContent = 'Switching...';
           const res = await fetchWithErrorHandling('/platformadmin/users/me/active-context', {
               method: 'PUT',
               headers: { 'Content-Type': 'application/json' },
               body: JSON.stringify({ context_id: contextId || null })
           });
           if (res) {
               const data = await res.json();
               statusEl.textContent = data.message || 'Done';
               statusEl.style.color = 'var(--success)';
               setTimeout(() => { statusEl.textContent = ''; }, 3000);
           } else {
               statusEl.textContent = 'Failed';
               statusEl.style.color = 'var(--error)';
           }
       }

       function showCreateModal() { document.getElementById('createModal').style.display = 'flex'; }
       function hideCreateModal() { document.getElementById('createModal').style.display = 'none'; document.getElementById('createForm').reset(); }

       async function createContext(e) {
           e.preventDefault();
           const name = document.getElementById('ctxName').value;
           const type = document.getElementById('ctxType').value;
           const res = await fetchWithErrorHandling('/platformadmin/contexts', {
               method: 'POST',
               headers: { 'Content-Type': 'application/json' },
               body: JSON.stringify({ name: name, type: type })
           });
           if (res) {
               showToast('Context created', 'success');
               hideCreateModal();
               loadContexts();
               loadMyContexts();
           }
       }

       function escapeHtml(str) {
           if (!str) return '';
           const div = document.createElement('div');
           div.textContent = str;
           return div.innerHTML;
       }

       loadContexts();
       loadMyContexts();
   """
   ```

2. **Add workspace and MCP counts to context list API**

   File: `services/agent/src/interfaces/http/admin_contexts.py`

   Update the `list_contexts` endpoint to also count Workspaces and McpServers. Add imports:
   ```python
   from core.db.models import Context, Conversation, McpServer, ToolPermission, Workspace
   ```
   (Replace existing `from core.db.models import Context, Conversation, ToolPermission`)

   Update the `ContextInfo` model to add:
   ```python
   workspace_count: int
   mcp_server_count: int
   ```

   Update the query in `list_contexts` to include:
   ```python
   stmt = (
       select(
           Context,
           func.count(distinct(Conversation.id)).label("conv_count"),
           func.count(distinct(OAuthToken.id)).label("oauth_count"),
           func.count(distinct(ToolPermission.id)).label("perm_count"),
           func.count(distinct(Workspace.id)).label("ws_count"),
           func.count(distinct(McpServer.id)).label("mcp_count"),
       )
       .outerjoin(Conversation, Conversation.context_id == Context.id)
       .outerjoin(OAuthToken, OAuthToken.context_id == Context.id)
       .outerjoin(ToolPermission, ToolPermission.context_id == Context.id)
       .outerjoin(Workspace, Workspace.context_id == Context.id)
       .outerjoin(McpServer, McpServer.context_id == Context.id)
       .group_by(Context.id)
       .order_by(Context.name)
   )
   ```

   Update the result unpacking:
   ```python
   for ctx, conv_count, oauth_count, perm_count, ws_count, mcp_count in rows:
       context_infos.append(
           ContextInfo(
               # ... existing fields ...
               workspace_count=ws_count,
               mcp_server_count=mcp_count,
           )
       )
   ```

**Ops tasks:**
- Run `cd /home/magnus/dev/ai-agent-platform && ./stack check`

**Files affected:**
- `services/agent/src/interfaces/http/admin_contexts.py` (modify -- enhanced list page + API)

---

### Phase 4: Context Detail Page with Tabs

This is the core of the restructure. A new HTML page at `/platformadmin/contexts/{id}/` with 6 tabs that reuse existing API endpoints.

**Engineer tasks:**

1. **Create the context detail HTML template**

   File: `services/agent/src/interfaces/http/templates/admin_context_detail.html`

   This file uses the `<!-- SECTION_SEPARATOR -->` pattern (same as `admin_mcp.html`). Three sections: HTML content, CSS, JavaScript.

   The template must include:
   - Tab bar with 6 tabs: Overview, Permissions, Workspaces, MCP Servers, OAuth Tokens, Conversations
   - Each tab fetches data from existing API endpoints
   - Tab state managed via URL hash (e.g., `#permissions`)

   The template should reference `CONTEXT_ID` as a placeholder that gets replaced at render time.

   **Template structure (abbreviated -- full implementation by Engineer):**

   Section 1 (HTML):
   ```html
   <div style="display: flex; align-items: center; gap: 16px; margin-bottom: 24px;">
       <a href="/platformadmin/contexts/" class="btn btn-sm">&larr; All Contexts</a>
       <h1 class="page-title" style="margin-bottom: 0;" id="contextTitle">Loading...</h1>
       <span class="badge" id="contextType"></span>
   </div>

   <div class="tab-bar">
       <button class="tab active" data-tab="overview" onclick="switchTab('overview')">Overview</button>
       <button class="tab" data-tab="permissions" onclick="switchTab('permissions')">Permissions</button>
       <button class="tab" data-tab="workspaces" onclick="switchTab('workspaces')">Workspaces</button>
       <button class="tab" data-tab="mcp" onclick="switchTab('mcp')">MCP Servers</button>
       <button class="tab" data-tab="oauth" onclick="switchTab('oauth')">OAuth Tokens</button>
       <button class="tab" data-tab="conversations" onclick="switchTab('conversations')">Conversations</button>
   </div>

   <div id="tab-overview" class="tab-content active">
       <!-- Context overview: name, type, config, pinned_files, default_cwd, members -->
       <div class="card" id="overviewCard">
           <div class="loading">Loading...</div>
       </div>
   </div>

   <div id="tab-permissions" class="tab-content" style="display:none;">
       <div class="card" id="permissionsCard">
           <div class="loading">Loading...</div>
       </div>
   </div>

   <div id="tab-workspaces" class="tab-content" style="display:none;">
       <div class="card" id="workspacesCard">
           <div class="loading">Loading...</div>
       </div>
   </div>

   <div id="tab-mcp" class="tab-content" style="display:none;">
       <div class="card" id="mcpCard">
           <div class="loading">Loading...</div>
       </div>
   </div>

   <div id="tab-oauth" class="tab-content" style="display:none;">
       <div class="card" id="oauthCard">
           <div class="loading">Loading...</div>
       </div>
   </div>

   <div id="tab-conversations" class="tab-content" style="display:none;">
       <div class="card" id="conversationsCard">
           <div class="loading">Loading...</div>
       </div>
   </div>
   ```

   Section 2 (CSS):
   ```css
   .tab-bar { display: flex; gap: 0; border-bottom: 2px solid var(--border); margin-bottom: 24px; }
   .tab { padding: 12px 20px; border: none; background: none; cursor: pointer; font-size: 14px; font-weight: 500; color: var(--text-muted); border-bottom: 2px solid transparent; margin-bottom: -2px; transition: all 0.15s; }
   .tab:hover { color: var(--text); }
   .tab.active { color: var(--primary); border-bottom-color: var(--primary); }
   .tab-content { display: none; }
   .tab-content.active { display: block; }
   .member-row { display: flex; align-items: center; gap: 12px; padding: 8px 0; border-bottom: 1px solid var(--border); }
   .member-row:last-child { border-bottom: none; }
   .config-pre { background: var(--bg); padding: 12px; border-radius: 4px; font-family: monospace; font-size: 13px; white-space: pre-wrap; overflow-x: auto; }
   .toggle-switch { position: relative; display: inline-block; width: 44px; height: 24px; }
   .toggle-switch input { opacity: 0; width: 0; height: 0; }
   .toggle-slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #e2e8f0; transition: 0.2s; border-radius: 24px; }
   .toggle-slider:before { position: absolute; content: ""; height: 18px; width: 18px; left: 3px; bottom: 3px; background-color: white; transition: 0.2s; border-radius: 50%; }
   input:checked + .toggle-slider { background-color: var(--success); }
   input:checked + .toggle-slider:before { transform: translateX(20px); }
   ```

   Section 3 (JavaScript):
   The JS must:
   - `CONTEXT_ID` is injected by the Python endpoint as a literal string replacement
   - `switchTab(tab)` function: shows/hides tab content, updates URL hash, lazy-loads tab data on first view
   - `loadOverview()`: fetches `/platformadmin/contexts/CONTEXT_ID` and renders overview
   - `loadPermissions()`: fetches `/platformadmin/permissions/contexts/CONTEXT_ID` and renders permission toggles
   - `loadWorkspaces()`: fetches `/platformadmin/workspaces/list?context_id=CONTEXT_ID` and renders workspace list
   - `loadMcpServers()`: fetches `/platformadmin/mcp/servers` and filters by context_id client-side (or add filter)
   - `loadOAuthTokens()`: fetches `/platformadmin/oauth/tokens?context_id=CONTEXT_ID` and renders token list
   - `loadConversations()`: fetches `/platformadmin/contexts/CONTEXT_ID` (already has conversations in detail response) and renders conversation table

   **Key JS functions (abbreviated):**
   ```javascript
   const CONTEXT_ID = '__CONTEXT_ID__';
   const loadedTabs = {};

   function switchTab(tab) {
       document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
       document.querySelectorAll('.tab-content').forEach(t => { t.style.display = 'none'; t.classList.remove('active'); });
       document.querySelector('[data-tab="' + tab + '"]').classList.add('active');
       const content = document.getElementById('tab-' + tab);
       content.style.display = 'block';
       content.classList.add('active');
       window.location.hash = tab;
       if (!loadedTabs[tab]) {
           loadedTabs[tab] = true;
           if (tab === 'overview') loadOverview();
           else if (tab === 'permissions') loadPermissions();
           else if (tab === 'workspaces') loadWorkspaces();
           else if (tab === 'mcp') loadMcpServers();
           else if (tab === 'oauth') loadOAuthTokens();
           else if (tab === 'conversations') loadConversations();
       }
   }

   // Read hash from URL on page load
   const initialTab = window.location.hash.replace('#', '') || 'overview';
   switchTab(initialTab);
   ```

   **Important implementation details for each tab:**

   **Permissions tab:** Reuse the toggle logic from `admin_permissions.py`'s JS. The API endpoint is `GET /platformadmin/permissions/contexts/{context_id}` which returns tool list with allowed status. Toggle via `PUT /platformadmin/permissions/contexts/{context_id}/tools/{tool_name}`. Bulk actions via `POST /platformadmin/permissions/contexts/{context_id}/bulk`.

   **Workspaces tab:** API endpoint is `GET /platformadmin/workspaces/list?context_id={context_id}`. Add workspace via `POST /platformadmin/workspaces` with `context_id` in body. Sync via `POST /platformadmin/workspaces/{id}/sync`. Delete via `DELETE /platformadmin/workspaces/{id}`.

   **MCP Servers tab:** API endpoint is `GET /platformadmin/mcp/servers`. Filter client-side by context_id. Add via `POST /platformadmin/mcp/servers`. Edit/delete/test via respective endpoints.

   **OAuth Tokens tab:** API endpoint is `GET /platformadmin/oauth/tokens?context_id={context_id}`. Delete (revoke) via `DELETE /platformadmin/oauth/tokens/{id}`. Initiate OAuth via `GET /platformadmin/oauth/initiate/{provider}` (existing -- keeps working because it resolves context from admin user).

   **Conversations tab:** Use the existing detail endpoint `GET /platformadmin/contexts/{context_id}` which returns conversations array. Display as a table with columns: ID, Platform, Created, CWD.

   **Overview tab:** Display context metadata in a form-like layout:
   - Name (editable -- for future)
   - Type (badge)
   - ID (monospace)
   - Config (JSON pre)
   - Pinned files (list)
   - Default CWD
   - Members (from UserContext join) -- query needed

2. **Add context detail HTML endpoint**

   File: `services/agent/src/interfaces/http/admin_contexts.py`

   Add a new endpoint for the context detail page. Add this import:
   ```python
   from pathlib import Path
   ```

   Add this endpoint after the existing `contexts_dashboard` endpoint:
   ```python
   @router.get("/{context_id}/", response_class=UTF8HTMLResponse)
   async def context_detail_page(
       context_id: UUID,
       admin: AdminUser = Depends(require_admin_or_redirect),
       session: AsyncSession = Depends(get_db),
   ) -> str:
       """Context detail page with tabbed sub-views.

       Tabs: Overview, Permissions, Workspaces, MCP Servers, OAuth Tokens, Conversations.
       Each tab lazy-loads data from existing API endpoints.
       """
       # Verify context exists
       stmt = select(Context).where(Context.id == context_id)
       result = await session.execute(stmt)
       ctx = result.scalar_one_or_none()

       if not ctx:
           raise HTTPException(
               status_code=status.HTTP_404_NOT_FOUND,
               detail=f"Context {context_id} not found",
           )

       template_path = Path(__file__).parent / "templates" / "admin_context_detail.html"
       parts = template_path.read_text(encoding="utf-8").split("<!-- SECTION_SEPARATOR -->")

       content = (parts[0] if len(parts) > 0 else "").replace("__CONTEXT_ID__", str(context_id))
       extra_css = parts[1] if len(parts) > 1 else ""
       extra_js = (parts[2] if len(parts) > 2 else "").replace("__CONTEXT_ID__", str(context_id))

       return render_admin_page(
           title=f"Context: {ctx.name}",
           active_page=f"/platformadmin/contexts/{context_id}/",
           content=content,
           user_name=admin.display_name or admin.email.split("@")[0],
           user_email=admin.email,
           breadcrumbs=[
               ("Contexts", "/platformadmin/contexts/"),
               (ctx.name, "#"),
           ],
           extra_css=extra_css,
           extra_js=extra_js,
       )
   ```

3. **Add context members API endpoint**

   File: `services/agent/src/interfaces/http/admin_contexts.py`

   Add an endpoint to get context members (needed by the Overview tab):
   ```python
   @router.get(
       "/{context_id}/members",
       dependencies=[Depends(verify_admin_user)],
   )
   async def get_context_members(
       context_id: UUID,
       session: AsyncSession = Depends(get_db),
   ) -> dict[str, object]:
       """Get users linked to a context."""
       from core.db.models import User, UserContext

       stmt = (
           select(User.id, User.email, User.display_name, UserContext.role, UserContext.is_default)
           .join(UserContext, User.id == UserContext.user_id)
           .where(UserContext.context_id == context_id)
           .order_by(UserContext.role, User.display_name)
       )
       result = await session.execute(stmt)
       rows = result.all()

       members = [
           {
               "user_id": str(uid),
               "email": email,
               "display_name": display_name or email.split("@")[0],
               "role": role,
               "is_default": is_default,
           }
           for uid, email, display_name, role, is_default in rows
       ]

       return {"members": members, "total": len(members)}
   ```

4. **Add MCP servers filter by context_id**

   File: `services/agent/src/interfaces/http/admin_mcp.py`

   Update the `list_mcp_servers` endpoint to accept an optional `context_id` query parameter:

   Change the function signature from:
   ```python
   async def list_mcp_servers(
       session: AsyncSession = Depends(get_db),
   ) -> McpServerListResponse:
   ```

   To:
   ```python
   async def list_mcp_servers(
       context_id: UUID | None = None,
       session: AsyncSession = Depends(get_db),
   ) -> McpServerListResponse:
   ```

   And add after the base `stmt` definition:
   ```python
   if context_id:
       stmt = stmt.where(McpServer.context_id == context_id)
   ```

**Ops tasks:**
- Run `cd /home/magnus/dev/ai-agent-platform && ./stack check`

**Files affected:**
- `services/agent/src/interfaces/http/templates/admin_context_detail.html` (create)
- `services/agent/src/interfaces/http/admin_contexts.py` (modify -- add detail endpoint + members API)
- `services/agent/src/interfaces/http/admin_mcp.py` (modify -- add context_id filter)

---

### Phase 5: Keep Old Routes Working (Backward Compatibility)

The old standalone pages (`/permissions/`, `/workspaces/`, `/mcp/`, `/oauth/`) still have their routers registered in `app.py`. We keep them working since their API endpoints are still called by the context detail page tabs. The HTML dashboards will remain accessible but are removed from the sidebar navigation (Phase 2 already handled that).

**No code changes needed for this phase.** The old routes work because:
1. Router registrations in `app.py` are unchanged
2. API endpoints are called by the new context detail tabs
3. Only navigation was updated (Phase 2)

This is intentional: old bookmarks and API clients continue working.

---

### Phase 6: User Detail Page (Optional, Phase 2 Enhancement)

This phase adds a user detail page at `/platformadmin/users/{id}/` with tabs for Contexts and Credentials. This is lower priority and can be implemented after the core context restructure is done.

**Engineer tasks (deferred):**

1. Create `/platformadmin/users/{id}/` HTML page with tabs:
   - **Contexts tab**: Show contexts the user belongs to (via UserContext), with role and is_default badge
   - **Credentials tab**: Show user's credentials (reuse `/platformadmin/credentials/user/{user_id}` API)

2. Make user rows in the users list page clickable, linking to the detail page.

**This phase is NOT required for the initial implementation.** It can be implemented in a follow-up PR.

---

## 4. Configuration Changes

### Environment Variables

No new environment variables needed.

### Database Migration

One migration: `20260211_add_active_context_to_users.py` (Phase 1)
- Adds nullable `active_context_id` column with FK to `contexts.id`
- Non-breaking: existing rows get NULL (fall back to default behavior)

---

## 5. Testing Strategy

### Unit Tests

File: `services/agent/src/core/tests/test_context_service.py`

Add test for active context resolution:
```python
@pytest.mark.asyncio
async def test_resolve_uses_active_context_when_set(db_session):
    """When user has active_context_id set, that context is used."""
    # Setup: user with personal context + active context pointing to shared context
    ...

@pytest.mark.asyncio
async def test_resolve_falls_back_when_active_context_inaccessible(db_session):
    """When active context is no longer accessible, falls back to default."""
    ...

@pytest.mark.asyncio
async def test_resolve_clears_active_context_when_deleted(db_session):
    """When active context is deleted (SET NULL), falls back to default."""
    ...
```

### Manual Testing

1. Navigate to `/platformadmin/contexts/` -- should show enhanced context list
2. Click on a context -- should show tabbed detail page
3. Click each tab -- should lazy-load content from existing APIs
4. Toggle a permission in the Permissions tab -- should persist
5. Add a workspace in the Workspaces tab -- should work
6. Switch active context in the context selector -- should persist
7. Verify old URLs still work: `/platformadmin/permissions/`, `/platformadmin/workspaces/`, etc.
8. Verify chat uses the selected active context (check logs for context_id in agent requests)

---

## 6. Quality Checks

After each phase:
```bash
cd /home/magnus/dev/ai-agent-platform && ./stack check
```

This runs: Ruff (linting) -> Black (formatting) -> Mypy (types) -> Pytest (tests)

**Expected issues to watch for:**
- Mypy may flag the new `active_context_id` relationship if not typed correctly
- The `User` model change needs to be reflected in any tests that mock User objects
- The `ContextService` change needs updated imports

---

## 7. Security Considerations

1. **Context access control:** The `set_active_context` endpoint verifies the user has a `UserContext` record linking them to the target context. Users cannot set active context to one they don't have access to.

2. **Admin-only access:** All context detail endpoints require admin authentication (existing `verify_admin_user` / `require_admin_or_redirect` dependencies).

3. **CSRF protection:** All state-changing endpoints (`PUT`, `POST`, `DELETE`) include `Depends(require_csrf)`.

4. **Path traversal:** Context IDs are UUIDs validated by FastAPI's path parameter parsing. No user-controlled strings in filesystem paths.

5. **XSS prevention:** All user-provided data displayed in HTML uses the `escapeHtml()` JS function (established pattern in existing admin pages).

6. **SQL injection:** All database queries use SQLAlchemy ORM with parameterized queries (existing pattern).

7. **FK constraint safety:** The `active_context_id` FK uses `ondelete="SET NULL"`, so deleting a context automatically clears it from users.

---

## 8. Success Criteria

- [ ] Context list page shows enhanced cards with all entity counts
- [ ] Clicking a context opens the tabbed detail page
- [ ] All 6 tabs (Overview, Permissions, Workspaces, MCP, OAuth, Conversations) load correctly
- [ ] Permission toggles work from within the context detail page
- [ ] Workspace CRUD works from within the context detail page
- [ ] MCP server management works from within the context detail page
- [ ] Active context switcher changes which context is used for chat
- [ ] Navigation sidebar is simplified (removed standalone pages)
- [ ] Old URLs still work (backward compatibility)
- [ ] `./stack check` passes (all quality gates green)

---

## 9. Agent Delegation

### Engineer (Sonnet) - Implementation
- Phase 1: Schema update, migration, ContextService changes, active context endpoints
- Phase 2: Navigation changes
- Phase 3: Enhanced context list page
- Phase 4: Context detail template + endpoints (the bulk of the work)

### Ops (Haiku - 10x cheaper) - Quality & Deployment
- Run `./stack check` after each phase
- Fix simple lint errors (auto-fixable by ruff/black)
- Run alembic migration
- Git operations (commit, push, PR)
- Report test results
- Escalate complex Mypy/test errors to Engineer

### Implementation Order

Phases 1-4 should be implemented sequentially. Phase 5 requires no code. Phase 6 is deferred.

Each phase should follow this pattern:
1. Engineer writes/modifies code
2. Engineer delegates to Ops for quality check (`./stack check`)
3. Ops reports back (or escalates if complex errors)
4. Repeat for next phase
5. After all phases: Ops commits and creates PR

### Cost Optimization
- Engineer handles all code writing (needs Sonnet reasoning)
- Ops handles all `./stack check` runs and git operations (Haiku is sufficient)
- The context detail template (Phase 4) is the most code-intensive -- Engineer should create the full template in one pass to minimize back-and-forth
