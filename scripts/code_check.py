"""Run local quality checks to mirror the CI pipeline."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    name: str
    args: Sequence[str]


COMMANDS: tuple[Command, ...] = (
    Command("Ruff (lint + fix)", ("ruff", "check", "--fix", ".")),
    Command(
        "Black (format)",
        (
            "black",
            "services/agent/src",
            "tests",
            "services/fetcher",
            "services/indexer",
            "services/ragproxy",
            "services/embedder",
            "scripts",
        ),
    ),
    Command("Mypy (type check)", ("mypy", "services/agent/src")),
    Command("Pytest", ("pytest",)),
)


def run_command(command: Command) -> None:
    """Execute a single command and exit immediately on failure."""
    print(f"\n==> {command.name}")
    print("$", " ".join(command.args))
    result = subprocess.run(command.args, check=False)  # noqa: S603
    if result.returncode != 0:
        print(f"\n{command.name} failed with exit code {result.returncode}.")
        print("Aborting local quality run.")
        sys.exit(result.returncode)
    print(f"{command.name} completed successfully.")


def main() -> None:
    for command in COMMANDS:
        run_command(command)
    print("\nAll quality checks completed successfully.")


if __name__ == "__main__":
    main()
