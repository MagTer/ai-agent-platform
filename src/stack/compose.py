"""Helper functions to interact with Docker Compose."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from pathlib import Path

from . import tooling
from .utils import load_environment, resolve_compose_files, resolve_project_name

Pathish = os.PathLike[str] | str


class ComposeError(RuntimeError):
    """Raised when a compose command fails."""


def _compose_command(
    args: Iterable[str],
    env: dict[str, str] | None = None,
    extra_files: Iterable[Pathish | None] | None = None,
    files_override: Iterable[Pathish | None] | None = None,
) -> list[str]:
    if files_override is None:
        files = resolve_compose_files(env)
    else:
        files = [Path(f) for f in files_override if f is not None]
    if extra_files:
        files.extend(Path(f) for f in extra_files if f is not None)
    command = ["docker", "compose"]
    for compose_file in files:
        command.extend(["-f", compose_file.as_posix()])
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
) -> subprocess.CompletedProcess[bytes]:
    """Execute a docker compose command and return the completed process."""

    env = load_environment()
    if env_override:
        env.update(env_override)
    command = _compose_command(list(args), env, extra_files, files_override)
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
) -> None:
    """Bring up the stack."""

    args = ["up"]
    if detach:
        args.append("-d")
    if build:
        args.append("--build")
    run_compose(args, extra_files=extra_files, capture_output=False)


def compose_down(
    remove_volumes: bool = False,
    *,
    extra_files: Iterable[Pathish | None] | None = None,
) -> None:
    """Tear down the stack."""

    args = ["down", "--remove-orphans"]
    if remove_volumes:
        args.append("--volumes")
    run_compose(args, extra_files=extra_files)


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
    return result.stdout.decode("utf-8")


__all__ = [
    "ComposeError",
    "compose_down",
    "compose_logs",
    "compose_up",
    "run_compose",
]
