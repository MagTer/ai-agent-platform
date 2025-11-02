"""Utility helpers shared by the stack CLI modules."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"
DEFAULT_PROJECT_NAME = os.getenv("STACK_PROJECT_NAME", "ai-agent-stack")


def load_environment(env_path: Path | None = None) -> dict[str, str]:
    """Load the environment variables combining OS env with a .env file."""

    env_path = env_path or DEFAULT_ENV_PATH
    file_values = dotenv_values(env_path) if env_path.exists() else {}
    merged = {**file_values, **os.environ}
    return {key: str(value) for key, value in merged.items() if value is not None}


__all__ = [
    "PROJECT_ROOT",
    "DEFAULT_ENV_PATH",
    "DEFAULT_COMPOSE_FILE",
    "DEFAULT_PROJECT_NAME",
    "load_environment",
]
