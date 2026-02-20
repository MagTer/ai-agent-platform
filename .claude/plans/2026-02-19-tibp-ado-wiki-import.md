# TIBP Azure DevOps Wiki Import

**Date:** 2026-02-19
**Status:** Ready for implementation

---

## 1. Feature Overview

Replace the existing local-file-based TIBP wiki ingestion script (`scripts/ingest_tibp_wiki.py`) with an Azure DevOps Wiki import system that:

1. Fetches wiki pages from Azure DevOps REST API using per-context PAT credentials
2. Embeds and indexes pages into the existing `tibp-wiki` Qdrant collection
3. Provides an admin portal UI to trigger imports and monitor status
4. Can be triggered on a schedule via the existing `ScheduledJob` mechanism
5. Preserves the existing `tibp_wiki_search` tool behavior (queries Qdrant `tibp-wiki` collection via RAG manager)

**What stays the same:**
- `TibpWikiSearchTool` in `core/tools/tibp_wiki_search.py` — NO changes needed. It queries Qdrant via `get_rag_manager()` regardless of how data was ingested.
- `tibp_researcher` skill in `skills/work/tibp_researcher.md` — NO changes needed.
- `config/tools.yaml` registration of `tibp_wiki_search` — NO changes needed.

**What changes:**
- New admin module for wiki import management (status + manual trigger)
- New DB table for tracking wiki import state per context
- New service module for ADO wiki API interaction + Qdrant ingestion
- New `wiki_sync` tool + skill for scheduler-triggered imports
- Old `scripts/ingest_tibp_wiki.py` superseded (can be removed)

---

## 2. Architecture Decisions

### Layer Placement

| Component | Layer | Rationale |
|-----------|-------|-----------|
| `WikiImportService` | `core/wiki/` | Core business logic. Needs `CredentialService`, DB models, and `get_rag_manager()`. Lives in core to avoid cross-layer imports. |
| `WikiSyncTool` | `core/tools/` | Thin tool wrapper over the import service, callable from skills |
| `admin_wiki.py` | `interfaces/http/` | Admin portal endpoints (follows existing admin module pattern) |
| `WikiImport` model | `core/db/models.py` | DB model for import state tracking |
| `wiki_sync.md` | `skills/system/` | Thin skill used by scheduler jobs |
| Alembic migration | `alembic/versions/` | New table |

### Data Model: `wiki_imports` Table

Store import state in PostgreSQL (durable across restarts, queryable for the portal).

```python
class WikiImport(Base):
    __tablename__ = "wiki_imports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    context_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contexts.id", ondelete="CASCADE"), index=True
    )
    wiki_identifier: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="idle")
    total_pages: Mapped[int] = mapped_column(Integer, default=0)
    pages_imported: Mapped[int] = mapped_column(Integer, default=0)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    last_import_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_import_completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    context = relationship("Context")

    __table_args__ = (
        UniqueConstraint("context_id", "wiki_identifier", name="uq_context_wiki_import"),
    )
```

---

## 3. Implementation Roadmap

### Step 1: Database Model and Migration

**Files affected:**
- `services/agent/src/core/db/models.py` (modify — add WikiImport class after SystemConfig)
- `services/agent/alembic/versions/20260219_add_wiki_imports.py` (create)

**DB Model — add to `core/db/models.py` after SystemConfig:**

```python
class WikiImport(Base):
    """Tracks Azure DevOps wiki import state per context."""

    __tablename__ = "wiki_imports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    context_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contexts.id", ondelete="CASCADE"), index=True
    )
    wiki_identifier: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="idle")
    total_pages: Mapped[int] = mapped_column(Integer, default=0)
    pages_imported: Mapped[int] = mapped_column(Integer, default=0)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    last_import_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_import_completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    context = relationship("Context")

    __table_args__ = (
        UniqueConstraint("context_id", "wiki_identifier", name="uq_context_wiki_import"),
    )
```

**Alembic migration:**

