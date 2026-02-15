"""Tests for memory writer tool."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from core.context.files import ensure_context_directories
from core.db.models import Context
from core.tools.memory_writer import MAX_MEMORY_SIZE_BYTES, MemoryWriterTool


@pytest.mark.asyncio
async def test_memory_writer_creates_file() -> None:
    """Test that memory writer creates file if it doesn't exist."""
    context_id = uuid4()
    context = Context(
        id=context_id, name="test-context", type="general", config={}, pinned_files=[]
    )

    # Mock session
    session = AsyncMock()
    session.execute = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = context
    session.execute.return_value = result_mock
    session.flush = AsyncMock()

    # Ensure directories exist
    ensure_context_directories(context_id)

    tool = MemoryWriterTool()
    result = await tool.run(
        content="Test memory entry",
        context_id=context_id,
        session=session,
    )

    assert "Memory updated successfully" in result
    assert "bytes" in result

    # Verify file was created
    memory_path = Path("data/contexts").resolve() / str(context_id) / "files" / "memory.md"
    assert memory_path.exists()
    content_read = memory_path.read_text(encoding="utf-8")
    assert "Test memory entry" in content_read

    # Cleanup
    memory_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_memory_writer_appends_to_existing_file() -> None:
    """Test that memory writer appends to existing file."""
    context_id = uuid4()
    context = Context(
        id=context_id, name="test-context", type="general", config={}, pinned_files=[]
    )

    # Mock session
    session = AsyncMock()
    session.execute = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = context
    session.execute.return_value = result_mock
    session.flush = AsyncMock()

    # Create initial memory file
    ensure_context_directories(context_id)
    memory_path = Path("data/contexts").resolve() / str(context_id) / "files" / "memory.md"
    memory_path.write_text("Initial memory\n", encoding="utf-8")

    tool = MemoryWriterTool()
    result = await tool.run(
        content="Second entry",
        context_id=context_id,
        session=session,
    )

    assert "Memory updated successfully" in result

    # Verify content was appended
    content_read = memory_path.read_text(encoding="utf-8")
    assert "Initial memory" in content_read
    assert "Second entry" in content_read

    # Cleanup
    memory_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_memory_writer_auto_pins_file() -> None:
    """Test that memory writer auto-pins the memory file."""
    context_id = uuid4()
    context = Context(
        id=context_id, name="test-context", type="general", config={}, pinned_files=[]
    )

    # Mock session
    session = AsyncMock()
    session.execute = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = context
    session.execute.return_value = result_mock
    session.flush = AsyncMock()

    ensure_context_directories(context_id)

    tool = MemoryWriterTool()
    await tool.run(
        content="Test memory entry",
        context_id=context_id,
        session=session,
    )

    # Verify file was pinned (context.pinned_files was modified)
    assert len(context.pinned_files) == 1
    assert "memory.md" in context.pinned_files[0]

    # Cleanup
    memory_path = Path("data/contexts").resolve() / str(context_id) / "files" / "memory.md"
    memory_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_memory_writer_respects_custom_memory_file_name() -> None:
    """Test that memory writer uses custom memory file name from context config."""
    context_id = uuid4()
    context = Context(
        id=context_id,
        name="test-context",
        type="general",
        config={"memory_file": "custom_memory.md"},
        pinned_files=[],
    )

    # Mock session
    session = AsyncMock()
    session.execute = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = context
    session.execute.return_value = result_mock
    session.flush = AsyncMock()

    ensure_context_directories(context_id)

    tool = MemoryWriterTool()
    await tool.run(
        content="Test memory entry",
        context_id=context_id,
        session=session,
    )

    # Verify file was created with custom name
    custom_memory_path = (
        Path("data/contexts").resolve() / str(context_id) / "files" / "custom_memory.md"
    )
    assert custom_memory_path.exists()
    content_read = custom_memory_path.read_text(encoding="utf-8")
    assert "Test memory entry" in content_read

    # Cleanup
    custom_memory_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_memory_writer_enforces_size_cap() -> None:
    """Test that memory writer rejects appends that exceed size cap."""
    context_id = uuid4()
    context = Context(
        id=context_id, name="test-context", type="general", config={}, pinned_files=[]
    )

    # Mock session
    session = AsyncMock()
    session.execute = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = context
    session.execute.return_value = result_mock
    session.flush = AsyncMock()

    ensure_context_directories(context_id)
    memory_path = Path("data/contexts").resolve() / str(context_id) / "files" / "memory.md"

    # Create a file close to the size cap
    large_content = "x" * (MAX_MEMORY_SIZE_BYTES - 100)
    memory_path.write_text(large_content, encoding="utf-8")

    tool = MemoryWriterTool()

    # Try to append content that would exceed cap
    result = await tool.run(
        content="y" * 200,
        context_id=context_id,
        session=session,
    )

    assert "Error" in result
    assert "exceed size cap" in result
    assert str(MAX_MEMORY_SIZE_BYTES) in result

    # Cleanup
    memory_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_memory_writer_requires_context_id() -> None:
    """Test that memory writer requires context_id."""
    session = AsyncMock()
    tool = MemoryWriterTool()
    result = await tool.run(
        content="Test",
        context_id=None,
        session=session,
    )

    assert "Error" in result
    assert "context_id is required" in result


@pytest.mark.asyncio
async def test_memory_writer_requires_session() -> None:
    """Test that memory writer requires database session."""
    tool = MemoryWriterTool()
    result = await tool.run(
        content="Test",
        context_id=uuid4(),
        session=None,
    )

    assert "Error" in result
    assert "database session is required" in result


@pytest.mark.asyncio
async def test_memory_writer_rejects_empty_content() -> None:
    """Test that memory writer rejects empty content."""
    context_id = uuid4()
    context = Context(
        id=context_id, name="test-context", type="general", config={}, pinned_files=[]
    )

    # Mock session
    session = AsyncMock()
    session.execute = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = context
    session.execute.return_value = result_mock
    session.flush = AsyncMock()

    tool = MemoryWriterTool()
    result = await tool.run(
        content="   ",
        context_id=context_id,
        session=session,
    )

    assert "Error" in result
    assert "cannot be empty" in result
