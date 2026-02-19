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
        """Run the wiki sync.

        Args:
            action: Action to perform. Only 'sync' is supported.
            context_id: Context ID with ADO credentials (injected by executor).
            session: Database session (injected by executor).
            force: If True, delete and recreate the collection before importing.

        Returns:
            Summary string on success, error message on failure.
        """
        if action != "sync":
            return f"Unknown action: {action}. Only 'sync' is supported."

        if not context_id or not session:
            return "wiki_sync requires context_id and session (injected by executor)."

        try:
            summary = await full_import(context_id, session, force=force)
            return f"Wiki sync complete. {summary}"
        except WikiImportError as e:
            return f"Wiki sync failed: {e}"
