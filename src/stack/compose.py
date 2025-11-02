"""Helper functions to interact with Docker Compose."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable

from .utils import DEFAULT_COMPOSE_FILE, DEFAULT_PROJECT_NAME, load_environment


class ComposeError(RuntimeError):
    """Raised when a compose command fails."""


def _compose_command(args: Iterable[str]) -> list[str]:
    return [
        "docker",
        "compose",
        "-f",
        str(DEFAULT_COMPOSE_FILE),
        "-p",
        DEFAULT_PROJECT_NAME,
        *args,
    ]


def run_compose(args: Iterable[str]) -> subprocess.CompletedProcess[bytes]:
    """Execute a docker compose command and return the completed process."""

    command = _compose_command(list(args))
    env = load_environment()
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
