"""Unit tests for git_clone URL validation."""

from __future__ import annotations

import pytest

from core.tools.git_clone import GitCloneTool


@pytest.fixture
def git_clone_tool() -> GitCloneTool:
    """Create a GitCloneTool instance for testing."""
    return GitCloneTool()


def test_validate_https_url_success(git_clone_tool: GitCloneTool) -> None:
    """Test that valid HTTPS URLs pass validation."""
    valid_urls = [
        "https://github.com/org/repo.git",
        "https://dev.azure.com/org/project/_git/repo",
        "https://gitlab.com/group/project.git",
    ]
    for url in valid_urls:
        # Should not raise
        git_clone_tool._validate_repo_url(url)


def test_validate_ssh_url_success(git_clone_tool: GitCloneTool) -> None:
    """Test that valid SSH URLs pass validation."""
    valid_urls = [
        "git@github.com:org/repo.git",
        "git@gitlab.com:group/project.git",
        "ssh://git@github.com/org/repo.git",
    ]
    for url in valid_urls:
        # Should not raise
        git_clone_tool._validate_repo_url(url)


def test_validate_embedded_credentials_rejected(git_clone_tool: GitCloneTool) -> None:
    """Test that URLs with embedded credentials are rejected."""
    urls_with_creds = [
        "https://user:password@github.com/org/repo.git",
        "https://admin:secret123@dev.azure.com/org/project/_git/repo",
        "https://token@github.com/org/repo.git",
    ]
    for url in urls_with_creds:
        with pytest.raises(ValueError, match="must not contain embedded credentials"):
            git_clone_tool._validate_repo_url(url)


def test_validate_newlines_rejected(git_clone_tool: GitCloneTool) -> None:
    """Test that URLs with newlines are rejected."""
    urls_with_newlines = [
        "https://github.com/org/repo.git\n",
        "https://github.com/org/repo.git\r\n",
        "https://github.com/\norg/repo.git",
    ]
    for url in urls_with_newlines:
        with pytest.raises(ValueError, match="invalid character"):
            git_clone_tool._validate_repo_url(url)


def test_validate_null_bytes_rejected(git_clone_tool: GitCloneTool) -> None:
    """Test that URLs with null bytes are rejected."""
    url_with_null = "https://github.com/org/repo.git\x00"
    with pytest.raises(ValueError, match="invalid character"):
        git_clone_tool._validate_repo_url(url_with_null)


def test_validate_shell_metacharacters_rejected(git_clone_tool: GitCloneTool) -> None:
    """Test that URLs with shell metacharacters are rejected."""
    urls_with_metacharacters = [
        "https://github.com/org/repo.git;rm -rf /",
        "https://github.com/org/repo.git&whoami",
        "https://github.com/org/repo.git|cat /etc/passwd",
        "https://github.com/org/repo.git`id`",
        "https://github.com/org/repo.git$(whoami)",
    ]
    for url in urls_with_metacharacters:
        with pytest.raises(ValueError, match="invalid character"):
            git_clone_tool._validate_repo_url(url)


def test_validate_non_https_ssh_protocol_rejected(git_clone_tool: GitCloneTool) -> None:
    """Test that non-HTTPS/SSH protocols are rejected."""
    invalid_protocols = [
        "ftp://github.com/org/repo.git",
        "file:///local/path/repo",
        "http://github.com/org/repo.git",  # HTTP not allowed (only HTTPS)
    ]
    for url in invalid_protocols:
        with pytest.raises(ValueError, match="Unsupported protocol"):
            git_clone_tool._validate_repo_url(url)


def test_validate_empty_url_rejected(git_clone_tool: GitCloneTool) -> None:
    """Test that empty URLs are rejected."""
    empty_urls = ["", "   ", "\t\n"]
    for url in empty_urls:
        with pytest.raises(ValueError, match="cannot be empty"):
            git_clone_tool._validate_repo_url(url)


@pytest.mark.asyncio
async def test_run_with_invalid_url_returns_error(git_clone_tool: GitCloneTool) -> None:
    """Test that run() returns error message for invalid URLs."""
    result = await git_clone_tool.run(repo_url="https://user:pass@github.com/org/repo.git")
    assert result.startswith("Error: Invalid repository URL")
    assert "embedded credentials" in result


@pytest.mark.asyncio
async def test_run_with_shell_injection_returns_error(git_clone_tool: GitCloneTool) -> None:
    """Test that run() returns error for shell injection attempts."""
    result = await git_clone_tool.run(repo_url="https://github.com/org/repo.git;rm -rf /")
    assert result.startswith("Error: Invalid repository URL")
    assert "invalid character" in result
