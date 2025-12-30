"""Tool to execute tests via pytest."""

from __future__ import annotations

import asyncio
from pathlib import Path

from .base import Tool


class TestRunnerTool(Tool):
    """Executes pytest on a specific file or directory."""

    name = "test_runner"
    description = "Executes pytest on a specific file or directory to verify code changes."
    category = "development"

    async def run(self, test_path: str, args: list[str] | None = None) -> str:
        """
        Run pytest on the specified path.

        Args:
            test_path: Relative path to the test file or directory.
            args: Optional list of additional pytest arguments.

        Returns:
            The output of the pytest command and the exit code.
        """
        # 1. Validate Path
        path_obj = Path(test_path)
        # Prevent absolute paths or traversal attempts (simple check)
        if path_obj.is_absolute() or ".." in str(path_obj):
            return "Error: test_path must be relative and cannot contain '..'"

        if not path_obj.exists():
            return f"Error: Path '{test_path}' does not exist."

        # 2. Construct Command
        cmd = ["pytest", str(path_obj)]
        if args:
            cmd.extend(args)

        # 3. Execute
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            output = stdout.decode().strip()
            error = stderr.decode().strip()

            result = f"Exit Code: {process.returncode}\n\nSTDOUT:\n{output}"
            if error:
                result += f"\n\nSTDERR:\n{error}"

            return result

        except Exception as e:
            return f"Failed to execute pytest: {e}"
