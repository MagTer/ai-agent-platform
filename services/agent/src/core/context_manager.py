"""Manager for Agent Contexts (projects/environments)."""

import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.core.config import Settings
from core.db.models import Context

LOGGER = logging.getLogger(__name__)


class ContextManager:
    """Manages the lifecycle and physical storage of Contexts."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._root_dir = settings.contexts_dir
        # Ensure root contexts dir exists
        if not self._root_dir.is_absolute():
            # If relative, make it relative to cwd (service root) using Path.cwd()?
            # Or assume the process CWD is correct.
            # Best to resolve it.
            self._root_dir = Path.cwd() / self._root_dir
        self._root_dir.mkdir(parents=True, exist_ok=True)

    async def create_context(
        self, session: AsyncSession, name: str, type: str, args: dict[str, Any]
    ) -> Context:
        """
        Create a new Context, performing necessary physical setup.

        Args:
            session: DB session.
            name: Unique name for the context (e.g. project name).
            type: 'git', 'local', or 'virtual'.
            args: Configuration args (e.g. 'url' for git).

        Returns:
            The created Context model.
        """
        # 1. Check DB collision
        stmt = select(Context).where(Context.name == name)
        result = await session.execute(stmt)
        if result.scalar_one_or_none():
            raise ValueError(f"Context with name '{name}' already exists.")

        # 2. Determine paths
        context_path = self._root_dir / name
        default_cwd = str(context_path)

        # 3. Physical Setup
        if type == "git":
            repo_url = args.get("url")
            if not repo_url:
                raise ValueError("Git context requires 'url' argument.")
            
            if context_path.exists():
                 # For MVP, if it exists, assume it's valid or raise? 
                 # Let's log warning and reuse if it looks like a repo, else raise.
                 if not (context_path / ".git").exists():
                     raise FileExistsError(f"Path {context_path} exists but is not a git repo.")
                 LOGGER.info(f"Context path {context_path} already exists. Skipping clone.")
            else:
                LOGGER.info(f"Cloning {repo_url} to {context_path}")
                # Ideally use async process. For MVP synchronous is safer to ensure it's ready.
                # We use os.system or subprocess. 
                # Security note: Validate URL or use shlex? 
                # For this internal tool, simple subprocess is okay but risky if args are untrusted.
                # 'args' come from user commands.
                import subprocess
                
                # Check git installed? Assumed.
                try:
                    subprocess.run(
                        ["git", "clone", repo_url, str(context_path)],
                        check=True,
                        capture_output=True,
                        text=True
                    )
                except subprocess.CalledProcessError as e:
                    raise RuntimeError(f"Failed to clone repo: {e.stderr}") from e

        elif type == "local":
            # EXISTING local path
            local_path = args.get("path")
            if not local_path:
                raise ValueError("Local context requires 'path' argument.")
            
            target_path = Path(local_path)
            if not target_path.exists():
                raise FileNotFoundError(f"Local path {target_path} does not exist.")
            
            # Symlink or just point default_cwd?
            # If we point default_cwd to absolute path, we don't need to put it in _root_dir.
            default_cwd = str(target_path)
            # We don't create anything in _root_dir for 'local' type usually, 
            # OR we symlink it so it appears managed.
            # Let's just point to it for now.

        elif type == "virtual":
            # Clean empty directory
            context_path.mkdir(parents=True, exist_ok=True)
            
        else:
            raise ValueError(f"Unknown context type: {type}")

        # 4. Create DB Record
        context = Context(
            name=name,
            type=type,
            config=args,
            default_cwd=default_cwd,
        )
        session.add(context)
        await session.flush()
        
        return context

    async def get_context(self, session: AsyncSession, name: str) -> Context | None:
        """Retrieve a context by name."""
        stmt = select(Context).where(Context.name == name)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_context_by_id(self, session: AsyncSession, context_id: uuid.UUID) -> Context | None:
        """Retrieve a context by ID."""
        return await session.get(Context, context_id)