```python
"""Add wiki_imports table.

Revision ID: 20260219_wiki_imports
Revises: <current head revision>
Create Date: 2026-02-19
"""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260219_wiki_imports"
down_revision: str | Sequence[str] | None = "<current head>"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wiki_imports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("context_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("contexts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("wiki_identifier", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'idle'")),
        sa.Column("total_pages", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("pages_imported", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_chunks", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("last_import_started_at", sa.DateTime(), nullable=True),
        sa.Column("last_import_completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("context_id", "wiki_identifier", name="uq_context_wiki_import"),
    )


def downgrade() -> None:
    op.drop_table("wiki_imports")
```

---

### Step 2: Wiki Import Service (`core/wiki/service.py`)

**Files affected:**
- `services/agent/src/core/wiki/__init__.py` (create — empty)
- `services/agent/src/core/wiki/service.py` (create)

The service encapsulates:
1. Fetching wiki pages from Azure DevOps REST API via httpx async client
2. Embedding + ingesting via RAG manager
3. Tracking state in `wiki_imports` table

Key points:
- Uses `BasicAuth(username="", password=pat)` — ADO PAT goes in the password field with empty username
- Tree fetch uses `recursionLevel=full` to get all paths in one call, then fetches content per page
- Progress is committed every 10 pages so the admin portal can show live progress
- The `force=True` path deletes and recreates the Qdrant collection (full re-index)

