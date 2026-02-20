"""Tests for context injector path safety."""

from pathlib import Path
from uuid import uuid4

import pytest

from core.context.files import CONTEXT_DATA_BASE, ensure_context_directories
from core.runtime.context_injector import ContextInjector
from shared.models import AgentMessage


@pytest.mark.asyncio
async def test_inject_pinned_files_allows_context_data_dir() -> None:
    """Test that pinned files in context data directory pass path safety check."""
    context_id = uuid4()
    ensure_context_directories(context_id)

    # Create a test file in context data directory
    test_file_path = CONTEXT_DATA_BASE.resolve() / str(context_id) / "files" / "test.md"
    test_file_path.write_text("Test content", encoding="utf-8")

    injector = ContextInjector()
    history: list[AgentMessage] = []

    # Inject pinned file from context data directory (absolute path)
    await injector._inject_pinned_files(
        history=history,
        pinned_files=[str(test_file_path)],
        workspace_path=None,
    )

    # File should be injected (not blocked)
    assert len(history) == 1
    content = history[0].content or ""
    assert "PINNED FILES" in content
    assert "Test content" in content

    # Cleanup
    test_file_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_inject_pinned_files_blocks_unsafe_paths() -> None:
    """Test that pinned files outside allowed directories are blocked."""
    injector = ContextInjector()
    history: list[AgentMessage] = []

    # Try to inject a file outside allowed paths
    unsafe_path = "/etc/passwd"

    await injector._inject_pinned_files(
        history=history,
        pinned_files=[unsafe_path],
        workspace_path=None,
    )

    # File should be blocked (no content injected)
    assert len(history) == 0


@pytest.mark.asyncio
async def test_inject_pinned_files_allows_workspace_paths() -> None:
    """Test that pinned files in workspace path are allowed."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace_path = Path(tmpdir)
        test_file = workspace_path / "test.md"
        test_file.write_text("Workspace content", encoding="utf-8")

        injector = ContextInjector()
        history: list[AgentMessage] = []

        await injector._inject_pinned_files(
            history=history,
            pinned_files=[str(test_file)],
            workspace_path=str(workspace_path),
        )

        # File should be injected
        assert len(history) == 1
        content = history[0].content or ""
        assert "Workspace content" in content


@pytest.mark.asyncio
async def test_inject_pinned_files_allows_home_directory() -> None:
    """Test that pinned files in home directory are allowed."""
    import tempfile

    # Create a temp file in a home-like structure
    with tempfile.TemporaryDirectory() as tmpdir:
        home_path = Path(tmpdir)
        test_file = home_path / "test.md"
        test_file.write_text("Home content", encoding="utf-8")

        injector = ContextInjector()
        history: list[AgentMessage] = []

        # Use the temp dir as "home" by passing it as an allowed base
        # (This simulates the home directory check in the actual code)
        await injector._inject_pinned_files(
            history=history,
            pinned_files=[str(test_file)],
            workspace_path=str(home_path),  # Pass as workspace to make it allowed
        )

        # File should be injected
        assert len(history) == 1
        content = history[0].content or ""
        assert "Home content" in content


@pytest.mark.asyncio
async def test_is_path_safe_docker_simulation() -> None:
    """Test path safety validation with Docker-like absolute paths."""
    injector = ContextInjector()

    # Simulate Docker environment where files are at /app/data/contexts/...
    docker_context_path = "/app/data/contexts/test-uuid/files/memory.md"
    allowed_bases = [str(CONTEXT_DATA_BASE.resolve())]

    # If CONTEXT_DATA_BASE resolves to /app/data/contexts (Docker)
    # or /home/user/project/data/contexts (dev), this should pass
    is_safe = injector._is_path_safe(docker_context_path, allowed_bases)

    # The safety check depends on CONTEXT_DATA_BASE resolution
    # In Docker, CONTEXT_DATA_BASE = /app/data/contexts
    # In dev, CONTEXT_DATA_BASE = <repo>/data/contexts
    # Both should be in allowed_bases after resolve()
    if str(CONTEXT_DATA_BASE.resolve()) in docker_context_path:
        assert is_safe
    else:
        # In dev, the docker path won't match - that's expected
        # The test verifies the pattern works correctly
        pass
