"""Quality Assurance tools for autonomous verification."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.tools.base import Tool
from core.tools.filesystem import validate_path

LOGGER = logging.getLogger(__name__)


class RunPytestTool(Tool):
    """Run pytest on a specified path."""

    name = "run_pytest"
    description = (
        "Run pytest to verify code changes. "
        "Args: test_path (str, optional) - Path to test file or directory (defaults to 'tests/')."
    )
    parameters = {
        "type": "object",
        "properties": {
            "test_path": {
                "type": "string",
                "description": "Path to test file or directory (default: 'tests/')",
            }
        },
    }

    def __init__(self, base_path: str) -> None:
        self._base_path = base_path

    async def run(self, test_path: str = "tests/", **kwargs: Any) -> str:
        try:
            target = validate_path(self._base_path, test_path)
        except ValueError as exc:
            return f"Error: Invalid test path: {exc}"

        rel_path = str(target.relative_to(self._base_path))
        if rel_path == ".":
            rel_path = "tests/"

        cmd = ["pytest", rel_path]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self._base_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return "Error: Pytest timed out after 60 seconds."

            output = (stdout or b"").decode("utf-8", errors="replace") + (stderr or b"").decode(
                "utf-8", errors="replace"
            )
            if len(output) > 5000:
                output = output[:5000] + "\n...[Output Truncated]"

            summary_line = "No summary found."
            for line in output.splitlines():
                if "passed" in line or "failed" in line:
                    summary_line = line

            status = "PASSED" if proc.returncode == 0 else "FAILED"
            return f"Pytest {status}\nSummary: {summary_line}\n\nDetails:\n{output}"

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
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self._base_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return "Error: Linter timed out."

            if proc.returncode == 0:
                return "Linting Passed: No errors found."

            output = (stdout or b"").decode("utf-8", errors="replace") + (stderr or b"").decode(
                "utf-8", errors="replace"
            )
            if len(output) > 5000:
                output = output[:5000] + "\n...[Output Truncated]"

            return f"Linting Failed:\n{output}"

        except Exception as exc:
            return f"Error: Failed to run linter: {exc}"