```python
"""Service for importing Azure DevOps wiki pages into Qdrant."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from urllib.parse import unquote
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth.credential_service import CredentialService
from core.db.models import WikiImport
from core.providers import get_rag_manager
from core.runtime.config import get_settings

LOGGER = logging.getLogger(__name__)

COLLECTION_NAME = "tibp-wiki"
ADO_API_VERSION = "7.1"
ADO_REQUEST_TIMEOUT = 30.0


class WikiImportError(Exception):
    """Raised when wiki import fails."""


class WikiPage:
    def __init__(self, path: str, content: str, order: int = 0) -> None:
        self.path = path
        self.content = content
        self.order = order


async def _get_ado_credentials(
    context_id: UUID,
    session: AsyncSession,
) -> tuple[str, str, str | None] | None:
    """Get Azure DevOps PAT and org/project from CredentialService.

    Returns (pat, org_url, project) or None if not configured.
    """
    settings = get_settings()
    if not settings.credential_encryption_key:
        return None

    cred_service = CredentialService(settings.credential_encryption_key)
    result = await cred_service.get_credential_with_metadata(
        context_id=context_id,
        credential_type="azure_devops_pat",
        session=session,
    )
    if not result:
        return None

    pat, metadata = result
    org_url_raw = metadata.get("organization_url", "")
    if not org_url_raw:
        return None

    url = unquote(org_url_raw.strip().rstrip("/"))
    match = re.match(r"^(https://dev\.azure\.com/[^/]+)(?:/(.+))?$", url)
    if match:
        return pat, match.group(1), match.group(2) or None

    return pat, url, None


def _collect_page_paths(node: dict[str, object], paths: list[str]) -> None:
    """Recursively collect page paths from the ADO wiki tree response.

    The tree API returns: {"path": "/", "subPages": [{"path": "/Page1", ...}, ...]}
    Root path "/" is excluded.
    """
    path = node.get("path")
    if isinstance(path, str) and path != "/":
        paths.append(path)
    sub_pages = node.get("subPages")
    if isinstance(sub_pages, list):
        for sub in sub_pages:
            if isinstance(sub, dict):
                _collect_page_paths(sub, paths)


async def fetch_wiki_page_tree(
    pat: str,
    org_url: str,
    project: str,
    wiki_identifier: str | None = None,
) -> list[WikiPage]:
    """Fetch all wiki pages from ADO API with content.

    Step 1: GET pages?path=/&recursionLevel=full  → page paths
    Step 2: GET pages?path=P&includeContent=true  → page content (per page)
    """
    if not wiki_identifier:
        wiki_identifier = f"{project}.wiki"

    tree_url = (
        f"{org_url}/{project}/_apis/wiki/wikis/{wiki_identifier}/pages"
        f"?path=/&recursionLevel=full&api-version={ADO_API_VERSION}"
    )

    auth = httpx.BasicAuth(username="", password=pat)
    async with httpx.AsyncClient(timeout=ADO_REQUEST_TIMEOUT, auth=auth) as client:
        response = await client.get(tree_url)
        response.raise_for_status()

        page_paths: list[str] = []
        _collect_page_paths(response.json(), page_paths)
        LOGGER.info("Found %d wiki pages in %s/%s", len(page_paths), project, wiki_identifier)

        pages: list[WikiPage] = []
        for i, path in enumerate(page_paths):
            content_url = (
                f"{org_url}/{project}/_apis/wiki/wikis/{wiki_identifier}/pages"
                f"?path={path}&includeContent=true&api-version={ADO_API_VERSION}"
            )
            try:
                r = await client.get(content_url)
                r.raise_for_status()
                content = r.json().get("content", "")
                if content and content.strip():
                    pages.append(WikiPage(path=path, content=content, order=i))
            except httpx.HTTPStatusError as e:
                LOGGER.warning("Failed to fetch wiki page %s: %s", path, e)

    LOGGER.info("Fetched content for %d/%d pages", len(pages), len(page_paths))
    return pages


async def full_import(
    context_id: UUID,
    session: AsyncSession,
    wiki_identifier: str | None = None,
    force: bool = False,
) -> str:
    """Fetch all wiki pages, embed, and index into Qdrant.

    Designed to run as a background task. Updates WikiImport record with
    progress as it proceeds. Returns a summary string on completion.

    Args:
        context_id: Context with ADO credentials.
        session: Database session.
        wiki_identifier: Override wiki identifier (default: {project}.wiki).
        force: Delete and recreate the Qdrant collection before importing.

    Returns:
        Summary string, e.g. "Imported 42 pages (187 chunks)"
    """
    creds = await _get_ado_credentials(context_id, session)
    if not creds:
        raise WikiImportError(
            "Azure DevOps credentials not configured for this context. "
            "Add a PAT via Context Detail -> Credentials."
        )

    pat, org_url, project = creds
    if not project:
        raise WikiImportError(
            "Project not specified in credentials URL. "
            "URL should be: https://dev.azure.com/Org/Project"
        )

    effective_wiki_id = wiki_identifier or f"{project}.wiki"

    # Get or create WikiImport record
    stmt = select(WikiImport).where(
        WikiImport.context_id == context_id,
        WikiImport.wiki_identifier == effective_wiki_id,
    )
    wiki_record = (await session.execute(stmt)).scalar_one_or_none()

    if not wiki_record:
        wiki_record = WikiImport(
            context_id=context_id,
            wiki_identifier=effective_wiki_id,
        )
        session.add(wiki_record)

    wiki_record.status = "fetching"
    wiki_record.last_error = None
    wiki_record.last_import_started_at = datetime.now(UTC).replace(tzinfo=None)
    wiki_record.pages_imported = 0
    wiki_record.total_chunks = 0
    await session.commit()
    await session.refresh(wiki_record)

    try:
        pages = await fetch_wiki_page_tree(pat, org_url, project, wiki_identifier=effective_wiki_id)
        wiki_record.total_pages = len(pages)
        await session.commit()

        if not pages:
            wiki_record.status = "completed"
            wiki_record.last_import_completed_at = datetime.now(UTC).replace(tzinfo=None)
            await session.commit()
            return "No wiki pages found."

        rag = get_rag_manager()

        if force:
            LOGGER.info("Force mode: recreating collection %s", COLLECTION_NAME)
            from qdrant_client.http import models as qm
            try:
                if await rag.client.collection_exists(COLLECTION_NAME):
                    await rag.client.delete_collection(COLLECTION_NAME)
            except Exception as e:
                LOGGER.warning("Could not delete collection: %s", e)
            await rag.client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=qm.VectorParams(size=4096, distance=qm.Distance.COSINE),
                hnsw_config=qm.HnswConfigDiff(m=32, ef_construct=256),
            )

        wiki_record.status = "embedding"
        await session.commit()

        total_chunks = 0
        for i, page in enumerate(pages):
            chunks = await rag.ingest_document(
                page.content,
                {"uri": page.path, "source": "tibp_wiki", "type": "documentation",
                 "context_id": str(context_id)},
                chunk_size=2000,
                chunk_overlap=300,
            )
            total_chunks += chunks
            wiki_record.pages_imported = i + 1
            wiki_record.total_chunks = total_chunks
            if (i + 1) % 10 == 0:
                await session.commit()

        wiki_record.status = "completed"
        wiki_record.total_chunks = total_chunks
        wiki_record.last_import_completed_at = datetime.now(UTC).replace(tzinfo=None)
        await session.commit()

        summary = f"Imported {len(pages)} pages ({total_chunks} chunks)"
        LOGGER.info("Wiki import completed for context %s: %s", context_id, summary)
        return summary

    except Exception as e:
        LOGGER.exception("Wiki import failed for context %s", context_id)
        wiki_record.status = "error"
        wiki_record.last_error = str(e)[:500]
        await session.commit()
        raise WikiImportError(f"Import failed: {e}") from e
```

