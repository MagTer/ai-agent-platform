"""Tool to delegate code investigation to Claude Code CLI.

SECURITY NOTES:
- This tool NO LONGER uses --dangerously-skip-permissions
- Investigate mode uses --allowlist for read-only tool access
- Fix mode requires explicit admin approval via approved_by parameter
- Path traversal protection validates repos are within context workspace
- Dangerous command patterns are blocked in task descriptions
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from uuid import UUID

from core.tools.base import Tool

LOGGER = logging.getLogger(__name__)

# Maximum output length to return (avoid overwhelming context)
MAX_OUTPUT_LENGTH = 50000

# Allowlist of safe operations for investigate mode (read-only)
INVESTIGATE_ALLOWED_TOOLS = [
    "Read",
    "Glob",
    "Grep",
    "Bash(git log*)",
    "Bash(git diff*)",
    "Bash(git show*)",
    "Bash(git status*)",
    "Bash(ls *)",
]

# Dangerous patterns to block in task descriptions
DANGEROUS_PATTERNS = [
    r"rm\s+-rf",
    r"rm\s+.*--no-preserve-root",
    r">\s*/etc/",
    r"curl.*\|.*sh",
    r"wget.*\|.*sh",
    r"git\s+push\s+.*--force",
    r"git\s+reset\s+--hard",
    r"chmod\s+777",
    r"sudo\s+",
    r"eval\s*\(",
    r"exec\s*\(",
    r"/etc/passwd",
    r"/etc/shadow",
    r"\.\.\/\.\.\/",  # Path traversal attempts
]


class ClaudeCodeTool(Tool):
    """Delegates code investigation and fixes to Claude Code CLI agent.

    SECURITY: This tool enforces strict constraints:
    - Investigate mode: Read-only access via --allowlist
    - Fix mode: Requires explicit approval (approved_by parameter)
    - Path validation: Repos must be within context workspace
    - Input sanitization: Blocks dangerous command patterns
    """

    name = "claude_code"
    description = (
        "Delegate code investigation or bug fixing to Claude Code. "
        "Use mode='investigate' to analyze and report findings (read-only), "
        "or mode='fix' to attempt a fix (requires admin approval)."
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
                    "'investigate' = read-only analysis, "
                    "'fix' = make changes (requires approval)."
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
            "approved_by": {
                "type": "string",
                "description": ("Admin user ID who approved fix mode. " "Required for mode='fix'."),
            },
        },
        "required": ["task", "repo_path", "mode"],
    }

    def __init__(
        self,
        timeout_seconds: int = 600,
        require_fix_approval: bool = True,
        workspace_base: Path | None = None,
    ) -> None:
        """Initialize with configurable timeout and security settings.

        Args:
            timeout_seconds: Max execution time before killing process.
            require_fix_approval: If True, fix mode requires approved_by.
            workspace_base: Base directory for workspaces (default: /tmp/agent-workspaces).
        """
        self.timeout_seconds = timeout_seconds
        self.require_fix_approval = require_fix_approval
        self.workspace_base = workspace_base or Path("/tmp/agent-workspaces")  # noqa: S108

    def _sanitize_task(self, task: str) -> tuple[str, list[str]]:
        """Remove potentially dangerous patterns from task description.

        Args:
            task: The task description to sanitize.

        Returns:
            Tuple of (sanitized_task, list_of_blocked_patterns).
        """
        sanitized = task
        blocked = []

        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, sanitized, re.IGNORECASE):
                LOGGER.warning("Blocked dangerous pattern in task: %s", pattern)
                blocked.append(pattern)
                sanitized = re.sub(pattern, "[BLOCKED]", sanitized, flags=re.IGNORECASE)

        return sanitized, blocked

    def _validate_repo_path(self, repo_path: str, context_id: UUID | None) -> Path | str:
        """Validate repo path is within allowed workspace.

        SECURITY: Prevents path traversal attacks by ensuring:
        1. Path exists and is a git repository
        2. Path is within the workspace_base directory
        3. If context_id provided, path is within that context's directory

        Args:
            repo_path: Path to validate.
            context_id: Optional context ID for additional isolation.

        Returns:
            Validated Path object, or error string if invalid.
        """
        try:
            repo_dir = Path(repo_path).resolve()
        except (ValueError, OSError) as e:
            return f"Error: Invalid path: {e}"

        # Must exist
        if not repo_dir.exists():
            return f"Error: Repository path does not exist: {repo_path}"

        # Must be a git repository
        if not (repo_dir / ".git").exists():
            return f"Error: Not a git repository: {repo_path}"

        # Must be within workspace base directory (prevent path traversal)
        try:
            repo_dir.relative_to(self.workspace_base)
        except ValueError:
            LOGGER.error(
                "SECURITY: Path traversal attempt blocked: %s (not under %s)",
                repo_path,
                self.workspace_base,
            )
            return "Error: Repository must be within workspace directory"

        # If context_id provided, must be in that context's directory
        if context_id:
            context_dir = self.workspace_base / str(context_id)
            try:
                repo_dir.relative_to(context_dir)
            except ValueError:
                LOGGER.error(
                    "SECURITY: Cross-context access blocked: context=%s path=%s",
                    context_id,
                    repo_path,
                )
                return "Error: Repository not in your context workspace"

        return repo_dir

    async def run(
        self,
        task: str,
        repo_path: str,
        mode: str,
        context: str | None = None,
        files_hint: list[str] | None = None,
        approved_by: str | None = None,
        context_id: UUID | None = None,  # Injected by executor
    ) -> str:
        """Run Claude Code to investigate or fix a code issue.

        Args:
            task: Description of the task.
            repo_path: Path to the repository.
            mode: 'investigate' or 'fix'.
            context: Additional context (stack traces, etc.).
            files_hint: Files likely related to the issue.
            approved_by: Admin ID who approved (required for fix mode).
            context_id: Context ID for workspace isolation.

        Returns:
            Claude Code's findings or fix summary.
        """
        # Sanitize task description
        task, blocked_patterns = self._sanitize_task(task)
        if blocked_patterns:
            LOGGER.warning("Task contained blocked patterns: %s", blocked_patterns)

        # Validate repo path (includes traversal protection)
        result = self._validate_repo_path(repo_path, context_id)
        if isinstance(result, str):  # Error message
            return result
        repo_dir = result

        # Fix mode requires explicit approval
        if mode == "fix":
            if self.require_fix_approval and not approved_by:
                return (
                    "Error: Fix mode requires admin approval. "
                    "Use mode='investigate' for read-only analysis, or "
                    "obtain approval via the admin portal first."
                )
            LOGGER.info(
                "Fix mode APPROVED by '%s' for repo: %s",
                approved_by,
                repo_path,
            )

        # Build the prompt
        prompt = self._build_prompt(task, mode, context, files_hint)

        LOGGER.info(
            "Running Claude Code in %s mode on %s",
            mode,
            repo_path,
        )

        # Run claude CLI with appropriate security constraints
        try:
            result = await self._run_claude(prompt, repo_dir, mode)
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
                "## Investigation Task (READ-ONLY)\n\n"
                "IMPORTANT: You are in READ-ONLY mode. Do NOT make any file changes.\n"
                "Only use read operations (Read, Glob, Grep, git log/diff/show).\n\n"
                "Analyze this codebase to understand and report on the issue below:\n"
                "1. Root cause analysis\n"
                "2. Affected files and functions\n"
                "3. Recommended fix approach\n"
                "4. Potential risks or side effects\n"
            )
        else:  # fix
            parts.append(
                "## Fix Task (APPROVED)\n\n"
                "This fix has been explicitly approved. You may make changes.\n"
                "After fixing:\n"
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

    async def _run_claude(self, prompt: str, cwd: Path, mode: str) -> str:
        """Execute claude CLI with security constraints.

        SECURITY CHANGES from original:
        - REMOVED --dangerously-skip-permissions entirely
        - Investigate mode uses --allowlist for read-only tools only
        - Fix mode uses Claude Code's normal permission system (prompts user)
        - Environment explicitly unsets dangerous flags
        """
        cmd = ["claude", "--print", "-p", prompt]

        # Add tool restrictions based on mode
        if mode == "investigate":
            # Read-only mode: only allow safe read tools
            for tool in INVESTIGATE_ALLOWED_TOOLS:
                cmd.extend(["--allowlist", tool])
        # Fix mode: Let Claude Code's normal permission system work
        # User will see prompts for any dangerous operations

        # Build safe environment (inherit minimal env, unset dangerous vars)
        safe_env = {
            "HOME": os.environ.get("HOME", "/tmp"),  # noqa: S108
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "CI": "true",
            "CLAUDE_CODE_ENTRYPOINT": "agent-tool",
            # Explicitly unset dangerous bypass flags
            "CLAUDE_DANGEROUS_SKIP_PERMISSIONS": "",
            "CLAUDE_CODE_SKIP_PERMISSIONS": "",
        }

        LOGGER.debug("Running claude with cmd: %s", " ".join(cmd[:4]) + " ...")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=safe_env,
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
            await process.wait()  # Clean up zombie process
            return (
                f"Error: Claude Code timed out after {self.timeout_seconds} seconds. "
                "The task may be too complex or stuck."
            )
