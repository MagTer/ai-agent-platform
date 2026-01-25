"""Helper functions to interact with Docker Compose."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from pathlib import Path

from stack import tooling
from stack.utils import (
    load_environment,
    resolve_compose_files,
    resolve_project_name,
    resolve_project_name_for_env,
)

Pathish = os.PathLike[str] | str


class ComposeError(RuntimeError):
    """Raised when a compose command fails."""


def _compose_command(
    args: Iterable[str],
    env: dict[str, str] | None = None,
    extra_files: Iterable[Pathish | None] | None = None,
    files_override: Iterable[Pathish | None] | None = None,
    prod: bool = False,
    dev: bool = False,
) -> list[str]:
    if files_override is None:
        files = resolve_compose_files(env, prod=prod, dev=dev)
    else:
        files = [Path(f) for f in files_override if f is not None]
    if extra_files:
        files.extend(Path(f) for f in extra_files if f is not None)
    command = ["docker", "compose"]
    for compose_file in files:
        command.extend(["-f", compose_file.as_posix()])
    # Use environment-specific project name if prod/dev specified
    if prod or dev:
        project_name = resolve_project_name_for_env(prod=prod, dev=dev)
    else:
        project_name = resolve_project_name(env)
    command.extend(["-p", project_name])
    command.extend(args)
    return command


def run_compose(
    args: Iterable[str],
    *,
    extra_files: Iterable[Pathish | None] | None = None,
    files_override: Iterable[Pathish | None] | None = None,
    env_override: dict[str, str] | None = None,
    capture_output: bool = True,
    prod: bool = False,
    dev: bool = False,
) -> subprocess.CompletedProcess[str | bytes]:
    """Execute a docker compose command and return the completed process.

    Args:
        args: Command arguments to pass to docker compose
        extra_files: Additional compose files to include
        files_override: Override the default compose file resolution
        env_override: Additional environment variables
        capture_output: Whether to capture stdout/stderr
        prod: If True, use production compose file (docker-compose.prod.yml)
        dev: If True, use development compose file (docker-compose.dev.yml)
    """
    env = load_environment()
    if env_override:
        env.update(env_override)
    command = _compose_command(list(args), env, extra_files, files_override, prod=prod, dev=dev)
    try:
        return tooling.run_command(
            command,
            check=True,
            env=env,
            capture_output=capture_output,
            text=False,
        )
    except subprocess.CalledProcessError as exc:  # pragma: no cover - integration failure
        raise ComposeError(exc.stderr.decode("utf-8")) from exc


def compose_up(
    *,
    detach: bool = True,
    build: bool = False,
    extra_files: Iterable[Pathish | None] | None = None,
    prod: bool = False,
    dev: bool = False,
) -> None:
    """Bring up the stack.

    Args:
        detach: Run in background
        build: Build images before starting
        extra_files: Additional compose files to include
        prod: If True, use production compose file
        dev: If True, use development compose file
    """
    args = ["up"]
    if detach:
        args.append("-d")
    if build:
        args.append("--build")
    run_compose(args, extra_files=extra_files, capture_output=False, prod=prod, dev=dev)


def compose_down(
    remove_volumes: bool = False,
    *,
    extra_files: Iterable[Pathish | None] | None = None,
    prod: bool = False,
    dev: bool = False,
) -> None:
    """Tear down the stack.

    Args:
        remove_volumes: Remove persistent volumes
        extra_files: Additional compose files to include
        prod: If True, use production compose file
        dev: If True, use development compose file
    """
    args = ["down", "--remove-orphans"]
    if remove_volumes:
        args.append("--volumes")
    run_compose(args, extra_files=extra_files, prod=prod, dev=dev)


def compose_logs(
    services: list[str] | None = None,
    tail: int = 50,
    *,
    extra_files: Iterable[Pathish | None] | None = None,
) -> str:
    """Return the latest logs for the provided services."""

    args = ["logs", f"--tail={tail}"]
    if services:
        args.extend(services)
    result = run_compose(args, extra_files=extra_files)
    stdout = result.stdout
    if isinstance(stdout, bytes):
        return stdout.decode("utf-8")
    return stdout


__all__ = [
    "ComposeError",
    "compose_down",
    "compose_logs",
    "compose_up",
    "run_compose",
]