---

### Step 3: Admin Portal Module (`interfaces/http/admin_wiki.py`)

**Files affected:**
- `services/agent/src/interfaces/http/admin_wiki.py` (create)
- `services/agent/src/interfaces/http/admin_shared.py` (modify — add NavItem)
- `services/agent/src/interfaces/http/app.py` (modify — register router)

**NavItem addition in `admin_shared.py`** — insert before the "Chat" external link:
```python
NavItem("Wiki Import", "/platformadmin/wiki/", "&#128218;", "features"),
```

**Router registration in `app.py`:**
```python
from interfaces.http.admin_wiki import router as admin_wiki_router
# ...
app.include_router(admin_wiki_router)
```

**Admin portal UI** — follows the pattern from `admin_workspaces.py`:
- `GET /platformadmin/wiki/` — HTML page with context selector dropdown, status card, and two action buttons: "Import" and "Force Re-import"
- `GET /platformadmin/wiki/status/{context_id}` — JSON: current WikiImport record(s) for the context
- `POST /platformadmin/wiki/import` — starts `full_import()` as a background asyncio task (returns immediately, status is polled)

The page auto-refreshes status every 5 seconds while import is running (badge states: idle → fetching → embedding → completed/error).

**Status card fields:**
- Status badge (idle / fetching / embedding / completed / error)
- Pages Imported / Total Pages
- Total Chunks
- Last Import timestamp
- Error message (if status=error)

**No "Trial Retrieval" or "Trial Import" buttons** — those are implementation verification steps only (see Section 6).

---

### Step 4: WikiSyncTool + Skill (for Scheduler integration)

**Files affected:**
- `services/agent/src/core/tools/wiki_sync.py` (create)
- `services/agent/config/tools.yaml` (modify — add wiki_sync)
- `skills/system/wiki_sync.md` (create)
- `services/agent/src/core/agents/executor.py` (modify — inject context_id + session for wiki_sync)

**`core/tools/wiki_sync.py`** — thin wrapper over the import service:

```python
"""Tool for syncing the TIBP wiki from Azure DevOps."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from core.tools.base import Tool
from core.wiki.service import WikiImportError, full_import

LOGGER = logging.getLogger(__name__)


class WikiSyncTool(Tool):
    """Syncs the TIBP wiki from Azure DevOps into Qdrant."""

    name = "wiki_sync"
    description = "Sync the TIBP corporate wiki from Azure DevOps into the search index."
    category = "system"

    async def run(
        self,
        action: str = "sync",
        context_id: UUID | None = None,
        session: AsyncSession | None = None,
        force: bool = False,
    ) -> str:
        if action != "sync":
            return f"Unknown action: {action}. Only 'sync' is supported."

        if not context_id or not session:
            return "wiki_sync requires context_id and session (injected by executor)."

        try:
            summary = await full_import(context_id, session, force=force)
            return f"Wiki sync complete. {summary}"
        except WikiImportError as e:
            return f"Wiki sync failed: {e}"
```

