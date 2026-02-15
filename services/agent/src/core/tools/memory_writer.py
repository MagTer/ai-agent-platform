"""Memory writer tool for context-scoped persistent facts."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.context.files import ensure_context_directories
from core.db.models import Context
from core.tools.base import Tool

LOGGER = logging.getLogger(__name__)

# Memory size cap (16KB ~= 4000 tokens) prevents context window bloat
MAX_MEMORY_SIZE_BYTES = 16 * 1024  # 16KB


class MemoryWriterTool(Tool):
    """Append facts or insights to the context's memory file.

    Creates/appends to a designated memory file in the context data directory.
    Auto-pins the file if not already pinned.

    Attributes:
        name: Tool identifier.
        description: Human-readable description for LLM.
        category: Tool category.
    """

    name = "update_memory"
    description = (
        "Append a fact, insight, or learning to the context's persistent memory file. "
        "Use this to remember important information across conversations."
    )
    category = "memory"

    async def run(
        self,
        content: str,
        context_id: UUID | None = None,
        session: AsyncSession | None = None,
        **kwargs: Any,
    ) -> str:
        """Append content to the memory file.

        Args:
            content: Content to append to memory.
            context_id: Context UUID (injected by agent).
            session: Database session (injected by agent).
            **kwargs: Additional arguments (ignored).

        Returns:
            Success message or error description.
        """
        if not context_id:
            return "Error: context_id is required for memory updates."

        if not session:
            return "Error: database session is required for memory updates."

        if not content or not content.strip():
            return "Error: content cannot be empty."

        # Get context from database
        stmt = select(Context).where(Context.id == context_id)
        result = await session.execute(stmt)
        context = result.scalar_one_or_none()

        if not context:
            return f"Error: Context {context_id} not found."

        # Get memory file name from context config or use default
        memory_file_name = context.config.get("memory_file", "memory.md")

        # Ensure context directories exist
        context_dir = ensure_context_directories(context_id)
        memory_file_path = context_dir / "files" / memory_file_name

        # Check size cap BEFORE appending
        current_size = 0
        if await asyncio.to_thread(memory_file_path.exists):
            stat = await asyncio.to_thread(memory_file_path.stat)
            current_size = stat.st_size

        # Calculate new size (current + newline + content)
        content_to_append = f"\n{content.strip()}\n"
        new_size = current_size + len(content_to_append.encode("utf-8"))

        if new_size > MAX_MEMORY_SIZE_BYTES:
            return (
                f"Error: Memory file would exceed size cap ({MAX_MEMORY_SIZE_BYTES} bytes). "
                f"Current size: {current_size} bytes. "
                f"Please summarize or compact the memory file before adding more content."
            )

        # Append to memory file
        try:
            # Create file if it doesn't exist
            if not await asyncio.to_thread(memory_file_path.exists):
                await asyncio.to_thread(memory_file_path.write_text, "", encoding="utf-8")
                LOGGER.info(f"Created memory file: {memory_file_path}")

            # Append content
            await asyncio.to_thread(
                memory_file_path.write_text,
                await asyncio.to_thread(memory_file_path.read_text, encoding="utf-8")
                + content_to_append,
                encoding="utf-8",
            )

            LOGGER.info(f"Appended to memory file: {memory_file_path}")

            # Auto-pin if not already pinned
            memory_file_absolute = str(memory_file_path.resolve())
            if memory_file_absolute not in context.pinned_files:
                context.pinned_files = list(context.pinned_files) + [memory_file_absolute]
                await session.flush()
                LOGGER.info(f"Auto-pinned memory file: {memory_file_absolute}")

            return (
                f"Memory updated successfully. "
                f"File size: {new_size} / {MAX_MEMORY_SIZE_BYTES} bytes."
            )

        except Exception as e:
            LOGGER.exception("Failed to update memory file")
            return f"Error: Failed to update memory - {e}"


__all__ = ["MemoryWriterTool"]
