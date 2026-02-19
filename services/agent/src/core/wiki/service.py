"""Service for importing Azure DevOps wiki pages into Qdrant."""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, unquote
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
GIT_CLONE_TIMEOUT = 300.0  # 5 minutes for clone
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


async def _get_wiki_clone_url(
    pat: str,
    org_url: str,
    project: str,
) -> tuple[str, str]:
    """Discover the wiki identifier and authenticated git clone URL.

    Calls the wikis list API to get the projectWiki's remoteUrl, then
    injects the PAT as the HTTP password for git clone auth.

    Returns (wiki_identifier, authenticated_clone_url).
    """
    auth = httpx.BasicAuth(username="", password=pat)
    list_url = f"{org_url}/{project}/_apis/wiki/wikis"
    wiki_name = f"{project.replace(' ', '-')}.wiki"
    remote_url = ""

    try:
        async with httpx.AsyncClient(timeout=ADO_REQUEST_TIMEOUT, auth=auth) as client:
            resp = await client.get(list_url, params={"api-version": ADO_API_VERSION})
            resp.raise_for_status()
            wikis = resp.json().get("value", [])
            # Prefer projectWiki type; fall back to first entry
            chosen = next((w for w in wikis if w.get("type") == "projectWiki"), None)
            if chosen is None and wikis:
                chosen = wikis[0]
            if chosen:
                discovered_name = chosen.get("name") or chosen.get("id") or wiki_name
                wiki_name = str(discovered_name).replace(" ", "-")
                remote_url = str(chosen.get("remoteUrl", ""))
                LOGGER.info("Discovered wiki: name=%s remote=%s", wiki_name, remote_url)
    except Exception as e:
        LOGGER.warning("Wiki discovery failed, using name-based fallback: %s", e)

    # Build the git clone URL from components.
    # The remoteUrl from the wikis API points to the web viewer (/_wiki/wikis/...),
    # not the git endpoint. The correct git clone URL is /_git/{wiki-name}.
    pat_encoded = quote(pat, safe="")
    org_part = org_url.replace("https://", "")
    proj_encoded = quote(project)
    clone_url = f"https://x-token:{pat_encoded}@{org_part}/{proj_encoded}/_git/{wiki_name}"

    return wiki_name, clone_url


async def clone_wiki_pages(
    pat: str,
    org_url: str,
    project: str,
) -> tuple[str, list[WikiPage]]:
    """Clone the ADO wiki git repo and return all markdown pages.

    Uses a shallow clone (--depth 1) for speed. The PAT is injected
    into the HTTPS clone URL as the HTTP password.

    Returns (wiki_identifier, pages).
    """
    wiki_name, clone_url = await _get_wiki_clone_url(pat, org_url, project)

    with tempfile.TemporaryDirectory() as tmpdir:
        LOGGER.info("Cloning wiki repo %s (shallow)...", wiki_name)
        proc = await asyncio.create_subprocess_exec(
            "git",
            "clone",
            "--depth",
            "1",
            "--quiet",
            clone_url,
            tmpdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=GIT_CLONE_TIMEOUT)

        if proc.returncode != 0:
            err_msg = stderr_bytes.decode(errors="replace")
            # Mask PAT in error output
            err_msg = err_msg.replace(pat, "***")
            raise WikiImportError(f"Git clone failed (exit {proc.returncode}): {err_msg}")

        # Walk cloned repo and collect all .md files (skip .git dir)
        pages: list[WikiPage] = []
        md_files = sorted(f for f in Path(tmpdir).rglob("*.md") if ".git" not in f.parts)
        for i, md_file in enumerate(md_files):
            try:
                content = md_file.read_text(encoding="utf-8", errors="replace")
                if content.strip():
                    rel_path = "/" + md_file.relative_to(tmpdir).as_posix()
                    pages.append(WikiPage(path=rel_path, content=content, order=i))
            except Exception as e:
                LOGGER.warning("Failed to read wiki file %s: %s", md_file, e)

        LOGGER.info("Read %d markdown pages from cloned wiki %s", len(pages), wiki_name)
        return wiki_name, pages


async def full_import(
    context_id: UUID,
    session: AsyncSession,
    wiki_identifier: str | None = None,
    force: bool = False,
) -> str:
    """Clone the ADO wiki git repo, embed pages, and index into Qdrant.

    Designed to run as a background task. Updates WikiImport record with
    progress as it proceeds. Returns a summary string on completion.

    Args:
        context_id: Context with ADO credentials.
        session: Database session.
        wiki_identifier: Unused override (kept for API compatibility).
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

    # Use hyphenated fallback for the DB record key; clone_wiki_pages will
    # discover and return the real identifier.
    db_wiki_id = wiki_identifier or f"{project.replace(' ', '-')}.wiki"

    # Get or create WikiImport record
    stmt = select(WikiImport).where(
        WikiImport.context_id == context_id,
        WikiImport.wiki_identifier == db_wiki_id,
    )
    wiki_record = (await session.execute(stmt)).scalar_one_or_none()

    if not wiki_record:
        wiki_record = WikiImport(
            context_id=context_id,
            wiki_identifier=db_wiki_id,
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
        discovered_id, pages = await clone_wiki_pages(pat, org_url, project)

        # Update DB record with discovered identifier if it differs
        if discovered_id != db_wiki_id:
            wiki_record.wiki_identifier = discovered_id

        wiki_record.total_pages = len(pages)
        await session.commit()

        if not pages:
            wiki_record.status = "completed"
            wiki_record.last_import_completed_at = datetime.now(UTC).replace(tzinfo=None)
            await session.commit()
            return "No wiki pages found."

        rag = get_rag_manager()

        # Verify the rag manager supports mutable collection_name (duck typing)
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
