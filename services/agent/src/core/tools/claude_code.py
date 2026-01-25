"""Tool to delegate code investigation to Claude Code CLI."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from core.tools.base import Tool

LOGGER = logging.getLogger(__name__)

# Maximum output length to return (avoid overwhelming context)
MAX_OUTPUT_LENGTH = 50000


class ClaudeCodeTool(Tool):
    """Delegates code investigation and fixes to Claude Code CLI agent."""

    name = "claude_code"
    description = (
        "Delegate code investigation or bug fixing to Claude Code. "
        "Use mode='investigate' to analyze and report findings, "
        "or mode='fix' to attempt a fix and prepare changes for PR."
    )
    category = "development"
    parameters = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "Description of what to investigate or fix. "
                    "Be specific about the bug, error, or feature."
                ),
            },
            "repo_path": {
                "type": "string",
                "description": "Local path to the cloned repository.",
            },
            "mode": {
                "type": "string",
                "enum": ["investigate", "fix"],
                "description": (
                    "Mode of operation: "
                    "'investigate' = analyze and report findings only, "
                    "'fix' = attempt to fix and prepare changes."
                ),
            },
            "context": {
                "type": "string",
                "description": (
                    "Additional context like stack traces, error messages, "
                    "or reproduction steps."
                ),
            },
            "files_hint": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of files likely related to the issue.",
            },
        },
        "required": ["task", "repo_path", "mode"],
    }

    def __init__(self, timeout_seconds: int = 600) -> None:
        """Initialize with configurable timeout."""
        self.timeout_seconds = timeout_seconds

    async def run(
        self,
        task: str,
        repo_path: str,
        mode: str,
        context: str | None = None,
        files_hint: list[str] | None = None,
    ) -> str:
        """
        Run Claude Code to investigate or fix a code issue.

        Args:
            task: Description of the task.
            repo_path: Path to the repository.
            mode: 'investigate' or 'fix'.
            context: Additional context (stack traces, etc.).
            files_hint: Files likely related to the issue.

        Returns:
            Claude Code's findings or fix summary.
        """
        # Validate repo path
        repo_dir = Path(repo_path)
        if not repo_dir.exists():
            return f"Error: Repository path does not exist: {repo_path}"
        if not (repo_dir / ".git").exists():
            return f"Error: Not a git repository: {repo_path}"

        # Build the prompt
        prompt = self._build_prompt(task, mode, context, files_hint)

        LOGGER.info(
            "Running Claude Code in %s mode on %s",
            mode,
            repo_path,
        )

        # Run claude CLI
        try:
            result = await self._run_claude(prompt, repo_dir)
            return result
        except Exception as e:
            LOGGER.exception("Claude Code execution failed")
            return f"Error: Claude Code execution failed: {e}"

    def _build_prompt(
        self,
        task: str,
        mode: str,
        context: str | None,
        files_hint: list[str] | None,
    ) -> str:
        """Build the prompt for Claude Code."""
        parts = []

        # Mode-specific instructions
        if mode == "investigate":
            parts.append(
                "## Investigation Task\n\n"
                "Analyze this codebase to understand and report on the issue below. "
                "DO NOT make any changes. Only investigate and report:\n"
                "1. Root cause analysis\n"
                "2. Affected files and functions\n"
                "3. Recommended fix approach\n"
                "4. Potential risks or side effects\n"
            )
        else:  # fix
            parts.append(
                "## Fix Task\n\n"
                "Analyze and FIX the issue below. After fixing:\n"
                "1. Run relevant tests to verify the fix\n"
                "2. Create a git commit with a descriptive message\n"
                "3. Report what was changed and why\n"
            )

        # The actual task
        parts.append(f"## Issue Description\n\n{task}\n")

        # Additional context
        if context:
            parts.append(f"## Additional Context\n\n{context}\n")

        # File hints
        if files_hint:
            files_list = "\n".join(f"- {f}" for f in files_hint)
            parts.append(f"## Likely Related Files\n\n{files_list}\n")

        return "\n".join(parts)

    async def _run_claude(self, prompt: str, cwd: Path) -> str:
        """Execute claude CLI and capture output."""
        # Use --print for non-interactive mode
        # Use --dangerously-skip-permissions to avoid interactive prompts
        cmd = [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "-p",
            prompt,
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Set environment to avoid interactive prompts
            env={
                "CI": "true",
                "CLAUDE_CODE_ENTRYPOINT": "agent-tool",
            },
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout_seconds,
            )

            output = stdout.decode()
            errors = stderr.decode()

            # Truncate if too long
            if len(output) > MAX_OUTPUT_LENGTH:
                output = (
                    output[: MAX_OUTPUT_LENGTH // 2]
                    + "\n\n... [output truncated] ...\n\n"
                    + output[-MAX_OUTPUT_LENGTH // 2 :]
                )

            result = f"## Claude Code Output\n\n{output}"

            if errors and process.returncode != 0:
                result += f"\n\n## Errors\n\n{errors}"

            result += f"\n\n## Exit Code: {process.returncode}"

            return result

        except TimeoutError:
            process.kill()
            return (
                f"Error: Claude Code timed out after {self.timeout_seconds} seconds. "
                "The task may be too complex or stuck."
            )
