"""Unit tests for RAGManager module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from modules.rag import RAGManager


class MockEmbedder:
    """Mock embedder for testing."""

    @property
    def dimension(self) -> int:
        """Return embedding dimension."""
        return 3

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return dummy embeddings for testing."""
        return [[0.1, 0.2, 0.3] for _ in texts]


@pytest.fixture
def mock_embedder() -> MockEmbedder:
    """Create a mock embedder."""
    return MockEmbedder()


@pytest.fixture
def rag_manager(mock_embedder: MockEmbedder) -> RAGManager:
    """Create a RAGManager with mock embedder."""
    return RAGManager(embedder=mock_embedder)


class TestRAGManagerInitialization:
    """Test RAGManager initialization."""

    def test_initialization_with_embedder(self, mock_embedder: MockEmbedder) -> None:
        """Test that RAGManager initializes with injected embedder."""
        manager = RAGManager(embedder=mock_embedder)
        assert manager.embedder is mock_embedder
        assert manager._client is None  # Lazy loading

    def test_client_lazy_loading(self, rag_manager: RAGManager) -> None:
        """Test that Qdrant client is lazy-loaded on first access."""
        assert rag_manager._client is None
        _ = rag_manager.client  # Access property
        assert rag_manager._client is not None

    def test_configuration_from_environment(self) -> None:
        """Test configuration loading from environment variables."""
        with patch.dict(
            "os.environ",
            {
                "QDRANT_URL": "http://test-qdrant:1234",
                "QDRANT_TOP_K": "10",
                "MMR_LAMBDA": "0.5",
                "QDRANT_COLLECTION": "test-collection",
            },
        ):
            manager = RAGManager(embedder=MockEmbedder())
            assert manager.qdrant_url == "http://test-qdrant:1234"
            assert manager.top_k == 10
            assert manager.mmr_lambda == 0.5
            assert manager.collection_name == "test-collection"


class TestRAGManagerCosineDistance:
    """Test cosine similarity calculation."""

    def test_cosine_identical_vectors(self, rag_manager: RAGManager) -> None:
        """Test cosine similarity of identical vectors is 1.0."""
        vec = np.array([1.0, 2.0, 3.0])
        similarity = rag_manager._cosine(vec, vec)
        assert pytest.approx(similarity, abs=1e-6) == 1.0

    def test_cosine_orthogonal_vectors(self, rag_manager: RAGManager) -> None:
        """Test cosine similarity of orthogonal vectors is 0.0."""
        vec_a = np.array([1.0, 0.0, 0.0])
        vec_b = np.array([0.0, 1.0, 0.0])
        similarity = rag_manager._cosine(vec_a, vec_b)
        assert pytest.approx(similarity, abs=1e-6) == 0.0

    def test_cosine_opposite_vectors(self, rag_manager: RAGManager) -> None:
        """Test cosine similarity of opposite vectors is -1.0."""
        vec_a = np.array([1.0, 0.0, 0.0])
        vec_b = np.array([-1.0, 0.0, 0.0])
        similarity = rag_manager._cosine(vec_a, vec_b)
        assert pytest.approx(similarity, abs=1e-6) == -1.0

    def test_cosine_handles_zero_vectors(self, rag_manager: RAGManager) -> None:
        """Test cosine similarity handles zero vectors (division by zero protection)."""
        vec_a = np.array([0.0, 0.0, 0.0])
        vec_b = np.array([1.0, 2.0, 3.0])
        # Should not raise, returns a valid number due to +1e-9 in denominator
        similarity = rag_manager._cosine(vec_a, vec_b)
        assert isinstance(similarity, float)


