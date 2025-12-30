import os
from pathlib import Path

from sqlalchemy import select

from core.db.engine import AsyncSessionLocal
from core.db.models import Context
from core.providers import get_code_indexer_factory
from core.tools.base import Tool, ToolError


class PinFileTool(Tool):
    """
    Pins a file to the active context (System Prompt).
    """

    name = "pin_file"
    description = (
        "Pin a file to the active context so it is always available to the agent. "
        "Args: filepath (str)"
    )

    async def run(self, filepath: str) -> str:
        path = Path(filepath).resolve()
        if not path.exists():
            raise ToolError(f"File not found: {filepath}")

        # Determine Context (Simplification: Get first context or match cwd)
        # Ideally this should be passed from the Agent state, but tools are stateless.
        # We assume the 'primary' context is what we want.

        async with AsyncSessionLocal() as session:
            # Find context that matches current working directory or just the first one
            # We used 'default_cwd' in data model
            stmt = select(Context).limit(1)
            result = await session.execute(stmt)
            context = result.scalar_one_or_none()

            if not context:
                # Fallback: Create a default context if none exists?
                # Or raise error. raising error seems safer.
                raise ToolError("No active context found in database.")

            pinned = list(context.pinned_files) if context.pinned_files else []
            s_path = str(path)

            if s_path in pinned:
                return f"File already pinned: {filepath}"

            pinned.append(s_path)
            context.pinned_files = pinned
            session.add(context)
            await session.commit()

            return f"Pinned file: {filepath}"


class UnpinFileTool(Tool):
    """
    Unpins a file from the active context.
    """

    name = "unpin_file"
    description = "Unpin a file from the active context. Args: filepath (str)"

    async def run(self, filepath: str) -> str:
        path = Path(filepath).resolve()
        s_path = str(path)

        async with AsyncSessionLocal() as session:
            stmt = select(Context).limit(1)
            result = await session.execute(stmt)
            context = result.scalar_one_or_none()

            if not context:
                raise ToolError("No active context found.")

            pinned = list(context.pinned_files) if context.pinned_files else []

            if s_path not in pinned:
                # Try relative path matching?
                return f"File was not pinned: {filepath}"

            pinned.remove(s_path)
            context.pinned_files = pinned
            session.add(context)
            await session.commit()

            return f"Unpinned file: {filepath}"


class IndexCodebaseTool(Tool):
    """
    Triggers checking and indexing of the codebase.
    """

    name = "index_codebase"
    description = (
        "Scans the codebase and indexes semantic chunks for search. "
        "Use this if you cannot find code usage."
    )

    async def run(self) -> str:
        root = Path(os.getcwd())
        indexer_class = get_code_indexer_factory()
        indexer = indexer_class(root)
        try:
            await indexer.scan_and_index()
            return "Indexing completed successfully."
        except Exception as e:
            raise ToolError(f"Indexing failed: {e}") from e
        finally:
            await indexer.close()
