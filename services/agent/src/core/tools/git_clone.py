"""Tool to clone git repositories to a context-isolated workspace."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import Workspace
from core.tools.base import Tool

LOGGER = logging.getLogger(__name__)

# Default workspace base directory (can be overridden via env)
DEFAULT_WORKSPACE_BASE = Path(
    os.environ.get("AGENT_WORKSPACE_BASE", "/tmp/agent-workspaces")  # noqa: S108
)


class GitCloneTool(Tool):
    """Clone git repositories to context-isolated workspaces.

    Each context has its own workspace directory to prevent conflicts.
    Workspaces are tracked in the database for management.
    """

    name = "git_clone"
    description = (
        "Clone a git repository to a workspace for investigation. "
        "Supports GitHub and Azure Repos URLs. Returns the local path to the cloned repo."
    )
    category = "development"
    parameters = {
        "type": "object",
        "properties": {
            "repo_url": {
                "type": "string",
                "description": (
                    "Git repository URL (HTTPS). "
                    "Examples: https://github.com/org/repo.git, "
                    "https://dev.azure.com/org/project/_git/repo"
                ),
            },
            "branch": {
                "type": "string",
                "description": "Branch to checkout (default: default branch)",
            },
            "workspace_name": {
                "type": "string",
                "description": ("Name for the workspace. If not provided, derived from repo name."),
            },
        },
        "required": ["repo_url"],
    }

    def __init__(self, workspace_base: Path | None = None) -> None:
        """Initialize git clone tool.

        Args:
            workspace_base: Base directory for workspaces (default: /tmp/agent-workspaces).
        """
        self.workspace_base = workspace_base or DEFAULT_WORKSPACE_BASE
        self.workspace_base.mkdir(parents=True, exist_ok=True)

    def _validate_repo_url(self, url: str) -> None:
        """Validate repository URL for security issues.

        Rejects URLs with:
        - Empty URLs
        - Embedded credentials (username/password)
        - Non-HTTPS/SSH protocols
        - Invalid characters (newlines, null bytes, shell metacharacters)

        Args:
            url: Repository URL to validate.

        Raises:
            ValueError: If URL is invalid or contains security issues.
        """
        # Check for empty URL first
        if not url.strip():
            raise ValueError("Repository URL cannot be empty")

        # Parse URL
        parsed = urlparse(url)

        # Check for embedded credentials (but allow 'git' user for SSH)
        if parsed.password:
            raise ValueError("Repository URLs must not contain embedded credentials")
        if parsed.username and parsed.username != "git":
            raise ValueError("Repository URLs must not contain embedded credentials")

        # Check protocol - support HTTPS, SSH (git@), and ssh:// scheme
        if url.startswith("git@"):
            # SSH URL format: git@github.com:org/repo.git
            # This is valid, skip scheme check
            pass
        elif parsed.scheme not in ("https", "ssh"):
            msg = f"Unsupported protocol: {parsed.scheme}. Only HTTPS and SSH are allowed."
            raise ValueError(msg)

        # Check for invalid characters that could enable command injection
        dangerous_chars = ["\n", "\r", "\0", ";", "&", "|", "`", "$", "(", ")"]
        for char in dangerous_chars:
            if char in url:
                raise ValueError(f"Repository URL contains invalid character: {repr(char)}")

    async def run(
        self,
        repo_url: str,
        branch: str | None = None,
        workspace_name: str | None = None,
        context_id: UUID | None = None,
        session: AsyncSession | None = None,
    ) -> str:
        """Clone a git repository to a context-isolated workspace.

        Args:
            repo_url: The HTTPS URL of the git repository.
            branch: Optional branch to checkout.
            workspace_name: Optional name for the workspace.
            context_id: Context UUID for isolation (injected by executor).
            session: Database session (injected by executor).

        Returns:
            The local path to the cloned repository, or an error message.
        """
        # Validate URL for security issues
        try:
            self._validate_repo_url(repo_url)
        except ValueError as e:
            return f"Error: Invalid repository URL: {e}"

        # Derive workspace name from URL if not provided
        if not workspace_name:
            workspace_name = self._derive_workspace_name(repo_url)

        # Determine workspace path (context-isolated if context_id provided)
        if context_id:
            context_dir = self.workspace_base / str(context_id)
            context_dir.mkdir(parents=True, exist_ok=True)
            workspace_path = context_dir / workspace_name
        else:
            # Fallback for calls without context (shouldn't happen in normal flow)
            LOGGER.warning("git_clone called without context_id - using shared workspace")
            workspace_path = self.workspace_base / "shared" / workspace_name

        workspace_path.parent.mkdir(parents=True, exist_ok=True)

        # Check if workspace exists in database
        existing_workspace = None
        if session and context_id:
            existing_workspace = await self._get_workspace(session, context_id, workspace_name)

        # If workspace exists on disk, sync it
        if workspace_path.exists() and (workspace_path / ".git").exists():
            LOGGER.info("Workspace exists, syncing: %s", workspace_path)
            result = await self._git_pull(workspace_path, branch)

            # Update database record
            if session and context_id:
                await self._update_workspace_status(
                    session,
                    existing_workspace,
                    context_id,
                    workspace_name,
                    repo_url,
                    branch or "main",
                    str(workspace_path),
                    "cloned",
                )

            return result

        # Clone the repository
        LOGGER.info("Cloning %s to %s", repo_url, workspace_path)
        result = await self._git_clone(repo_url, workspace_path, branch)

        # Track in database
        if session and context_id and not result.startswith("Error"):
            await self._create_or_update_workspace(
                session,
                context_id,
                workspace_name,
                repo_url,
                branch or "main",
                str(workspace_path),
                "cloned" if not result.startswith("Error") else "error",
                result if result.startswith("Error") else None,
            )

        return result

    def _derive_workspace_name(self, repo_url: str) -> str:
        """Extract workspace name from repository URL.

        Examples:
            https://github.com/org/repo.git -> repo
            https://dev.azure.com/org/project/_git/repo -> repo

        Args:
            repo_url: Git repository URL.

        Returns:
            Derived workspace name.
        """
        # https://github.com/org/repo.git -> repo
        # https://dev.azure.com/org/project/_git/repo -> repo
        name = repo_url.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        return name

    async def _get_workspace(
        self,
        session: AsyncSession,
        context_id: UUID,
        workspace_name: str,
    ) -> Workspace | None:
        """Get existing workspace from database."""
        stmt = select(Workspace).where(
            Workspace.context_id == context_id,
            Workspace.name == workspace_name,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def _create_or_update_workspace(
        self,
        session: AsyncSession,
        context_id: UUID,
        name: str,
        repo_url: str,
        branch: str,
        local_path: str,
        status: str,
        error: str | None = None,
    ) -> Workspace:
        """Create or update workspace record in database."""
        # Check for existing by repo_url (might have different name)
        stmt = select(Workspace).where(
            Workspace.context_id == context_id,
            Workspace.repo_url == repo_url,
        )
        result = await session.execute(stmt)
        workspace = result.scalar_one_or_none()

        now = datetime.now(UTC).replace(tzinfo=None)

        if workspace:
            workspace.name = name
            workspace.branch = branch
            workspace.local_path = local_path
            workspace.status = status
            workspace.last_synced_at = now if status == "cloned" else None
            workspace.sync_error = error
        else:
            workspace = Workspace(
                context_id=context_id,
                name=name,
                repo_url=repo_url,
                branch=branch,
                local_path=local_path,
                status=status,
                last_synced_at=now if status == "cloned" else None,
                sync_error=error,
            )
            session.add(workspace)

        await session.commit()
        return workspace

    async def _update_workspace_status(
        self,
        session: AsyncSession,
        workspace: Workspace | None,
        context_id: UUID,
        name: str,
        repo_url: str,
        branch: str,
        local_path: str,
        status: str,
    ) -> None:
        """Update workspace status after sync."""
        if workspace:
            workspace.status = status
            workspace.last_synced_at = datetime.now(UTC).replace(tzinfo=None)
            workspace.sync_error = None
            await session.commit()
        else:
            await self._create_or_update_workspace(
                session, context_id, name, repo_url, branch, local_path, status
            )

    async def _git_clone(
        self,
        repo_url: str,
        workspace_path: Path,
        branch: str | None,
    ) -> str:
        """Execute git clone with shallow depth for performance.

        Args:
            repo_url: Git repository URL.
            workspace_path: Local path for cloning.
            branch: Optional branch to checkout.

        Returns:
            Success message or error.
        """
        cmd = ["git", "clone", "--depth", "100"]
        if branch:
            cmd.extend(["--branch", branch])
        cmd.extend([repo_url, str(workspace_path)])

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=300  # 5 min timeout
            )

            if process.returncode != 0:
                error_msg = stderr.decode().strip()
                LOGGER.error("Git clone failed: %s", error_msg)
                return f"Error: Git clone failed: {error_msg}"

            return f"Repository cloned to: {workspace_path}"

        except TimeoutError:
            return "Error: Git clone timed out after 5 minutes."
        except Exception as e:
            LOGGER.exception("Git clone failed")
            return f"Error: Failed to clone repository: {e}"

    async def _git_pull(self, workspace_path: Path, branch: str | None) -> str:
        """Pull latest changes with automatic reset on divergence.

        Args:
            workspace_path: Path to git repository.
            branch: Optional branch to checkout first.

        Returns:
            Success message or warning.
        """
        try:
            # Checkout branch if specified
            if branch:
                checkout_cmd = ["git", "checkout", branch]
                process = await asyncio.create_subprocess_exec(
                    *checkout_cmd,
                    cwd=workspace_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await process.communicate()

            # Pull latest
            pull_cmd = ["git", "pull", "--ff-only"]
            process = await asyncio.create_subprocess_exec(
                *pull_cmd,
                cwd=workspace_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)

            if process.returncode != 0:
                # Pull failed - try reset if history diverged
                reset_cmd = ["git", "reset", "--hard", "origin/HEAD"]
                await asyncio.create_subprocess_exec(
                    *reset_cmd,
                    cwd=workspace_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                return f"Repository updated (reset): {workspace_path}"

            return f"Repository updated: {workspace_path}"

        except TimeoutError:
            return f"Warning: Pull timed out, using existing: {workspace_path}"
        except Exception as e:
            return f"Warning: Pull failed ({e}), using existing: {workspace_path}"
