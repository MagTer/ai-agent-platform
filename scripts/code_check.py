#!/usr/bin/env python3
"""
Single Source of Truth for running quality checks.
Handles directory switching and environment variables automatically.
"""

import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# Identify directories
REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = REPO_ROOT / "services" / "agent"


@dataclass
class Command:
    name: str
    args: list[str]
    cwd: Path
    extra_env: dict | None = None
    allowed_return_codes: Sequence[int] = (0,)


def get_commands() -> list[Command]:
    # Common environment for agent-related commands to find 'scripts' module etc.
    agent_env = os.environ.copy()
    agent_env["PYTHONPATH"] = str(REPO_ROOT)

    return [
        Command(
            name="Ruff (lint + fix)",
            args=[
                "poetry",
                "run",
                "ruff",
                "check",
                "--fix",
                "src",
                "../embedder",
                "../fetcher",
                "../indexer",
                "../ragproxy",
                "../../tests",
                "../../scripts",
            ],
            cwd=AGENT_DIR,
        ),
        Command(
            name="Black (format)",
            args=[
                "poetry",
                "run",
                "python",
                "-m",
                "black",
                "src",
                "../embedder",
                "../fetcher",
                "../indexer",
                "../ragproxy",
                "../../tests",
                "../../scripts",
            ],
            cwd=AGENT_DIR,
        ),
        Command(
            name="Mypy (type check)",
            args=["poetry", "run", "mypy", "src"],
            cwd=AGENT_DIR,
        ),
        Command(
            name="Pytest",
            args=["poetry", "run", "pytest"],
            cwd=AGENT_DIR,
            extra_env=agent_env,
        ),
        Command(
            name="Dependency Check",
            args=["poetry", "run", "python", "../../scripts/deps_check.py", "--quiet"],
            cwd=AGENT_DIR,
            extra_env=agent_env,
            # Allow exit codes 1, 2, 3 (outdated dependencies) but fail on 4 (error)
            allowed_return_codes=(0, 1, 2, 3),
        ),
    ]


def run_command(command: Command) -> None:
    """Execute a single command and exit immediately on failure."""
    print(f"\n==> {command.name}")
    print(f"CWD: {command.cwd}")
    print("$", " ".join(command.args))

    env = os.environ.copy()
    if command.extra_env:
        env.update(command.extra_env)

    # Flush stdout before running subprocess to ensure order in logs
    sys.stdout.flush()

    try:
        result = subprocess.run(  # noqa: S603
            command.args,
            cwd=command.cwd,
            env=env,
            check=False,
            text=True,
        )

        if result.returncode not in command.allowed_return_codes:
            print(f"\n❌ {command.name} failed with exit code {result.returncode}.")
            sys.exit(result.returncode)

        if result.returncode == 0:
            print(f"✅ {command.name} passed.")
        else:
            print(
                f"⚠️ {command.name} passed with warning (exit code {result.returncode})."
            )

    except FileNotFoundError as e:
        print(f"\n❌ Failed to execute command: {e}")
        print("Ensure that the executable (e.g. poetry) is in your PATH.")
        sys.exit(1)


def main() -> None:
    print(f"Repo Root: {REPO_ROOT}")
    print(f"Agent Dir: {AGENT_DIR}")

    commands = get_commands()
    for command in commands:
        run_command(command)

    print("\n🎉 All quality checks completed successfully.")


if __name__ == "__main__":
    main()