**`config/tools.yaml`** — add entry:
```yaml
- name: wiki_sync
  type: core.tools.wiki_sync.WikiSyncTool
  enabled: true
  description: "Syncs the TIBP corporate wiki from Azure DevOps into the search index."
```

**`skills/system/wiki_sync.md`** — thin skill that just calls the tool:

```markdown
---
name: wiki_sync
description: Syncs the TIBP corporate wiki from Azure DevOps into the Qdrant search index. Use for scheduled imports.
model: skillsrunner
max_turns: 2
tools:
  - wiki_sync
---
# Wiki Sync

Sync the TIBP wiki from Azure DevOps.

## Instructions

Call the wiki_sync tool immediately:

\`\`\`json
{"name": "wiki_sync", "arguments": {"action": "sync"}}
\`\`\`

Report the result to the user exactly as returned by the tool.
Do not ask questions. Do not add commentary.
```

**`executor.py` injection** — add `wiki_sync` to the context injection block in `_run_tool_gen()`:

```python
if step.tool in ("homey", "git_clone", "azure_devops", "wiki_sync"):
    context_id_str = (request.metadata or {}).get("context_id")
    if context_id_str:
        final_args["context_id"] = UUID(context_id_str)

if step.tool in ("git_clone", "azure_devops", "wiki_sync"):
    db_session = (request.metadata or {}).get("_db_session")
    if db_session:
        final_args["session"] = db_session
```

---

### Step 5: Unit Tests

**Files affected:**
- `services/agent/src/core/tests/test_wiki_service.py` (create)

Tests to write:

```python
class TestCollectPagePaths:
    def test_empty_tree(self) -> None: ...
    def test_flat_tree(self) -> None: ...
    def test_nested_tree(self) -> None: ...
    def test_root_excluded(self) -> None: ...

class TestGetAdoCredentials:
    async def test_returns_none_when_no_encryption_key(self) -> None: ...
    async def test_returns_none_when_no_credential(self) -> None: ...
    async def test_parses_org_url_with_project(self) -> None: ...

class TestFullImport:
    async def test_raises_when_no_credentials(self) -> None: ...
    async def test_raises_when_no_project(self) -> None: ...
    async def test_sets_status_error_on_api_failure(self) -> None: ...
```

---

## 4. Scheduler Integration

### How the existing scheduler works

The `SchedulerAdapter` polls `ScheduledJob` DB records every 60 seconds. For each due job it creates an `AgentRequest(prompt=job.skill_prompt, metadata={"context_id": str(job.context_id), ...})` and calls `AgentService.handle_request()`. This goes through the full LLM planner pipeline.

### What scheduler-triggered wiki sync requires

After Step 4 is implemented (WikiSyncTool + skill), setting up a scheduled import requires:

1. **No code changes** — all required infrastructure is already in place.

2. **Create a `ScheduledJob` record** via the Admin Portal → Scheduler → New Job:
   - **Name**: `TIBP Wiki Sync`
   - **Context**: the context that holds the Azure DevOps PAT
   - **Cron expression**: e.g. `0 3 * * *` (daily at 03:00 UTC)
   - **Skill prompt**: `Sync the TIBP wiki from Azure DevOps`
   - **Timeout**: `600` (10 minutes — the wiki import may take several minutes for large wikis)
   - **Notification**: optional email/Telegram on success/error

3. **What happens at runtime**:
   - Scheduler fires → creates `AgentRequest(prompt="Sync the TIBP wiki...")`
   - Planner routes to `wiki_sync` skill (via the skill description)
   - SkillExecutor calls `WikiSyncTool.run(action="sync")`
   - Executor injects `context_id` and `session` from request metadata
   - Tool calls `full_import()`, which updates the `wiki_imports` DB record throughout
   - Tool returns summary string (e.g. "Wiki sync complete. Imported 42 pages (187 chunks)")
   - Scheduler records this as `last_run_result`, sends notification if configured

### LLM overhead

The planner adds one LLM call (to route to the `wiki_sync` skill), and the skill adds one LLM call (to generate the tool call). Both are lightweight with a short context. Total overhead is negligible compared to the actual import time.

