"""Helper functions to interact with Docker Compose."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable

from .utils import DEFAULT_PROJECT_NAME, load_environment, resolve_compose_files


class ComposeError(RuntimeError):
    """Raised when a compose command fails."""


def _compose_command(args: Iterable[str], env: dict[str, str] | None = None) -> list[str]:
    files = resolve_compose_files(env)
    command = ["docker", "compose"]
    for compose_file in files:
        command.extend(["-f", str(compose_file)])
    command.extend(["-p", DEFAULT_PROJECT_NAME])
    command.extend(args)
    return command


def run_compose(args: Iterable[str]) -> subprocess.CompletedProcess[bytes]:
    """Execute a docker compose command and return the completed process."""

    env = load_environment()
    command = _compose_command(list(args), env)
    try:
        return subprocess.run(command, check=True, env=env, capture_output=True)  # noqa: S603
    except subprocess.CalledProcessError as exc:  # pragma: no cover - integration failure
        raise ComposeError(exc.stderr.decode("utf-8")) from exc


def compose_up(detach: bool = True) -> None:
    """Bring up the stack."""

    args = ["up"]
    if detach:
        args.append("-d")
    run_compose(args)


def compose_down(remove_volumes: bool = False) -> None:
    """Tear down the stack."""

    args = ["down", "--remove-orphans"]
    if remove_volumes:
        args.append("--volumes")
    run_compose(args)


def compose_logs(services: list[str] | None = None, tail: int = 50) -> str:
    """Return the latest logs for the provided services."""

    args = ["logs", f"--tail={tail}"]
    if services:
        args.extend(services)
    result = run_compose(args)
    return result.stdout.decode("utf-8")


__all__ = [
    "ComposeError",
    "compose_down",
    "compose_logs",
    "compose_up",
    "run_compose",
]
