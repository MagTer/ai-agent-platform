"""Per-context file management utilities.

Provides path helpers for context-isolated file storage.
Each context has isolated directories for pinned files and skills.
"""

import os
from pathlib import Path
from uuid import UUID

# Base directory for all context data (configurable via env)
CONTEXT_DATA_BASE = Path(os.getenv("CONTEXT_DATA_DIR", "data/contexts"))


def get_context_dir(context_id: UUID) -> Path:
    """Return the base directory for a context's files.

    Resolves to absolute path to ensure consistency in Docker environments.

    Args:
        context_id: UUID of the context.

    Returns:
        Absolute path to the context directory.
    """
    return CONTEXT_DATA_BASE.resolve() / str(context_id)


def ensure_context_directories(context_id: UUID) -> Path:
    """Ensure context directory structure exists.

    Creates:
    - data/contexts/{context_id}/files/  (pinned files)
    - data/contexts/{context_id}/skills/ (per-context skills)

    Args:
        context_id: UUID of the context.

    Returns:
        Absolute path to the context base directory.
    """
    base = get_context_dir(context_id)
    (base / "files").mkdir(parents=True, exist_ok=True)
    (base / "skills").mkdir(parents=True, exist_ok=True)
    return base


__all__ = ["CONTEXT_DATA_BASE", "get_context_dir", "ensure_context_directories"]
