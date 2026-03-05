"""Obsidian vault tool: per-context filesystem access with on-demand sync.

Sync mechanism
--------------
The ``ob`` CLI (from the ``obsidian-headless`` npm package) is invoked as a
subprocess inside the agent container (requires INCLUDE_VAULT=true at build time).
Each context has its own:
  - Vault directory:  /vault/<context_id>/
  - ob config home:   /vault/.ob-home-<context_id>/
  - Auth token:       stored encrypted in the database (CredentialService)

No shared environment variables are used. All config lives in the admin portal
(Admin Portal -> Context -> Obsidian Vault tab).

First-time vault link (per context, interactive, run once on any machine with Node 22)
  ob login                             # authenticate
  ob sync-list-remote                  # list available vaults
  ob sync-setup --local-path /vault/<context_id>/ --vault-name "YourVault"
Then copy the resulting ~/.config/obsidian-headless/auth_token into the admin portal.

Subsequent syncs are automated: ob sync /vault/<context_id>/
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from core.tools.base import Tool

LOGGER = logging.getLogger(__name__)

# Restrict agent writes to a dedicated subtree to protect user notes
_WRITE_PREFIX = "_ai-platform"
_MAX_FILE_SIZE = 1_000_000  # 1 MB per file

_OBSIDIAN_CREDENTIAL_TYPE = "obsidian_vault"
_SYNC_TTL = 300  # seconds — skip sync if last sync is fresher than this
_SYNC_TIMEOUT = 60  # seconds to wait for ob sync subprocess


class VaultTool(Tool):
    name = "vault"
    description = "Read and write notes in the user's Obsidian vault"
    category = "personal"

    # Class-level TTL cache: str(context_id) -> monotonic time of last successful sync
    _sync_cache: dict[str, float] = {}

    async def run(
        self,
        action: str,
        context_id: UUID | None = None,
        session: AsyncSession | None = None,
        path: str = "",
        query: str = "",
        content: str = "",
        recursive: bool = False,
        limit: int = 20,
    ) -> str:
        if context_id is None:
            return "vault tool requires context_id (not available outside a user session)"
        if session is None:
            return "vault tool requires database session"

        # All config from admin portal — no env vars
        result = await self._get_auth_token_and_vault(context_id, session)
        if result is None:
            return (
                "Obsidian Vault not configured for this context. "
                "Go to Admin Portal -> Context -> Obsidian Vault tab to add your auth token."
            )
        auth_token, vault_name = result

        vault_root = Path("/vault") / str(context_id)
        vault_root.mkdir(parents=True, exist_ok=True)

        # Sync before reads so the agent sees current content
        if action in ("search", "read", "list"):
            sync_msg = await self._sync(context_id, auth_token, vault_name, vault_root)
            if "unavailable" in sync_msg:
                LOGGER.warning("ob sync unavailable, using cached vault: %s", sync_msg)

        match action:
            case "search":
                return await self._search(vault_root, query, path, limit)
            case "read":
                return await self._read(vault_root, path)
            case "list":
                return await self._list(vault_root, path, recursive)
            case "write":
                write_result = await self._write(vault_root, path, content)
                # Push the new note upstream immediately after writing
                await self._sync(context_id, auth_token, vault_name, vault_root, force=True)
                return write_result
            case _:
                return f"Unknown action: {action}. Available: search, read, list, write"

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def _get_auth_token_and_vault(
        self, context_id: UUID, session: AsyncSession
    ) -> tuple[str, str] | None:
        """Return (auth_token, vault_name) from per-context CredentialService, or None."""
        from core.auth.credential_service import CredentialService
        from core.runtime.config import get_settings

        settings = get_settings()
        if not settings.credential_encryption_key:
            LOGGER.warning("AGENT_CREDENTIAL_ENCRYPTION_KEY not set; cannot read vault credentials")
            return None

        cred_service = CredentialService(settings.credential_encryption_key)
        cred = await cred_service.get_credential_with_metadata(
            context_id, _OBSIDIAN_CREDENTIAL_TYPE, session
        )
        if cred is None:
            return None

        auth_token, metadata = cred
        vault_name = metadata.get("vault_name", "")
        return auth_token, vault_name

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    async def _sync(
        self,
        context_id: UUID,
        auth_token: str,
        vault_name: str,
        vault_root: Path,
        force: bool = False,
    ) -> str:
        """Run ``ob sync`` for this context, with TTL-based caching.

        Each context gets its own HOME directory so that ob's config
        (which vault to sync, stored in ~/.config/obsidian-headless/) is
        fully isolated between users.

        Command used:
            ob sync <vault_root> [--vault-name <vault_name>]

        The --vault-name flag selects which remote vault to sync when the
        context home has not been set up yet. If ob doesn't support that
        flag in the installed version, drop it and rely on the config file
        written by ``ob sync-setup`` (which the user runs once per context
        during initial setup).
        """
        cache_key = str(context_id)
        now = time.monotonic()
        if not force and now - self._sync_cache.get(cache_key, 0) < _SYNC_TTL:
            return "cached (within TTL)"

        # Per-context HOME isolates ob config (~/.config/obsidian-headless/)
        context_home = Path("/vault") / f".ob-home-{context_id}"
        context_home.mkdir(parents=True, exist_ok=True)

        env = {**os.environ, "OBSIDIAN_AUTH_TOKEN": auth_token, "HOME": str(context_home)}

        cmd = ["ob", "sync", str(vault_root)]
        if vault_name:
            cmd += ["--vault-name", vault_name]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_SYNC_TIMEOUT)
            if proc.returncode == 0:
                self._sync_cache[cache_key] = now
                LOGGER.debug("ob sync succeeded for context %s", context_id)
                return "synced"
            err = stderr.decode(errors="replace")[:300]
            LOGGER.warning(
                "ob sync failed for context %s (rc=%d): %s", context_id, proc.returncode, err
            )
            return f"sync failed: {err}"
        except FileNotFoundError:
            return (
                "sync unavailable: ob CLI not found. "
                "Ensure the agent image is built with INCLUDE_VAULT=true."
            )
        except TimeoutError:
            return f"sync unavailable: timed out after {_SYNC_TIMEOUT}s"

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    async def _search(self, vault_root: Path, query: str, path_filter: str, limit: int) -> str:
        if not query:
            return "search requires a query parameter"

        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error as exc:
            return f"Invalid regex pattern: {exc}"

        search_root = vault_root / path_filter if path_filter else vault_root
        if not search_root.exists():
            return f"Path not found: {path_filter}"

        results: list[dict[str, str]] = []

        def _scan() -> None:
            for md_file in search_root.rglob("*.md"):
                if md_file.stat().st_size > _MAX_FILE_SIZE:
                    continue
                text = md_file.read_text(encoding="utf-8", errors="replace")
                m = pattern.search(text)
                if m:
                    start = max(0, m.start() - 80)
                    end = min(len(text), start + 200)
                    preview = text[start:end].replace("\n", " ").strip()
                    rel = str(md_file.relative_to(vault_root))
                    results.append({"path": rel, "preview": preview})
                    if len(results) >= limit:
                        return

        await asyncio.to_thread(_scan)

        if not results:
            return f"No notes matching '{query}'"

        lines = [f"Found {len(results)} note(s) matching '{query}':\n"]
        for r in results:
            lines.append(f"- {r['path']}\n  {r['preview']}")
        return "\n".join(lines)

    async def _read(self, vault_root: Path, path: str) -> str:
        if not path:
            return "read requires a path parameter"

        target = (vault_root / path).resolve()
        try:
            target.relative_to(vault_root.resolve())
        except ValueError:
            return "Access denied: path escapes vault root"

        if not target.exists():
            return f"Note not found: {path}"
        if target.stat().st_size > _MAX_FILE_SIZE:
            return f"Note too large to read (>{_MAX_FILE_SIZE // 1024}KB): {path}"

        return await asyncio.to_thread(target.read_text, encoding="utf-8", errors="replace")

    async def _list(self, vault_root: Path, path: str, recursive: bool) -> str:
        list_root = vault_root / path if path else vault_root
        if not list_root.exists():
            return f"Path not found: {path}"

        def _collect() -> list[str]:
            glob = list_root.rglob("*.md") if recursive else list_root.glob("*.md")
            return sorted(str(f.relative_to(vault_root)) for f in glob)

        files = await asyncio.to_thread(_collect)
        return "\n".join(files) if files else f"No markdown notes in: {path or '/'}"

    async def _write(self, vault_root: Path, path: str, content: str) -> str:
        if not path:
            return "write requires a path parameter"
        if not path.startswith(_WRITE_PREFIX):
            return (
                f"Write rejected: path must start with '{_WRITE_PREFIX}/' to keep agent notes "
                f"separate from user notes. Got: {path}"
            )

        target = (vault_root / path).resolve()
        try:
            target.relative_to(vault_root.resolve())
        except ValueError:
            return "Access denied: path escapes vault root"

        if not target.suffix:
            target = target.with_suffix(".md")

        def _write_atomic() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(target)

        await asyncio.to_thread(_write_atomic)
        return f"Written: {target.relative_to(vault_root)}"