class TestRAGManagerMMR:
    """Test Maximal Marginal Relevance (MMR) algorithm."""

    def test_mmr_empty_docs(self, rag_manager: RAGManager) -> None:
        """Test MMR with no documents returns empty list."""
        query_vec = np.array([1.0, 0.0, 0.0])
        result = rag_manager._mmr(query_vec, [], k=5, lam=0.7)
        assert result == []

    def test_mmr_single_doc(self, rag_manager: RAGManager) -> None:
        """Test MMR with single document returns that document."""
        query_vec = np.array([1.0, 0.0, 0.0])
        doc_vecs = [np.array([0.9, 0.1, 0.0])]
        result = rag_manager._mmr(query_vec, doc_vecs, k=5, lam=0.7)
        assert result == [0]

    def test_mmr_k_greater_than_docs(self, rag_manager: RAGManager) -> None:
        """Test MMR when k is greater than number of documents."""
        query_vec = np.array([1.0, 0.0, 0.0])
        doc_vecs = [
            np.array([0.9, 0.1, 0.0]),
            np.array([0.8, 0.2, 0.0]),
        ]
        result = rag_manager._mmr(query_vec, doc_vecs, k=10, lam=0.7)
        assert len(result) == 2  # Only 2 docs available

    def test_mmr_diversification(self, rag_manager: RAGManager) -> None:
        """Test that MMR selects diverse documents."""
        query_vec = np.array([1.0, 0.0, 0.0])
        doc_vecs = [
            np.array([0.9, 0.1, 0.0]),  # Similar to query
            np.array([0.88, 0.12, 0.0]),  # Very similar to first doc
            np.array([0.5, 0.5, 0.0]),  # Different direction
        ]

        # With high lambda (0.9), favor diversity
        result = rag_manager._mmr(query_vec, doc_vecs, k=2, lam=0.9)
        assert 0 in result  # Most relevant should be included
        # Second doc should be the diverse one (index 2), not similar one (index 1)
        assert 2 in result