If zero LLM overhead is required in future (e.g., for very frequent syncs), the scheduler could be extended with a `job_type: "direct"` mechanism that bypasses AgentService and calls the service function directly. That would require a `ScheduledJob` model change + a handler registry in `SchedulerAdapter`, which is a larger change not warranted for a daily sync job.

---

## 5. Configuration

No new environment variables needed. Uses:
- Existing `CREDENTIAL_ENCRYPTION_KEY` for PAT decryption
- Existing `QDRANT_URL`
- Existing LiteLLM/embedder setup
- Existing `azure_devops_pat` credential type (no new credential type)

---

## 6. Implementation Verification Steps

These are verification tasks for the implementer to confirm the pipeline works end-to-end. They are NOT permanent features of the final product.

### Verification Step 1: Trial Retrieval (during development)

After implementing `core/wiki/service.py`, write a quick test script or pytest fixture that:
1. Calls `fetch_wiki_page_tree(pat, org_url, project)` with real credentials from `.env`
2. Prints the first 5 page paths and first 200 chars of each page's content
3. Confirms: pages are returned, content is non-empty markdown

### Verification Step 2: Trial Import (during development)

After implementing `full_import()` and the Qdrant integration:
1. Run `full_import()` against the real ADO wiki for 3 pages only (pass `force=False`)
2. Immediately call `get_rag_manager().retrieve("guidelines", top_k=3, collection_name="tibp-wiki")`
3. Confirm: results are returned, URIs match wiki page paths

### Verification Step 3: End-to-end (after deployment)

1. Run `alembic upgrade head`
2. Start dev: `./stack dev deploy`
3. Navigate to Admin Portal → Wiki Import
4. Select the context with ADO PAT
5. Click "Import" — observe status badge progress: fetching → embedding → completed
6. Open the agent chat and ask a question that should be answered from the wiki
7. Confirm the `tibp_researcher` skill returns relevant results

---

## 7. Security Considerations

1. **Credential handling**: PAT decrypted in-memory only, never logged. Uses existing CredentialService.
2. **SSRF**: ADO URL is constructed from admin-entered credential metadata, validated with regex (must match `https://dev.azure.com/...`).
3. **CSRF**: All POST admin endpoints use `require_csrf` dependency.
4. **Auth**: All admin endpoints require `verify_admin_user`.
5. **XSS**: Any page content displayed in the portal is escaped via `escapeHtml()` JS helper.
6. **Timeout**: httpx client 30s per ADO API call; scheduler job timeout 600s.
7. **Error messages**: API errors truncated to 200 chars before returning to client.

---

## 8. Files Summary

| File | Action |
|------|--------|
| `services/agent/src/core/db/models.py` | Modify — add `WikiImport` |
| `services/agent/alembic/versions/20260219_add_wiki_imports.py` | Create |
| `services/agent/src/core/wiki/__init__.py` | Create (empty) |
| `services/agent/src/core/wiki/service.py` | Create |
| `services/agent/src/core/tools/wiki_sync.py` | Create |
| `services/agent/config/tools.yaml` | Modify — add `wiki_sync` |
| `skills/system/wiki_sync.md` | Create |
| `services/agent/src/core/agents/executor.py` | Modify — inject `context_id`+`session` for `wiki_sync` |
| `services/agent/src/interfaces/http/admin_wiki.py` | Create |
| `services/agent/src/interfaces/http/admin_shared.py` | Modify — add NavItem |
| `services/agent/src/interfaces/http/app.py` | Modify — register router |
| `services/agent/src/core/tests/test_wiki_service.py` | Create |

---

## 9. Success Criteria

1. Admin portal shows "Wiki Import" page with status and import buttons
2. "Import" button triggers a background full import with live status updates
3. After import, `tibp_wiki_search` tool returns relevant results for wiki content queries
4. Scheduler job configured via admin UI triggers wiki sync on schedule
5. All quality checks pass (`./stack check`)
6. Unit tests cover page tree parsing, credential handling, and error cases
