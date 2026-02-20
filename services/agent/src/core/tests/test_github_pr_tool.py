"""Tests for GitHubPRTool: branch sanitization, commit visibility, PR flow, errors."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.tools.github_pr import GitHubPRTool


@pytest.fixture
def tool() -> GitHubPRTool:
    """Return a GitHubPRTool instance."""
    return GitHubPRTool()


def make_process(returncode: int, stdout: bytes, stderr: bytes = b"") -> MagicMock:
    """Build a fake asyncio subprocess process mock."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


# ---------------------------------------------------------------------------
# Branch name sanitization (bug fix #3 behavior)
# ---------------------------------------------------------------------------


class TestBranchNameSanitization:
    """_sanitize_branch_name produces git-safe branch names."""

    def test_spaces_replaced_with_hyphens(self, tool: GitHubPRTool) -> None:
        """Spaces in branch names are replaced with hyphens."""
        assert tool._sanitize_branch_name("my feature branch") == "my-feature-branch"

    def test_slash_is_valid_in_branches(self, tool: GitHubPRTool) -> None:
        """Forward slashes are valid git branch separators and must be kept."""
        assert tool._sanitize_branch_name("feat/add-thing") == "feat/add-thing"

    def test_leading_hyphen_stripped(self, tool: GitHubPRTool) -> None:
        """Branch names starting with '-' are stripped of leading hyphens."""
        assert tool._sanitize_branch_name("-leading-hyphen") == "leading-hyphen"

    def test_multiple_leading_hyphens_stripped(self, tool: GitHubPRTool) -> None:
        """Multiple leading hyphens are all stripped."""
        assert tool._sanitize_branch_name("---my-branch") == "my-branch"

    def test_long_name_truncated_to_100_chars(self, tool: GitHubPRTool) -> None:
        """Names longer than 100 characters are truncated."""
        long_name = "a" * 150
        result = tool._sanitize_branch_name(long_name)
        assert len(result) <= 100

    def test_tilde_replaced(self, tool: GitHubPRTool) -> None:
        """Tilde is an invalid git branch char and must be replaced."""
        result = tool._sanitize_branch_name("branch~1")
        assert "~" not in result
        assert "-" in result

    def test_caret_replaced(self, tool: GitHubPRTool) -> None:
        """Caret is an invalid git branch char and must be replaced."""
        result = tool._sanitize_branch_name("branch^2")
        assert "^" not in result

    def test_colon_replaced(self, tool: GitHubPRTool) -> None:
        """Colon is an invalid git branch char and must be replaced."""
        result = tool._sanitize_branch_name("branch:tag")
        assert ":" not in result

    def test_double_dot_replaced(self, tool: GitHubPRTool) -> None:
        """Double dot is invalid in git branch names and must be replaced."""
        result = tool._sanitize_branch_name("branch..main")
        assert ".." not in result

    def test_clean_name_unchanged(self, tool: GitHubPRTool) -> None:
        """A well-formed branch name passes through unchanged."""
        name = "feat/fix-auth-bug-123"
        assert tool._sanitize_branch_name(name) == name

    @pytest.mark.asyncio
    async def test_sanitization_applied_in_run_for_branch_name(
        self, tool: GitHubPRTool, tmp_path: Path
    ) -> None:
        """The sanitized branch_name is used when creating a branch inside run()."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        created_names: list[str] = []

        # patch.object on the class passes 'self' as the first arg to the replacement
        async def mock_create_branch(self_: object, d: Path, name: str) -> str:
            created_names.append(name)
            return f"Created branch: {name}"

        with (
            patch.object(
                GitHubPRTool, "_has_uncommitted_changes", new=AsyncMock(return_value=False)
            ),
            patch.object(GitHubPRTool, "_get_current_branch", new=AsyncMock(return_value="main")),
            patch.object(GitHubPRTool, "_create_branch", new=mock_create_branch),
            patch.object(GitHubPRTool, "_push_branch", new=AsyncMock(return_value="Branch pushed")),
            patch.object(
                GitHubPRTool,
                "_create_pr",
                new=AsyncMock(
                    return_value="Pull request created: https://github.com/org/repo/pull/1"
                ),
            ),
        ):
            await tool.run(
                repo_path=str(repo_dir),
                title="My PR",
                body="Body text",
                branch_name="my feature branch",  # spaces should become hyphens
            )

        assert created_names == ["my-feature-branch"]


# ---------------------------------------------------------------------------
# Commit visibility (bug fix #4 behavior)
# ---------------------------------------------------------------------------


class TestCommitVisibility:
    """_commit_changes includes staged file list in return message."""

    @pytest.mark.asyncio
    async def test_commit_result_lists_staged_files(
        self, tool: GitHubPRTool, tmp_path: Path
    ) -> None:
        """Return message must include the files that were staged."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        status_output = b"M  src/auth.py\nA  src/new_feature.py\nD  src/old.py\n"

        processes = [
            make_process(0, status_output),  # git status --porcelain
            make_process(0, b""),  # git add -A
            make_process(0, b"[main abc123] My PR\n"),  # git commit
        ]

        with patch("asyncio.create_subprocess_exec", side_effect=processes):
            result = await tool._commit_changes(repo_dir, "My PR")

        assert not result.startswith("Error")
        assert "auth.py" in result or "src/auth.py" in result or "M  src/auth.py" in result
        assert "new_feature.py" in result or "A  src/new_feature.py" in result
        assert "old.py" in result or "D  src/old.py" in result

    @pytest.mark.asyncio
    async def test_commit_failure_returns_error(self, tool: GitHubPRTool, tmp_path: Path) -> None:
        """When git commit fails, the error message is returned."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        processes = [
            make_process(0, b"M  file.py\n"),  # git status
            make_process(0, b""),  # git add -A
            make_process(1, b"", b"nothing to commit"),  # git commit fails
        ]

        with patch("asyncio.create_subprocess_exec", side_effect=processes):
            result = await tool._commit_changes(repo_dir, "My PR")

        assert result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_commit_warns_about_git_add_all(
        self, tool: GitHubPRTool, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A WARNING log is emitted when staging all changes with git add -A."""
        import logging

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        processes = [
            make_process(0, b"M  file.py\n"),
            make_process(0, b""),
            make_process(0, b"[main abc1] Commit\n"),
        ]

        with caplog.at_level(logging.WARNING, logger="core.tools.github_pr"):
            with patch("asyncio.create_subprocess_exec", side_effect=processes):
                await tool._commit_changes(repo_dir, "My commit")

        assert any("git add -A" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# PR creation flow
# ---------------------------------------------------------------------------


class TestPRCreationFlow:
    """Tests for the overall PR creation flow in run()."""

    @pytest.mark.asyncio
    async def test_already_on_feature_branch_no_new_branch_created(
        self, tool: GitHubPRTool, tmp_path: Path
    ) -> None:
        """When already on a feature branch (not main/master), no new branch is created."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        with (
            patch.object(
                GitHubPRTool, "_has_uncommitted_changes", new=AsyncMock(return_value=False)
            ),
            patch.object(
                GitHubPRTool,
                "_get_current_branch",
                new=AsyncMock(return_value="feature/my-fix"),
            ),
            patch.object(GitHubPRTool, "_create_branch", new=AsyncMock()) as mock_create_branch,
            patch.object(GitHubPRTool, "_push_branch", new=AsyncMock(return_value="Branch pushed")),
            patch.object(
                GitHubPRTool,
                "_create_pr",
                new=AsyncMock(
                    return_value="Pull request created: https://github.com/org/repo/pull/42"
                ),
            ),
        ):
            result = await tool.run(
                repo_path=str(repo_dir),
                title="My PR",
                body="Description",
            )

        assert not result.startswith("Error")
        mock_create_branch.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_main_without_branch_name_returns_error(
        self, tool: GitHubPRTool, tmp_path: Path
    ) -> None:
        """On main branch without branch_name parameter returns error."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        with (
            patch.object(
                GitHubPRTool, "_has_uncommitted_changes", new=AsyncMock(return_value=True)
            ),
            patch.object(GitHubPRTool, "_get_current_branch", new=AsyncMock(return_value="main")),
        ):
            result = await tool.run(
                repo_path=str(repo_dir),
                title="My PR",
                body="Description",
                # no branch_name
            )

        assert result.startswith("Error:")
        assert "branch_name" in result or "feature branch" in result.lower()

    @pytest.mark.asyncio
    async def test_on_main_creates_branch_with_sanitized_name(
        self, tool: GitHubPRTool, tmp_path: Path
    ) -> None:
        """On main branch, a new branch is created with the sanitized name."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        created_branch_names: list[str] = []

        # patch.object on the class passes 'self' as the first arg to the replacement
        async def capture_create_branch(self_: object, d: Path, name: str) -> str:
            created_branch_names.append(name)
            return f"Created branch: {name}"

        with (
            patch.object(
                GitHubPRTool, "_has_uncommitted_changes", new=AsyncMock(return_value=False)
            ),
            patch.object(GitHubPRTool, "_get_current_branch", new=AsyncMock(return_value="main")),
            patch.object(GitHubPRTool, "_create_branch", new=capture_create_branch),
            patch.object(GitHubPRTool, "_push_branch", new=AsyncMock(return_value="Branch pushed")),
            patch.object(
                GitHubPRTool,
                "_create_pr",
                new=AsyncMock(
                    return_value="Pull request created: https://github.com/org/repo/pull/1"
                ),
            ),
        ):
            await tool.run(
                repo_path=str(repo_dir),
                title="My PR",
                body="Description",
                branch_name="fix auth module",  # spaces -> hyphens
            )

        assert created_branch_names == ["fix-auth-module"]

    @pytest.mark.asyncio
    async def test_gh_cli_not_available_returns_error(
        self, tool: GitHubPRTool, tmp_path: Path
    ) -> None:
        """When gh CLI is not available, _create_pr returns a clear error."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        proc = make_process(127, b"", b"gh: command not found")

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await tool._create_pr(repo_dir, "Title", "Body", "main", False, None)

        assert result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_pr_already_exists_returns_note(self, tool: GitHubPRTool, tmp_path: Path) -> None:
        """When a PR already exists, a note is returned (not a hard failure)."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        proc = make_process(1, b"", b"GraphQL: A pull request already exists for this branch.")

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await tool._create_pr(repo_dir, "Title", "Body", "main", False, None)

        assert "already exists" in result.lower()
        assert not result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_push_failure_returns_error(self, tool: GitHubPRTool, tmp_path: Path) -> None:
        """When push fails, the error is propagated."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        proc = make_process(1, b"", b"error: failed to push some refs")

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await tool._push_branch(repo_dir, "my-branch")

        assert result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_nonexistent_repo_path_returns_error(
        self, tool: GitHubPRTool, tmp_path: Path
    ) -> None:
        """run() with a nonexistent repo_path returns an error immediately."""
        result = await tool.run(
            repo_path=str(tmp_path / "does-not-exist"),
            title="My PR",
            body="Body",
        )
        assert result.startswith("Error:")
        assert "does not exist" in result.lower()
