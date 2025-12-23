"""Quality Assurance tools for autonomous verification."""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from .base import Tool
from .filesystem import validate_path

LOGGER = logging.getLogger(__name__)


class RunPytestTool(Tool):
    """Run pytest on a specified path."""

    name = "run_pytest"
    description = (
        "Run pytest to verify code changes. "
        "Args: test_path (str, optional) - Path to test file or directory (defaults to 'tests/')."
    )

    def __init__(self, base_path: str) -> None:
        self._base_path = base_path

    async def run(self, test_path: str = "tests/", **kwargs: Any) -> str:
        # Validate path roughly - ensure it's inside repo
        try:
            target = validate_path(self._base_path, test_path)
            # validate_path ensures it is inside base_path.
        except ValueError as exc:
            return f"Error: Invalid test path: {exc}"

        # Does target exist? pytest works on dirs too.
        # But if it doesn't exist, pytest will fail.

        # We run pytest via subprocess.
        # Security: cwd is locked to base_path.
        # But command injection? test_path is validated via validate_path which
        # resolves to a Path object, confirming it's a file/dir on disk inside sandbox (mostly).
        # But we pass `str(test_path)` to subprocess?
        # Actually `subprocess.run` with list of args doesn't use shell so it's safer.
        # Use relative path for cleaner output?
        # Let's use relative path from base_path.

        rel_path = str(target.relative_to(self._base_path))
        if rel_path == ".":
            # If user passed base_path itself
            rel_path = "tests/"  # Default fallback if just dir passed?
            # Or just "."? Pytest on . runs everything.

        cmd = ["pytest", rel_path]

        try:
            result = subprocess.run(  # noqa: S603
                cmd,
                cwd=self._base_path,
                capture_output=True,
                text=True,
                timeout=60,  # 1 min timeout
                check=False,  # Don't raise on non-zero exit (tests failing)
            )

            output = result.stdout + result.stderr
            # Truncate if too long?
            if len(output) > 5000:
                output = output[:5000] + "\n...[Output Truncated]"

            summary_line = "No summary found."
            for line in output.splitlines():
                if "passed" in line or "failed" in line:
                    summary_line = line

            status = "PASSED" if result.returncode == 0 else "FAILED"
            return f"Pytest {status}\nSummary: {summary_line}\n\nDetails:\n{output}"

        except subprocess.TimeoutExpired:
            return "Error: Pytest timed out after 60 seconds."
        except Exception as exc:
            return f"Error: Failed to run pytest: {exc}"


class RunLinterTool(Tool):
    """Run ruff linter on specified files."""

    name = "run_linter"
    description = (
        "Run ruff linter to check for syntax and style errors. "
        "Args: files (list[str], optional) - List of files to check (defaults to '.')."
    )

    def __init__(self, base_path: str) -> None:
        self._base_path = base_path

    async def run(self, files: list[str] | None = None, **kwargs: Any) -> str:
        target_files = []
        if not files:
            target_files = ["."]
        else:
            for f in files:
                try:
                    p = validate_path(self._base_path, f)
                    target_files.append(str(p.relative_to(self._base_path)))
                except ValueError as exc:
                    return f"Error: Invalid file path '{f}': {exc}"

        cmd = ["ruff", "check", *target_files]

        try:
            result = subprocess.run(  # noqa: S603
                cmd,
                cwd=self._base_path,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

            if result.returncode == 0:
                return "Linting Passed: No errors found."

            output = result.stdout + result.stderr
            if len(output) > 5000:
                output = output[:5000] + "\n...[Output Truncated]"

            return f"Linting Failed:\n{output}"

        except subprocess.TimeoutExpired:
            return "Error: Linter timed out."
        except Exception as exc:
            return f"Error: Failed to run linter: {exc}"
