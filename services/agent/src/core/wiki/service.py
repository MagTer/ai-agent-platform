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
    """Represents a single wiki page with path and content."""

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

    Step 1: GET pages?path=/&recursionLevel=full  -> page paths
    Step 2: GET pages?path=P&includeContent=true  -> page content (per page)
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

        # Verify the rag manager supports mutable collection_name (duck typing — no modules import)
        if not hasattr(rag, "collection_name") or not hasattr(rag, "client"):
            raise WikiImportError(
                "RAG manager does not support collection_name override. Cannot ingest wiki pages."
            )

        if force:
            LOGGER.info("Force mode: recreating collection %s", COLLECTION_NAME)
            from qdrant_client.http import models as qm

            qdrant_client = rag.client
            try:
                existing = await qdrant_client.get_collections()
                collection_names = [c.name for c in existing.collections]
                if COLLECTION_NAME in collection_names:
                    await qdrant_client.delete_collection(COLLECTION_NAME)
            except Exception as e:
                LOGGER.warning("Could not delete collection: %s", e)
            await qdrant_client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=qm.VectorParams(size=4096, distance=qm.Distance.COSINE),
                hnsw_config=qm.HnswConfigDiff(m=32, ef_construct=256),
            )

        wiki_record.status = "embedding"
        await session.commit()

        # Temporarily override collection for wiki ingestion (duck typing on RAGManager)
        original_collection: str = rag.collection_name
        rag.collection_name = COLLECTION_NAME
        try:
            total_chunks = 0
            for i, page in enumerate(pages):
                chunks = await rag.ingest_document(
                    page.content,
                    {
                        "uri": page.path,
                        "source": "tibp_wiki",
                        "type": "documentation",
                        "context_id": str(context_id),
                    },
                    chunk_size=2000,
                    chunk_overlap=300,
                )
                total_chunks += chunks
                wiki_record.pages_imported = i + 1
                wiki_record.total_chunks = total_chunks
                if (i + 1) % 10 == 0:
                    await session.commit()
        finally:
            rag.collection_name = original_collection

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
