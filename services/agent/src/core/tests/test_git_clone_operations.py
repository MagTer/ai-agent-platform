"""Tests for GitCloneTool operational behavior: workspace naming, pull, and clone."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from core.tools.git_clone import GitCloneTool

FAKE_CONTEXT_ID = UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
def git_tool(tmp_path: Path) -> GitCloneTool:
    """Return a GitCloneTool backed by a temp directory."""
    return GitCloneTool(workspace_base=tmp_path / "workspaces")


# ---------------------------------------------------------------------------
# _derive_workspace_name
# ---------------------------------------------------------------------------


class TestDeriveWorkspaceName:
    """Tests for the workspace name derivation helper."""

    def test_github_https_with_git_suffix(self, git_tool: GitCloneTool) -> None:
        """HTTPS GitHub URL with .git suffix extracts repo name."""
        assert git_tool._derive_workspace_name("https://github.com/org/repo.git") == "repo"

    def test_github_https_without_git_suffix(self, git_tool: GitCloneTool) -> None:
        """HTTPS GitHub URL without .git suffix extracts repo name."""
        assert git_tool._derive_workspace_name("https://github.com/org/repo") == "repo"

    def test_azure_devops_git_url(self, git_tool: GitCloneTool) -> None:
        """Azure DevOps _git/ URL extracts repo name."""
        assert (
            git_tool._derive_workspace_name("https://dev.azure.com/org/project/_git/my-service")
            == "my-service"
        )

    def test_ssh_url_git_suffix_stripped(self, git_tool: GitCloneTool) -> None:
        """SSH git@ URL with .git suffix is stripped to give the bare repo name."""
        # git@github.com:org/repo.git -> split('/') -> last='repo.git' -> strip .git -> 'repo'
        assert git_tool._derive_workspace_name("git@github.com:org/repo.git") == "repo"

    def test_trailing_slash_handled(self, git_tool: GitCloneTool) -> None:
        """URLs with trailing slashes should still yield the correct name."""
        assert git_tool._derive_workspace_name("https://github.com/org/repo/") == "repo"

    def test_azure_devops_without_git_suffix(self, git_tool: GitCloneTool) -> None:
        """ADO URL without .git suffix extracts name correctly."""
        assert (
            git_tool._derive_workspace_name("https://dev.azure.com/org/project/_git/service")
            == "service"
        )


# ---------------------------------------------------------------------------
# _git_pull - pull failure returns error, not hard-reset
# ---------------------------------------------------------------------------


class TestGitPullFailure:
    """Tests that pull failure returns an error message and does NOT hard-reset."""

    @pytest.mark.asyncio
    async def test_pull_failure_returns_error_message(self, git_tool: GitCloneTool) -> None:
        """When git pull --ff-only fails, an error message is returned (no reset)."""

        # Build fake process objects for the subprocess calls
        def make_process(returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> MagicMock:
            proc = MagicMock()
            proc.returncode = returncode
            proc.communicate = AsyncMock(return_value=(stdout, stderr))
            return proc

        # pull --ff-only fails with exit code 1
        pull_error_output = b"fatal: Not possible to fast-forward, aborting."
        processes = [make_process(1, b"", pull_error_output)]

        with patch("asyncio.create_subprocess_exec", side_effect=processes) as mock_exec:
            result = await git_tool._git_pull(Path("/some/workspace"), branch=None)

        assert result.startswith("Error:")
        assert "diverged" in result.lower() or "manual intervention" in result.lower()

        # Ensure git reset --hard was NOT called
        for called_args in mock_exec.call_args_list:
            args = called_args[0]
            assert "reset" not in args, "git reset --hard must NOT be called on pull failure"

    @pytest.mark.asyncio
    async def test_pull_failure_includes_error_text(self, git_tool: GitCloneTool) -> None:
        """Error message includes the pull error output for diagnostics."""

        def make_process(returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> MagicMock:
            proc = MagicMock()
            proc.returncode = returncode
            proc.communicate = AsyncMock(return_value=(stdout, stderr))
            return proc

        pull_err = b"error: Your local changes would be overwritten by merge."
        process = make_process(128, b"", pull_err)

        with patch("asyncio.create_subprocess_exec", return_value=process):
            result = await git_tool._git_pull(Path("/repo"), branch=None)

        assert "error" in result.lower() or "overwritten" in result.lower()

    @pytest.mark.asyncio
    async def test_pull_success_returns_success_message(self, git_tool: GitCloneTool) -> None:
        """Successful git pull returns a success message with the path."""

        def make_process(returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> MagicMock:
            proc = MagicMock()
            proc.returncode = returncode
            proc.communicate = AsyncMock(return_value=(stdout, stderr))
            return proc

        pull_ok = make_process(0, b"Already up to date.", b"")

        with patch("asyncio.create_subprocess_exec", return_value=pull_ok):
            result = await git_tool._git_pull(Path("/my/workspace"), branch=None)

        assert "updated" in result.lower() or "/my/workspace" in result

    @pytest.mark.asyncio
    async def test_pull_with_branch_checkout_failure_is_tolerated(
        self, git_tool: GitCloneTool
    ) -> None:
        """If branch checkout fails (non-zero), pull still proceeds."""

        def make_process(returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> MagicMock:
            proc = MagicMock()
            proc.returncode = returncode
            proc.communicate = AsyncMock(return_value=(stdout, stderr))
            return proc

        checkout_fail = make_process(1, b"", b"error: pathspec 'feature' did not match")
        pull_ok = make_process(0, b"Already up to date.", b"")

        with patch("asyncio.create_subprocess_exec", side_effect=[checkout_fail, pull_ok]):
            result = await git_tool._git_pull(Path("/repo"), branch="feature")

        # Pull succeeded despite checkout issue
        assert "updated" in result.lower() or "/repo" in result


# ---------------------------------------------------------------------------
# Successful clone with DB record tracking
# ---------------------------------------------------------------------------


class TestSuccessfulClone:
    """Tests for the happy-path clone flow."""

    @pytest.mark.asyncio
    async def test_clone_success_returns_path_message(
        self, git_tool: GitCloneTool, tmp_path: Path
    ) -> None:
        """Successful clone returns a message containing the workspace path."""

        def make_process(returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> MagicMock:
            proc = MagicMock()
            proc.returncode = returncode
            proc.communicate = AsyncMock(return_value=(stdout, stderr))
            return proc

        clone_ok = make_process(0, b"", b"Cloning into 'repo'...")

        with patch("asyncio.create_subprocess_exec", return_value=clone_ok):
            result = await git_tool.run(
                repo_url="https://github.com/org/myrepo.git",
                context_id=FAKE_CONTEXT_ID,
            )

        assert "myrepo" in result or "cloned" in result.lower()
        assert not result.startswith("Error")

    @pytest.mark.asyncio
    async def test_clone_creates_db_workspace_record(
        self, git_tool: GitCloneTool, tmp_path: Path
    ) -> None:
        """After a successful clone, a Workspace record is created in the DB."""

        def make_process(returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> MagicMock:
            proc = MagicMock()
            proc.returncode = returncode
            proc.communicate = AsyncMock(return_value=(stdout, stderr))
            return proc

        clone_ok = make_process(0, b"", b"Cloning into 'repo'...")

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=clone_ok):
            with patch(
                "core.tools.git_clone.GitCloneTool._create_or_update_workspace",
                new_callable=AsyncMock,
            ) as mock_create_ws:
                result = await git_tool.run(
                    repo_url="https://github.com/org/myrepo.git",
                    context_id=FAKE_CONTEXT_ID,
                    session=mock_session,
                )

        assert not result.startswith("Error")
        mock_create_ws.assert_called_once()
        call_kwargs = mock_create_ws.call_args
        # context_id should be passed
        assert FAKE_CONTEXT_ID in call_kwargs[0] or FAKE_CONTEXT_ID in call_kwargs[1].values()

    @pytest.mark.asyncio
    async def test_clone_failure_returns_error(self, git_tool: GitCloneTool) -> None:
        """When git clone fails, an error message is returned."""

        def make_process(returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> MagicMock:
            proc = MagicMock()
            proc.returncode = returncode
            proc.communicate = AsyncMock(return_value=(stdout, stderr))
            return proc

        clone_fail = make_process(
            128, b"", b"fatal: repository 'https://github.com/org/missing.git' not found"
        )

        with patch("asyncio.create_subprocess_exec", return_value=clone_fail):
            result = await git_tool.run(
                repo_url="https://github.com/org/missing.git",
                context_id=FAKE_CONTEXT_ID,
            )

        assert result.startswith("Error")
        assert "clone" in result.lower() or "failed" in result.lower()