class TestRAGManagerRetrieve:
    """Test RAG retrieval functionality."""

    @pytest.mark.asyncio
    async def test_retrieve_empty_query(self, rag_manager: RAGManager) -> None:
        """Test retrieve with empty query returns empty list."""
        # Mock embedder to return empty vectors
        rag_manager.embedder = MagicMock()
        rag_manager.embedder.embed = AsyncMock(return_value=[])

        result = await rag_manager.retrieve("test query")
        assert result == []

    @pytest.mark.asyncio
    async def test_retrieve_no_results(self, rag_manager: RAGManager) -> None:
        """Test retrieve with no Qdrant results returns empty list."""
        # Mock Qdrant client
        mock_client = AsyncMock()
        mock_client.query_points = AsyncMock(return_value=MagicMock(points=[]))
        rag_manager._client = mock_client

        result = await rag_manager.retrieve("test query")
        assert result == []

    @pytest.mark.asyncio
    async def test_retrieve_with_filters(self, rag_manager: RAGManager) -> None:
        """Test retrieve applies filters correctly."""
        # Mock Qdrant client
        mock_client = AsyncMock()
        mock_client.query_points = AsyncMock(return_value=MagicMock(points=[]))
        rag_manager._client = mock_client

        await rag_manager.retrieve("test", filters={"context_id": "123"})

        # Verify query_points was called with filters
        call_args = mock_client.query_points.call_args
        assert call_args[1]["query_filter"] is not None

    @pytest.mark.asyncio
    async def test_retrieve_deduplication(self, rag_manager: RAGManager) -> None:
        """Test that duplicate URIs are deduplicated."""
        # Mock Qdrant response with duplicate URIs
        mock_points = [
            MagicMock(
                payload={"url": "https://example.com", "text": "content1"},
                vector=[0.1, 0.2, 0.3],
                score=0.9,
            ),
            MagicMock(
                payload={"url": "https://example.com", "text": "content2"},
                vector=[0.15, 0.25, 0.35],
                score=0.85,
            ),
        ]

        mock_client = AsyncMock()
        mock_client.query_points = AsyncMock(return_value=MagicMock(points=mock_points))
        rag_manager._client = mock_client

        result = await rag_manager.retrieve("test", top_k=5)

        # Should only have 1 result due to deduplication
        assert len(result) == 1
        assert result[0]["url"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_retrieve_handles_exceptions(self, rag_manager: RAGManager) -> None:
        """Test that retrieve handles exceptions gracefully."""
        # Mock embedder to raise exception
        rag_manager.embedder = MagicMock()
        rag_manager.embedder.embed = AsyncMock(side_effect=Exception("Embedding failed"))

        result = await rag_manager.retrieve("test query")
        assert result == []  # Should return empty list on error

    @pytest.mark.asyncio
    async def test_retrieve_custom_collection(self, rag_manager: RAGManager) -> None:
        """Test retrieve with custom collection name."""
        mock_client = AsyncMock()
        mock_client.query_points = AsyncMock(return_value=MagicMock(points=[]))
        rag_manager._client = mock_client

        await rag_manager.retrieve("test", collection_name="custom-collection")

        # Verify query_points was called with custom collection
        call_args = mock_client.query_points.call_args
        assert call_args[1]["collection_name"] == "custom-collection"


class TestRAGManagerIngestDocument:
    """Test document ingestion functionality."""

    @pytest.mark.asyncio
    async def test_ingest_empty_content(self, rag_manager: RAGManager) -> None:
        """Test that empty content returns 0 chunks."""
        result = await rag_manager.ingest_document("", metadata={})
        assert result == 0

    @pytest.mark.asyncio
    async def test_ingest_chunking_logic(self, rag_manager: RAGManager) -> None:
        """Test that content is chunked correctly."""
        # Mock Qdrant client
        mock_client = AsyncMock()
        mock_client.upsert = AsyncMock()
        rag_manager._client = mock_client

        # Short content that should produce 1 chunk
        content = "Short content"
        result = await rag_manager.ingest_document(
            content, metadata={"uri": "test.txt"}, chunk_size=1000
        )

        assert result == 1
        # Verify upsert was called
        mock_client.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_ingest_multiple_chunks(self, rag_manager: RAGManager) -> None:
        """Test that long content produces multiple chunks."""
        # Mock Qdrant client
        mock_client = AsyncMock()
        mock_client.upsert = AsyncMock()
        rag_manager._client = mock_client

        # Long content (2000 chars)
        content = "x" * 2000
        result = await rag_manager.ingest_document(
            content, metadata={"uri": "test.txt"}, chunk_size=500, chunk_overlap=100
        )

        # Should produce multiple chunks
        assert result > 1

    @pytest.mark.asyncio
    async def test_ingest_preserves_metadata(self, rag_manager: RAGManager) -> None:
        """Test that metadata is preserved in chunks."""
        # Mock Qdrant client
        mock_client = AsyncMock()
        mock_client.upsert = AsyncMock()
        rag_manager._client = mock_client

        metadata = {"uri": "test.txt", "author": "test"}
        await rag_manager.ingest_document("content", metadata=metadata)

        # Verify upsert was called with metadata
        call_args = mock_client.upsert.call_args
        points = call_args[1]["points"]
        assert all(p.payload["uri"] == "test.txt" and p.payload["author"] == "test" for p in points)

    @pytest.mark.asyncio
    async def test_ingest_handles_embedder_failure(self, rag_manager: RAGManager) -> None:
        """Test that ingest handles embedder failures gracefully."""
        # Mock embedder to fail
        rag_manager.embedder = MagicMock()
        rag_manager.embedder.embed = AsyncMock(side_effect=Exception("Embedding failed"))

        result = await rag_manager.ingest_document("content", metadata={})
        assert result == 0  # Should return 0 on error

    @pytest.mark.asyncio
    async def test_ingest_handles_qdrant_failure(self, rag_manager: RAGManager) -> None:
        """Test that ingest handles Qdrant failures gracefully."""
        # Mock Qdrant client to fail
        mock_client = AsyncMock()
        mock_client.upsert = AsyncMock(side_effect=Exception("Qdrant error"))
        rag_manager._client = mock_client

        result = await rag_manager.ingest_document("content", metadata={})
        assert result == 0  # Should return 0 on error


class TestRAGManagerClose:
    """Test cleanup functionality."""

    @pytest.mark.asyncio
    async def test_close_with_initialized_client(self, rag_manager: RAGManager) -> None:
        """Test that close properly closes an initialized client."""
        # Initialize client
        mock_client = AsyncMock()
        rag_manager._client = mock_client

        await rag_manager.close()

        # Verify close was called
        mock_client.close.assert_called_once()
        assert rag_manager._client is None

    @pytest.mark.asyncio
    async def test_close_without_initialized_client(self, rag_manager: RAGManager) -> None:
        """Test that close handles uninitialized client gracefully."""
        assert rag_manager._client is None

        # Should not raise
        await rag_manager.close()

        assert rag_manager._client is None
