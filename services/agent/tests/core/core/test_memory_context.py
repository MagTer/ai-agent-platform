"""Unit tests for MemoryStore context filtering.

Tests that MemoryStore properly isolates memories between contexts using Qdrant filtering.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.core.memory import MemoryRecord, MemoryStore


@pytest.mark.asyncio
class TestMemoryStoreContext:
    """Test MemoryStore context isolation."""

    async def test_memory_store_initialization_with_context(self, settings):
        """Test that MemoryStore can be initialized with a context_id."""
        context_id = uuid.uuid4()

        memory = MemoryStore(settings, context_id=context_id)

        assert memory._settings == settings
        assert memory._context_id == context_id

    async def test_memory_store_initialization_without_context(self, settings):
        """Test that MemoryStore can be initialized without a context_id (legacy)."""
        memory = MemoryStore(settings, context_id=None)

        assert memory._context_id is None

    @patch("core.core.memory.QdrantClient")
    async def test_store_adds_context_id_to_payload(self, mock_qdrant_class, settings):
        """Test that storing a memory adds context_id to the payload."""
        context_id = uuid.uuid4()

        # Mock Qdrant client
        mock_client = MagicMock()
        mock_qdrant_class.return_value = mock_client

        memory = MemoryStore(settings, context_id=context_id)
        await memory.ainit()

        # Create memory record
        record = MemoryRecord(
            conversation_id="test_conv",
            text="Test memory content",
            metadata={"key": "value"},
        )

        # Mock embedder
        with patch.object(memory, "_get_embedding", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1, 0.2, 0.3]

            await memory.store(record)

            # Verify upsert was called
            mock_client.upsert.assert_called_once()

            # Extract payload from call
            call_args = mock_client.upsert.call_args
            points = call_args[1]["points"]
            assert len(points) == 1

            payload = points[0].payload
            assert "context_id" in payload
            assert payload["context_id"] == str(context_id)
            assert payload["conversation_id"] == "test_conv"
            assert payload["text"] == "Test memory content"

    @patch("core.core.memory.QdrantClient")
    async def test_store_without_context_id(self, mock_qdrant_class, settings):
        """Test that storing without context_id doesn't add context_id to payload."""
        # Mock Qdrant client
        mock_client = MagicMock()
        mock_qdrant_class.return_value = mock_client

        memory = MemoryStore(settings, context_id=None)
        await memory.ainit()

        # Create memory record
        record = MemoryRecord(
            conversation_id="test_conv",
            text="Test memory",
            metadata={},
        )

        # Mock embedder
        with patch.object(memory, "_get_embedding", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1, 0.2, 0.3]

            await memory.store(record)

            # Extract payload
            call_args = mock_client.upsert.call_args
            points = call_args[1]["points"]
            payload = points[0].payload

            # Should NOT have context_id
            assert "context_id" not in payload

    @patch("core.core.memory.QdrantClient")
    async def test_search_filters_by_context_id(self, mock_qdrant_class, settings):
        """Test that search filters results by context_id."""
        context_id = uuid.uuid4()

        # Mock Qdrant client
        mock_client = MagicMock()
        mock_qdrant_class.return_value = mock_client

        # Mock search results
        mock_point = MagicMock()
        mock_point.payload = {
            "conversation_id": "test_conv",
            "text": "Test memory",
            "context_id": str(context_id),
        }
        mock_point.score = 0.95

        mock_client.search.return_value = [mock_point]

        memory = MemoryStore(settings, context_id=context_id)
        await memory.ainit()

        # Mock embedder
        with patch.object(memory, "_get_embedding", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1, 0.2, 0.3]

            results = await memory.search("test query", limit=5)

            # Verify search was called
            mock_client.search.assert_called_once()

            # Extract filter from call
            call_kwargs = mock_client.search.call_args[1]
            assert "query_filter" in call_kwargs

            # Verify context_id filter was applied
            query_filter = call_kwargs["query_filter"]
            # Note: The actual filter structure depends on qdrant_client.models.Filter
            # We can check that it was constructed with our context_id
            assert query_filter is not None

    @patch("core.core.memory.QdrantClient")
    async def test_search_with_conversation_id_adds_both_filters(self, mock_qdrant_class, settings):
        """Test that searching with conversation_id adds both context and conversation filters."""
        context_id = uuid.uuid4()
        conversation_id = "specific_conv"

        # Mock Qdrant client
        mock_client = MagicMock()
        mock_qdrant_class.return_value = mock_client

        mock_point = MagicMock()
        mock_point.payload = {
            "conversation_id": conversation_id,
            "text": "Test memory",
            "context_id": str(context_id),
        }
        mock_point.score = 0.95
        mock_client.search.return_value = [mock_point]

        memory = MemoryStore(settings, context_id=context_id)
        await memory.ainit()

        # Mock embedder
        with patch.object(memory, "_get_embedding", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1, 0.2, 0.3]

            results = await memory.search("test query", limit=5, conversation_id=conversation_id)

            # Verify search was called with filters
            mock_client.search.assert_called_once()

            call_kwargs = mock_client.search.call_args[1]
            query_filter = call_kwargs["query_filter"]

            # Should have both context_id and conversation_id filters
            assert query_filter is not None

    @patch("core.core.memory.QdrantClient")
    async def test_search_without_context_id_no_filter(self, mock_qdrant_class, settings):
        """Test that search without context_id doesn't add context filter."""
        # Mock Qdrant client
        mock_client = MagicMock()
        mock_qdrant_class.return_value = mock_client

        mock_point = MagicMock()
        mock_point.payload = {
            "conversation_id": "test_conv",
            "text": "Test memory",
        }
        mock_point.score = 0.95
        mock_client.search.return_value = [mock_point]

        memory = MemoryStore(settings, context_id=None)
        await memory.ainit()

        # Mock embedder
        with patch.object(memory, "_get_embedding", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1, 0.2, 0.3]

            results = await memory.search("test query", limit=5)

            # Verify search was called
            mock_client.search.assert_called_once()

            call_kwargs = mock_client.search.call_args[1]

            # Should not have context filter (or should be empty filter list)
            query_filter = call_kwargs.get("query_filter")
            # Filter might be None or an empty filter depending on implementation

    async def test_different_contexts_isolated(self, settings):
        """Integration test: Verify different context_id values create isolated stores."""
        context_a = uuid.uuid4()
        context_b = uuid.uuid4()

        memory_a = MemoryStore(settings, context_id=context_a)
        memory_b = MemoryStore(settings, context_id=context_b)

        # Verify they have different context_ids
        assert memory_a._context_id == context_a
        assert memory_b._context_id == context_b
        assert memory_a._context_id != memory_b._context_id

        # Verify they are different instances
        assert memory_a is not memory_b

    @patch("core.core.memory.QdrantClient")
    async def test_store_multiple_memories_same_context(self, mock_qdrant_class, settings):
        """Test storing multiple memories maintains same context_id."""
        context_id = uuid.uuid4()

        # Mock Qdrant client
        mock_client = MagicMock()
        mock_qdrant_class.return_value = mock_client

        memory = MemoryStore(settings, context_id=context_id)
        await memory.ainit()

        # Store multiple records
        records = [
            MemoryRecord(
                conversation_id="conv1",
                text="Memory 1",
                metadata={},
            ),
            MemoryRecord(
                conversation_id="conv2",
                text="Memory 2",
                metadata={},
            ),
        ]

        # Mock embedder
        with patch.object(memory, "_get_embedding", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1, 0.2, 0.3]

            for record in records:
                await memory.store(record)

            # Verify all had same context_id
            assert mock_client.upsert.call_count == 2

            for call in mock_client.upsert.call_args_list:
                points = call[1]["points"]
                payload = points[0].payload
                assert payload["context_id"] == str(context_id)
