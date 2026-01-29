"""Tool to create GitHub pull requests using gh CLI."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from core.tools.base import Tool

LOGGER = logging.getLogger(__name__)


class GitHubPRTool(Tool):
    """Creates pull requests on GitHub using the gh CLI."""

    name = "github_pr"
    description = (
        "Create a pull request on GitHub. Requires the repository to have "
        "committed changes on a feature branch. Uses the gh CLI."
    )
    category = "development"
    parameters = {
        "type": "object",
        "properties": {
            "repo_path": {
                "type": "string",
                "description": "Local path to the git repository.",
            },
            "title": {
                "type": "string",
                "description": "PR title (e.g., 'fix: Resolve null pointer in auth module').",
            },
            "body": {
                "type": "string",
                "description": (
                    "PR description. Should include: summary of changes, "
                    "root cause (for bugs), testing done."
                ),
            },
            "branch_name": {
                "type": "string",
                "description": (
                    "Name for the feature branch. If not on a feature branch, "
                    "one will be created with this name."
                ),
            },
            "base_branch": {
                "type": "string",
                "description": "Target branch for the PR (default: main).",
            },
            "draft": {
                "type": "boolean",
                "description": "Create as draft PR (default: false).",
            },
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Labels to add to the PR (e.g., ['bug', 'automated']).",
            },
        },
        "required": ["repo_path", "title", "body"],
    }

    async def run(
        self,
        repo_path: str,
        title: str,
        body: str,
        branch_name: str | None = None,
        base_branch: str = "main",
        draft: bool = False,
        labels: list[str] | None = None,
    ) -> str:
        """
        Create a GitHub pull request.

        Args:
            repo_path: Path to the local repository.
            title: PR title.
            body: PR description.
            branch_name: Feature branch name (creates if needed).
            base_branch: Target branch (default: main).
            draft: Create as draft PR.
            labels: Labels to add.

        Returns:
            PR URL on success, or error message.
        """
        repo_dir = Path(repo_path)
        if not repo_dir.exists():
            return f"Error: Repository path does not exist: {repo_path}"

        # Check for uncommitted changes
        has_changes = await self._has_uncommitted_changes(repo_dir)

        # Get current branch
        current_branch = await self._get_current_branch(repo_dir)

        # If on main/master and have changes, need to create a branch
        if current_branch in ("main", "master"):
            if not branch_name:
                return (
                    "Error: On main branch. Provide branch_name to create "
                    "a feature branch for the PR."
                )

            # Create and checkout new branch
            result = await self._create_branch(repo_dir, branch_name)
            if result.startswith("Error"):
                return result

            current_branch = branch_name

        # Commit any uncommitted changes
        if has_changes:
            commit_result = await self._commit_changes(repo_dir, title)
            if commit_result.startswith("Error"):
                return commit_result

        # Push the branch
        push_result = await self._push_branch(repo_dir, current_branch)
        if push_result.startswith("Error"):
            return push_result

        # Create the PR
        return await self._create_pr(repo_dir, title, body, base_branch, draft, labels)

    async def _has_uncommitted_changes(self, repo_dir: Path) -> bool:
        """Check if there are uncommitted changes."""
        process = await asyncio.create_subprocess_exec(
            "git",
            "status",
            "--porcelain",
            cwd=repo_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        return bool(stdout.decode().strip())

    async def _get_current_branch(self, repo_dir: Path) -> str:
        """Get the current git branch name."""
        process = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
            cwd=repo_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        return stdout.decode().strip()

    async def _create_branch(self, repo_dir: Path, branch_name: str) -> str:
        """Create and checkout a new branch."""
        process = await asyncio.create_subprocess_exec(
            "git",
            "checkout",
            "-b",
            branch_name,
            cwd=repo_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            return f"Error: Failed to create branch: {stderr.decode()}"
        return f"Created branch: {branch_name}"

    async def _commit_changes(self, repo_dir: Path, title: str) -> str:
        """Stage and commit all changes."""
        # Stage all changes
        process = await asyncio.create_subprocess_exec(
            "git",
            "add",
            "-A",
            cwd=repo_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate()

        # Commit
        commit_msg = f"{title}\n\nAutomated fix by AI Agent Platform"
        process = await asyncio.create_subprocess_exec(
            "git",
            "commit",
            "-m",
            commit_msg,
            cwd=repo_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            return f"Error: Failed to commit: {stderr.decode()}"
        return "Changes committed"

    async def _push_branch(self, repo_dir: Path, branch_name: str) -> str:
        """Push the branch to origin."""
        process = await asyncio.create_subprocess_exec(
            "git",
            "push",
            "-u",
            "origin",
            branch_name,
            cwd=repo_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            error = stderr.decode()
            # Ignore "already up to date" type messages
            if "Everything up-to-date" in error or process.returncode == 0:
                return "Branch pushed"
            return f"Error: Failed to push: {error}"
        return "Branch pushed"

    async def _create_pr(
        self,
        repo_dir: Path,
        title: str,
        body: str,
        base_branch: str,
        draft: bool,
        labels: list[str] | None,
    ) -> str:
        """Create the pull request using gh CLI."""
        cmd = [
            "gh",
            "pr",
            "create",
            "--title",
            title,
            "--body",
            body,
            "--base",
            base_branch,
        ]

        if draft:
            cmd.append("--draft")

        if labels:
            for label in labels:
                cmd.extend(["--label", label])

        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=repo_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)

        if process.returncode != 0:
            error = stderr.decode()
            # Check if PR already exists
            if "already exists" in error.lower():
                return "Note: A pull request for this branch already exists."
            return f"Error: Failed to create PR: {error}"

        pr_url = stdout.decode().strip()
        LOGGER.info("Created PR: %s", pr_url)
        return f"Pull request created: {pr_url}"
