# ruff: noqa: S603
"""Shared helpers for stack management commands."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx

from .utils import PROJECT_ROOT


class CommandError(RuntimeError):
    """Raised when an underlying command exits with a failure."""


REPO_MARKER = "docker-compose.yml"


def resolve_repo_root(start: Path | None = None, marker: str = REPO_MARKER) -> Path:
    """Return the repository root by walking upwards for ``marker``."""

    directory = (start or PROJECT_ROOT).resolve()
    for _ in range(10):
        candidate = directory / marker
        if candidate.exists():
            return candidate.parent
        if directory.parent == directory:
            break
        directory = directory.parent
    raise FileNotFoundError(f"Could not find {marker} upwards from {start or PROJECT_ROOT}")


def ensure_program(name: str) -> None:
    """Ensure an executable exists on ``PATH``."""

    if shutil.which(name) is None:  # type: ignore[name-defined]
        raise FileNotFoundError(f"Required executable not found in PATH: {name}")


def run_command(  # noqa: S603
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture_output: bool = True,
    text: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and optionally capture output."""

    result = subprocess.run(  # noqa: S603
        list(args),
        cwd=str(cwd) if cwd else None,
        check=False,
        capture_output=capture_output,
        text=text,
        env=env,
    )
    if check and result.returncode != 0:
        quoted = " ".join(map(shlex.quote, args))
        raise CommandError(f"Command failed ({result.returncode}): {quoted}\n{result.stderr}")
    return result


GitPrinter = Callable[[Sequence[str]], None]


def _default_git_printer(args: Sequence[str]) -> None:
    quoted = " ".join(shlex.quote(arg) for arg in args)
    print(f"$ git {quoted}")


def run_git_command(
    args: Sequence[str],
    *,
    repo_root: Path | None = None,
    printer: GitPrinter | None = _default_git_printer,
    **kwargs,
) -> subprocess.CompletedProcess[str]:
    """Run a git command relative to the repository and optionally emit it."""

    if printer is not None:
        printer(args)
    return run_command(["git", *args], cwd=repo_root, **kwargs)


def _noop_git_printer(args: Sequence[str]) -> None:
    """No-op printer for quiet git invocations."""


def current_branch(repo_root: Path) -> str | None:
    """Return the name of the currently checked-out branch, if any."""

    try:
        result = run_git_command(
            ["rev-parse", "--abbrev-ref", "HEAD"],
            repo_root=repo_root,
            printer=_noop_git_printer,
        )
    except CommandError:
        return None
    branch = result.stdout.strip()
    return branch if branch else None


def branch_exists(repo_root: Path, branch: str) -> bool:
    """Return True when ``branch`` exists locally."""

    result = run_command(
        ["git", "show-ref", "--verify", f"refs/heads/{branch}"],
        cwd=repo_root,
        check=False,
    )
    return result.returncode == 0


def ensure_branch(
    repo_root: Path,
    branch: str,
    *,
    printer: GitPrinter | None = _default_git_printer,
) -> str:
    """Ensure the requested branch is checked out."""

    if not branch:
        raise ValueError("Branch name must be provided.")

    current = current_branch(repo_root)
    if current == branch:
        return branch
    if branch_exists(repo_root, branch):
        run_git_command(["checkout", branch], repo_root=repo_root, printer=printer)
    else:
        run_git_command(["checkout", "-b", branch], repo_root=repo_root, printer=printer)
    return branch


def ensure_docker() -> None:
    """Raise when Docker is unavailable."""

    ensure_program("docker")


def ensure_directory(path: Path) -> None:
    """Create ``path`` when it does not exist."""

    path.mkdir(parents=True, exist_ok=True)


def docker_exec(
    container: str,
    *command: str,
    user: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute ``command`` inside ``container`` using ``docker exec``."""

    args = ["docker", "exec"]
    if user:
        args.extend(["-u", user])
    args.append(container)
    args.extend(command)
    return run_command(args)


def docker_cp(src: str, dest: str) -> None:
    """Copy files between the host and a container."""

    run_command(["docker", "cp", src, dest])


def ensure_container_exists(container: str) -> None:
    """Raise when ``container`` is not known to Docker."""

    run_command(["docker", "inspect", container, "--format", "{{.Name}}"], capture_output=True)


def get_mapped_port(container: str, internal_port: int) -> int:
    """Return the host port mapped to ``container:internal_port``."""

    try:
        result = run_command(
            ["docker", "port", container, f"{internal_port}/tcp"],
            capture_output=True,
        )
    except CommandError:
        return internal_port
    output = (result.stdout or "").strip()
    if not output:
        return internal_port
    token = output.split()[-1]
    if ":" in token:
        token = token.rsplit(":", 1)[-1]
    try:
        return int(token)
    except ValueError:
        return internal_port


def wait_http_ok(url: str, timeout: float) -> bool:
    """Poll ``url`` until a 2xx response is returned or timeout expires."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=3.0)
            if 200 <= response.status_code < 300:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(2.0)
    return False


def ensure_models(models: Sequence[str]) -> None:
    """Ensure the given Ollama models are pulled inside the ``ollama`` container."""

    for model in models:
        quoted = shlex.quote(model)
        command = f"if ! ollama list | grep -q {quoted}; then ollama pull {quoted}; fi"
        run_command(  # noqa: S603
            [
                "docker",
                "exec",
                "ollama",
                "/bin/sh",
                "-lc",
                command,
            ]
        )


def read_models_file(repo_root: Path) -> list[str] | None:
    """Return models declared in ``config/models.txt`` when present."""

    config_file = repo_root / "config" / "models.txt"
    if not config_file.exists():
        return None
    models: list[str] = []
    for line in config_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        models.append(stripped)
    return models or None


def git_available() -> bool:
    """Return True when ``git`` can be located."""

    return shutil.which("git") is not None  # type: ignore[name-defined]


def stage_and_commit(
    repo_root: Path,
    message: str,
    *,
    printer: GitPrinter | None = _default_git_printer,
) -> str | None:
    """Stage all files and create a timestamped commit when required."""

    run_git_command(["add", "-A"], repo_root=repo_root, printer=printer, capture_output=True)
    status = run_command(["git", "status", "--porcelain"], cwd=repo_root)
    if not status.stdout.strip():
        return None
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    final_message = f"{message} ({timestamp})"
    run_git_command(["commit", "-m", final_message], repo_root=repo_root, printer=printer)
    return final_message


def tail_logs(services: Sequence[str], since: str = "5m") -> None:
    """Stream docker logs for ``services``."""

    for service in services:
        run_command(
            ["docker", "logs", "-f", "--since", since, service],
            capture_output=False,
            check=False,
        )


def ensure_secrets(env: dict[str, str]) -> None:
    """Validate secrets required by stack services."""

    missing: list[str] = []
    if not env.get("OPENWEBUI_SECRET"):
        missing.append("OPENWEBUI_SECRET")
    if not env.get("SEARXNG_SECRET"):
        missing.append("SEARXNG_SECRET")
    if missing:
        formatted = ", ".join(missing)
        raise RuntimeError(f"Missing required secrets in .env: {formatted}")


__all__ = [
    "CommandError",
    "docker_cp",
    "docker_exec",
    "ensure_container_exists",
    "ensure_directory",
    "ensure_docker",
    "ensure_models",
    "ensure_program",
    "ensure_secrets",
    "get_mapped_port",
    "git_available",
    "read_models_file",
    "resolve_repo_root",
    "run_command",
    "run_git_command",
    "current_branch",
    "branch_exists",
    "ensure_branch",
    "stage_and_commit",
    "tail_logs",
    "wait_http_ok",
]
