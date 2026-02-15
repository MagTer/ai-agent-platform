"""Context injection module - handles file/workspace injection with security validation."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from shared.models import AgentMessage

from core.context.files import CONTEXT_DATA_BASE

LOGGER = logging.getLogger(__name__)


class ContextInjector:
    """Handles injection of files and workspace context into conversation history."""

    def _is_path_safe(self, file_path: str, allowed_bases: list[str]) -> bool:
        """Check if a file path is safe to read (no path traversal).

        SECURITY: Prevents reading sensitive files outside allowed directories.

        Args:
            file_path: The file path to validate
            allowed_bases: List of allowed base directories

        Returns:
            True if path is within an allowed directory, False otherwise
        """
        try:
            resolved = Path(file_path).resolve()
            for base in allowed_bases:
                base_resolved = Path(base).resolve()
                # Check if the resolved path starts with the allowed base
                if str(resolved).startswith(str(base_resolved) + "/"):
                    return True
                if resolved == base_resolved:
                    return True
            return False
        except Exception:
            LOGGER.debug(
                "Path resolution failed during sandbox check for %s (allowed: %s), "
                "assuming not sandboxed",
                file_path,
                allowed_bases,
                exc_info=True,
            )
            return False

    async def _inject_pinned_files(
        self,
        history: list[AgentMessage],
        pinned_files: list[str] | None,
        workspace_path: str | None = None,
    ) -> None:
        """Inject pinned file contents into the conversation history.

        Args:
            history: The conversation history to modify in-place
            pinned_files: List of file paths to inject
            workspace_path: Optional workspace path for path validation
        """
        if not pinned_files:
            return

        # SECURITY: Define allowed base directories for pinned files
        allowed_bases: list[str] = []
        if workspace_path:
            allowed_bases.append(workspace_path)
        # Also allow user's home directory as a reasonable default
        home = Path.home()
        if await asyncio.to_thread(home.exists):
            allowed_bases.append(str(home))
        # Allow context data directory for per-context pinned files
        allowed_bases.append(str(CONTEXT_DATA_BASE.resolve()))

        async def _read_pinned_file(pf: str) -> str | None:
            """Read a single pinned file asynchronously."""
            try:
                # SECURITY: Validate path is within allowed directories
                if allowed_bases and not self._is_path_safe(pf, allowed_bases):
                    LOGGER.warning(f"Blocked pinned file outside allowed paths: {pf}")
                    return None

                p = Path(pf)
                if await asyncio.to_thread(p.exists) and await asyncio.to_thread(p.is_file):
                    file_content = await asyncio.to_thread(p.read_text, encoding="utf-8")
                    return f"### FILE: {pf}\n{file_content}"
                return None
            except Exception as e:
                LOGGER.warning(f"Failed to read pinned file {pf}: {e}")
                return None

        # Read all pinned files in parallel
        results = await asyncio.gather(
            *[_read_pinned_file(pf) for pf in pinned_files],
            return_exceptions=True,
        )

        pinned_content: list[str] = []
        for result in results:
            if isinstance(result, BaseException):
                LOGGER.warning(f"Failed to read pinned file: {result}")
            elif result is not None:
                pinned_content.append(result)

        if pinned_content:
            combined_pinned = "\n\n".join(pinned_content)
            history.append(
                AgentMessage(
                    role="system",
                    content=(
                        f"## PINNED FILES (Active Context)\n"
                        f"The following files are pinned to your context:\n\n{combined_pinned}"
                    ),
                )
            )

    async def _inject_workspace_rules(
        self,
        history: list[AgentMessage],
        workspace_path: str,
    ) -> None:
        """Inject workspace rules from .agent/rules.md into the conversation history.

        Args:
            history: The conversation history to modify in-place
            workspace_path: Path to the workspace directory
        """
        # SECURITY: Validate workspace_path and ensure rules file stays within it
        try:
            workspace_resolved = Path(workspace_path).resolve()
        except Exception:
            LOGGER.warning("Invalid workspace path: %s", workspace_path, exc_info=True)
            return

        rules_path = Path(workspace_path) / ".agent" / "rules.md"

        # SECURITY: Ensure resolved rules_path is within workspace
        try:
            rules_resolved = rules_path.resolve()
            if not str(rules_resolved).startswith(str(workspace_resolved) + "/"):
                LOGGER.warning(f"Blocked rules path traversal: {rules_path}")
                return
        except Exception:
            LOGGER.warning("Failed to validate rules path: %s", rules_path, exc_info=True)
            return

        if not await asyncio.to_thread(rules_path.exists) or not await asyncio.to_thread(
            rules_path.is_file
        ):
            return

        try:
            rules_content = await asyncio.to_thread(rules_path.read_text, encoding="utf-8")
            rules_content = rules_content.strip()
            if not rules_content:
                return

            # Insert at the beginning of history as a system message
            history.insert(
                0,
                AgentMessage(
                    role="system",
                    content=(
                        f"## WORKSPACE RULES\n"
                        f"These rules apply to this workspace and must be followed:\n\n"
                        f"{rules_content}"
                    ),
                ),
            )
            LOGGER.info(f"Injected workspace rules from {rules_path}")
        except Exception as e:
            LOGGER.warning(f"Failed to read workspace rules from {rules_path}: {e}")
