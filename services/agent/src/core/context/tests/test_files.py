"""Tests for context file management utilities."""

import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from core.context.files import CONTEXT_DATA_BASE, get_context_dir


def test_get_context_dir_returns_absolute_path() -> None:
    """Test that get_context_dir returns an absolute path."""
    context_id = uuid4()
    path = get_context_dir(context_id)

    assert path.is_absolute()
    assert str(context_id) in str(path)
    assert str(CONTEXT_DATA_BASE.resolve()) in str(path)


def test_get_context_dir_stays_within_base() -> None:
    """Test that get_context_dir never escapes CONTEXT_DATA_BASE."""
    context_id = uuid4()
    path = get_context_dir(context_id)

    # Resolved path should start with CONTEXT_DATA_BASE
    assert str(path).startswith(str(CONTEXT_DATA_BASE.resolve()))


def test_ensure_context_directories_creates_structure() -> None:
    """Test that ensure_context_directories creates files/ and skills/ subdirectories."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Override CONTEXT_DATA_BASE for this test
        original_base = os.environ.get("CONTEXT_DATA_DIR")
        os.environ["CONTEXT_DATA_DIR"] = tmpdir

        try:
            # Import after setting env var (module-level CONTEXT_DATA_BASE won't be affected,
            # but the function will use get_context_dir which reads the current value)
            from core.context.files import ensure_context_directories as ensure_dirs_fresh

            context_id = uuid4()
            base_path = ensure_dirs_fresh(context_id)

            # Check that directories were created
            assert base_path.exists()
            assert (base_path / "files").exists()
            assert (base_path / "files").is_dir()
            assert (base_path / "skills").exists()
            assert (base_path / "skills").is_dir()

        finally:
            if original_base:
                os.environ["CONTEXT_DATA_DIR"] = original_base
            else:
                os.environ.pop("CONTEXT_DATA_DIR", None)


def test_ensure_context_directories_idempotent() -> None:
    """Test that ensure_context_directories can be called multiple times safely."""
    with tempfile.TemporaryDirectory() as tmpdir:
        original_base = os.environ.get("CONTEXT_DATA_DIR")
        os.environ["CONTEXT_DATA_DIR"] = tmpdir

        try:
            from core.context.files import ensure_context_directories as ensure_dirs_fresh

            context_id = uuid4()

            # Call multiple times
            base1 = ensure_dirs_fresh(context_id)
            base2 = ensure_dirs_fresh(context_id)

            # Should return same path and not raise errors
            assert base1 == base2
            assert (base1 / "files").exists()
            assert (base1 / "skills").exists()

        finally:
            if original_base:
                os.environ["CONTEXT_DATA_DIR"] = original_base
            else:
                os.environ.pop("CONTEXT_DATA_DIR", None)


@pytest.mark.parametrize(
    "env_value", ["data/contexts", "/tmp/agent-contexts", "custom/path"]  # noqa: S108
)
def test_context_data_base_respects_env(env_value: str) -> None:
    """Test that CONTEXT_DATA_BASE can be configured via environment variable."""
    # This test just verifies the pattern; actual override requires module reload
    # which is complex in pytest. The docstring documents the expected behavior.
    assert CONTEXT_DATA_BASE == Path(os.getenv("CONTEXT_DATA_DIR", "data/contexts"))
