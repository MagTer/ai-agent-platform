"""Obsidian vault tool for reading and writing notes via filesystem."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from core.tools.base import Tool

LOGGER = logging.getLogger(__name__)

# Restrict agent writes to a dedicated subtree to protect user notes
_WRITE_PREFIX = "_ai-platform"
_MAX_FILE_SIZE = 1_000_000  # 1MB per file


class VaultTool(Tool):
    name = "vault"
    description = "Read and write notes in the user's Obsidian vault"
    category = "personal"

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

        vault_root = Path("/vault") / str(context_id)
        if not vault_root.exists():
            return (
                "Vault not synced for this context. "
                "Configure Obsidian Vault in Admin Portal -> Context -> Obsidian Vault tab."
            )

        match action:
            case "search":
                return await self._search(vault_root, query, path, limit)
            case "read":
                return await self._read(vault_root, path)
            case "list":
                return await self._list(vault_root, path, recursive)
            case "write":
                return await self._write(vault_root, path, content)
            case _:
                return f"Unknown action: {action}. Available actions: search, read, list, write"

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
                if pattern.search(text):
                    rel = md_file.relative_to(vault_root)
                    # Extract a short preview around the first match
                    m = pattern.search(text)
                    start = max(0, m.start() - 80) if m else 0
                    end = min(len(text), start + 200)
                    preview = text[start:end].replace("\n", " ").strip()
                    results.append({"path": str(rel), "preview": preview})
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
        # Security: prevent path traversal outside vault
        try:
            target.relative_to(vault_root.resolve())
        except ValueError:
            return "Access denied: path escapes vault root"

        if not target.exists():
            return f"Note not found: {path}"

        if target.stat().st_size > _MAX_FILE_SIZE:
            return f"Note too large to read (>{_MAX_FILE_SIZE // 1024}KB): {path}"

        text = await asyncio.to_thread(target.read_text, encoding="utf-8", errors="replace")
        return text

    async def _list(self, vault_root: Path, path: str, recursive: bool) -> str:
        list_root = vault_root / path if path else vault_root
        if not list_root.exists():
            return f"Path not found: {path}"

        def _collect() -> list[str]:
            if recursive:
                return [str(f.relative_to(vault_root)) for f in list_root.rglob("*.md")]
            else:
                return [str(f.relative_to(vault_root)) for f in list_root.glob("*.md")]

        files = await asyncio.to_thread(_collect)
        files.sort()

        if not files:
            return f"No markdown notes in: {path or '/'}"

        return "\n".join(files)

    async def _write(self, vault_root: Path, path: str, content: str) -> str:
        if not path:
            return "write requires a path parameter"

        if not path.startswith(_WRITE_PREFIX):
            return (
                f"Write rejected: path must start with '{_WRITE_PREFIX}/' to keep agent notes "
                f"separate from user notes. Got: {path}"
            )

        target = (vault_root / path).resolve()
        # Security: prevent path traversal outside vault
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
        rel = target.relative_to(vault_root)
        return f"Written: {rel}"
