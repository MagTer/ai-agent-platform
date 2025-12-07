"""Utility helpers shared by the stack CLI modules."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values

COMPOSE_FILES_ENV = "STACK_COMPOSE_FILES"
PROJECT_NAME_ENV = "STACK_PROJECT_NAME"

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"
DEFAULT_PROJECT_NAME = "ai-agent-stack"


def load_environment(env_path: Path | None = None) -> dict[str, str]:
    """Load the environment variables combining OS env with a .env file."""

    env_path = env_path or DEFAULT_ENV_PATH
    file_values = dotenv_values(env_path) if env_path.exists() else {}
    
    merged = os.environ.copy()
    
    # Overlay values from .env
    for key, val in file_values.items():
        # If the key is missing in env, or exists but is empty, take from file
        # (Prefer file value over empty environment variable to avoid accidental blanking)
        if key not in merged or not merged[key]:
            merged[key] = val
            
    return {key: str(value) for key, value in merged.items() if value is not None}


def resolve_compose_files(env: Mapping[str, str] | None = None) -> list[Path]:
    """Return the compose files to apply, honouring overrides from the environment."""

    if env is None:
        env = os.environ
    raw = env.get(COMPOSE_FILES_ENV)
    if not raw:
        return [DEFAULT_COMPOSE_FILE]

    try:
        raw_str = os.fspath(raw)
    except TypeError:
        raw_str = str(raw)

    files: list[Path] = []
    for chunk in raw_str.split(os.pathsep):
        candidate = chunk.strip()
        if not candidate:
            continue
        path = Path(candidate)
        if not path.is_absolute():
            path = (PROJECT_ROOT / candidate).resolve()
        files.append(path)

    default_resolved = DEFAULT_COMPOSE_FILE.resolve()
    if all(path.resolve() != default_resolved for path in files):
        files.insert(0, DEFAULT_COMPOSE_FILE)

    return files


def resolve_project_name(env: Mapping[str, str] | None = None) -> str:
    """Return the compose project name, falling back to the default."""

    if env is None:
        env = os.environ
    value = env.get(PROJECT_NAME_ENV)
    if value is None:
        return DEFAULT_PROJECT_NAME

    # Treat empty or whitespace-only overrides as unset so docker compose receives a
    # valid project name even if the variable exists in the environment without a
    # concrete value (for example, when declared but left blank in ``.env``).
    value_str = str(value).strip()
    if not value_str:
        return DEFAULT_PROJECT_NAME

    return value_str


__all__ = [
    "PROJECT_ROOT",
    "DEFAULT_ENV_PATH",
    "DEFAULT_COMPOSE_FILE",
    "DEFAULT_PROJECT_NAME",
    "PROJECT_NAME_ENV",
    "COMPOSE_FILES_ENV",
    "load_environment",
    "resolve_compose_files",
    "resolve_project_name",
]
