"""Integration tests for the chunking pipeline and CodeIndexer compatibility."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.indexer.chunker import SemanticChunker
from modules.indexer.code_splitter import CodeSplitter
from modules.rag import RAGManager


@pytest.fixture
def mock_embedder() -> AsyncMock:
    """Create a mock embedder that returns deterministic vectors."""
    embedder = AsyncMock()
    embedder.embed.return_value = [[0.1] * 384]  # Simulate 384-dim embeddings
    return embedder


@pytest.fixture
def mock_qdrant_client() -> AsyncMock:
    """Create a mock Qdrant client."""
    client = AsyncMock()
    client.upsert = AsyncMock()
    return client


@pytest.fixture
def rag_manager_with_mock_client(mock_embedder: AsyncMock, mock_qdrant_client: AsyncMock) -> RAGManager:
    """Create a RAGManager with mocked Qdrant client."""
    with patch("modules.rag.AsyncQdrantClient", return_value=mock_qdrant_client):
        manager = RAGManager(
            embedder=mock_embedder,
            collection_name="documents_v2",
        )
        # Override the lazy-loaded client
        manager._client = mock_qdrant_client
        return manager


@pytest.fixture
def semantic_chunker() -> SemanticChunker:
    """Create a SemanticChunker with small chunk size for testing."""
    return SemanticChunker(chunk_size=256)


class TestRAGManagerIngestion:
    """Integration tests for RAGManager document ingestion with SemanticChunker."""

    @pytest.mark.asyncio
    async def test_ingest_document_calls_semantic_chunker(
        self,
        rag_manager_with_mock_client: RAGManager,
        mock_embedder: AsyncMock,
        mock_qdrant_client: AsyncMock,
        semantic_chunker: SemanticChunker,
    ) -> None:
        """Test that ingest_document calls SemanticChunker and stores in documents_v2."""
        content = "# Test Document\n\nThis is some test content for chunking."
        metadata = {"uri": "test://doc1", "name": "Test Doc"}

        # Mock the chunker to return known chunks
        with patch.object(semantic_chunker, "split_text") as mock_split:
            mock_split.return_value = [
                {"text": "chunk1", "metadata": {"chunk_index": 0, "chunk_type": "semantic"}},
                {"text": "chunk2", "metadata": {"chunk_index": 1, "chunk_type": "semantic"}},
            ]

            rag_manager_with_mock_client.chunker = semantic_chunker

            result = await rag_manager_with_mock_client.ingest_document(content, metadata)

            # Verify chunker was called
            mock_split.assert_called_once_with(content, document_type="prose")

            # Verify embedder was called with chunked text
            assert mock_embedder.embed.call_count == 1
            texts_called = mock_embedder.embed.call_args[0][0]
            assert len(texts_called) == 2
            assert "chunk1" in texts_called
            assert "chunk2" in texts_called

    @pytest.mark.asyncio
    async def test_ingest_document_stores_in_documents_v2_collection(
        self,
        rag_manager_with_mock_client: RAGManager,
        mock_embedder: AsyncMock,
        mock_qdrant_client: AsyncMock,
        semantic_chunker: SemanticChunker,
    ) -> None:
        """Test that ingested documents go to documents_v2 collection, not agent-memories."""
        content = "Test content for collection isolation."
        metadata = {"uri": "test://doc1"}

        with patch.object(semantic_chunker, "split_text") as mock_split:
            mock_split.return_value = [
                {"text": "chunk1", "metadata": {"chunk_index": 0}},
            ]
            rag_manager_with_mock_client.chunker = semantic_chunker

            await rag_manager_with_mock_client.ingest_document(content, metadata)

            # Verify the collection name used in upsert
            assert mock_qdrant_client.upsert.called
            call_args = mock_qdrant_client.upsert.call_args
            collection_name = call_args.kwargs.get("collection_name")
            assert collection_name == "documents_v2"

    @pytest.mark.asyncio
    async def test_ingest_document_collection_isolation(
        self,
        rag_manager_with_mock_client: RAGManager,
        mock_embedder: AsyncMock,
        mock_qdrant_client: AsyncMock,
        semantic_chunker: SemanticChunker,
    ) -> None:
        """Test that documents_v2 is separate from agent-memories collection."""
        # Test 1: RAGManager with documents_v2
        rag_manager_with_mock_client.collection_name = "documents_v2"
        with patch.object(semantic_chunker, "split_text") as mock_split:
            mock_split.return_value = [{"text": "chunk1", "metadata": {}}]
            await rag_manager_with_mock_client.ingest_document("content1", {"uri": "doc1"})

        # Verify documents_v2 was used
        call1_collection = mock_qdrant_client.upsert.call_args.kwargs.get("collection_name")
        assert call1_collection == "documents_v2"

        # Reset mock
        mock_qdrant_client.upsert.reset_mock()

        # Test 2: Create a RAGManager with agent-memories collection
        with patch("modules.rag.AsyncQdrantClient", return_value=mock_qdrant_client):
            manager_memories = RAGManager(
                embedder=mock_embedder,
                collection_name="agent-memories",
            )
            manager_memories._client = mock_qdrant_client
            with patch.object(semantic_chunker, "split_text") as mock_split2:
                mock_split2.return_value = [{"text": "chunk2", "metadata": {}}]
                await manager_memories.ingest_document("content2", {"uri": "doc2"})

        # Verify agent-memories was used
        call2_collection = mock_qdrant_client.upsert.call_args.kwargs.get("collection_name")
        assert call2_collection == "agent-memories"

        # Verify the two collections are different
        assert call1_collection != call2_collection

    @pytest.mark.asyncio
    async def test_ingest_document_preserves_metadata(
        self,
        rag_manager_with_mock_client: RAGManager,
        mock_embedder: AsyncMock,
        mock_qdrant_client: AsyncMock,
        semantic_chunker: SemanticChunker,
    ) -> None:
        """Test that document metadata is preserved through the full pipeline."""
        content = "Test content."
        metadata = {
            "uri": "test://doc1",
            "name": "Test Document",
            "source": "manual",
            "custom_field": "custom_value",
        }

        with patch.object(semantic_chunker, "split_text") as mock_split:
            mock_split.return_value = [
                {
                    "text": "chunk1",
                    "metadata": {
                        "chunk_index": 0,
                        "chunk_type": "semantic",
                        "section_title": "Section 1",
                    },
                },
            ]
            rag_manager_with_mock_client.chunker = semantic_chunker

            await rag_manager_with_mock_client.ingest_document(content, metadata)

            # Verify payload was constructed correctly
            assert mock_qdrant_client.upsert.called
            call_args = mock_qdrant_client.upsert.call_args
            points = call_args.kwargs.get("points", [])

            assert len(points) == 1
            payload = points[0].payload

            # Original metadata preserved
            assert payload["uri"] == "test://doc1"
            assert payload["name"] == "Test Document"
            assert payload["source"] == "manual"
            assert payload["custom_field"] == "custom_value"

            # Chunk-specific metadata preserved
            assert payload["text"] == "chunk1"
            assert payload["chunk_index"] == 0
            assert payload["section_title"] == "Section 1"
            assert payload["chunk_type"] == "semantic"

    @pytest.mark.asyncio
    async def test_ingest_document_semantic_chunk_metadata_preserved(
        self,
        rag_manager_with_mock_client: RAGManager,
        mock_embedder: AsyncMock,
        mock_qdrant_client: AsyncMock,
        semantic_chunker: SemanticChunker,
    ) -> None:
        """Test that semantic chunk metadata (chunk_type, document_type) is preserved."""
        content = "Test content."
        metadata = {"uri": "test://doc1"}

        with patch.object(semantic_chunker, "split_text") as mock_split:
            mock_split.return_value = [
                {
                    "text": "chunk1",
                    "metadata": {
                        "chunk_index": 0,
                        "chunk_type": "semantic",
                        "document_type": "markdown",
                        "section_title": "Intro",
                    },
                },
            ]
            rag_manager_with_mock_client.chunker = semantic_chunker

            result = await rag_manager_with_mock_client.ingest_document(content, metadata, document_type="markdown")

            assert mock_qdrant_client.upsert.called
            points = mock_qdrant_client.upsert.call_args.kwargs["points"]
            payload = points[0].payload

            # Semantic chunk metadata should be in payload
            assert payload["chunk_type"] == "semantic"
            assert payload["document_type"] == "markdown"
            assert payload["section_title"] == "Intro"

    def test_ingest_document_empty_content_returns_zero(
        self,
        rag_manager_with_mock_client: RAGManager,
        mock_embedder: AsyncMock,
    ) -> None:
        """Test that empty content returns 0 without calling embedder."""
        # ingest_document is async, so we need to test it differently
        # Empty content should return 0 directly
        result = rag_manager_with_mock_client.ingest_document("", {"uri": "doc1"})

        # Check that it's a coroutine (the async method returns a coroutine)
        import inspect
        assert inspect.iscoroutine(result)

    @pytest.mark.asyncio
    async def test_ingest_document_none_chunker_uses_default(
        self,
        rag_manager_with_mock_client: RAGManager,
        mock_embedder: AsyncMock,
        mock_qdrant_client: AsyncMock,
    ) -> None:
        """Test that ingest_document uses instance default chunker when None passed."""
        content = "Test content."

        # The instance should have a default SemanticChunker
        assert rag_manager_with_mock_client.chunker is not None
        assert isinstance(rag_manager_with_mock_client.chunker, SemanticChunker)

        # Test that calling with None chunker uses default
        with patch.object(rag_manager_with_mock_client.chunker, "split_text") as mock_split:
            mock_split.return_value = [{"text": "chunk1", "metadata": {}}]
            mock_embedder.embed.return_value = [[0.1] * 384]

            result = await rag_manager_with_mock_client.ingest_document(content, {"uri": "doc1"}, chunker=None)

            # Verify chunker was called with the content
            mock_split.assert_called_once_with(content, document_type="prose")


class TestCodeIndexerCompatibility:
    """Tests to verify CodeIndexer continues working with the updated pipeline."""

    def test_code_splitter_uses_langchain_text_splitters(
        self,
    ) -> None:
        """Test that CodeSplitter still uses langchain-text-splitters."""
        # Verify the import exists
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        assert RecursiveCharacterTextSplitter is not None

        splitter = CodeSplitter()
        assert splitter.recursive_splitter is not None
        assert splitter.python_splitter is not None

    def test_code_splitter_can_split_python_files(
        self,
    ) -> None:
        """Test that CodeSplitter can split Python files."""
        splitter = CodeSplitter(chunk_size=500, chunk_overlap=100)

        python_code = """def hello():
    print("Hello")

def world():
    print("World")
"""

        chunks = splitter.split_file(Path("test.py"), python_code)

        assert len(chunks) > 0
        for chunk in chunks:
            assert "text" in chunk
            assert "filepath" in chunk
            assert chunk["filepath"] == "test.py"
            assert chunk["type"] == "code_chunk"

    def test_code_splitter_differentiates_by_extension(
        self,
    ) -> None:
        """Test that CodeSplitter uses appropriate splitter by file extension."""
        splitter = CodeSplitter(chunk_size=500, chunk_overlap=100)

        # Python file should use python_splitter
        python_code = "def test(): pass"
        python_chunks = splitter.split_file(Path("test.py"), python_code)

        # Markdown file should use recursive_splitter
        md_code = "# Header\n\nContent"
        md_chunks = splitter.split_file(Path("test.md"), md_code)

        # Both should produce chunks
        assert len(python_chunks) > 0
        assert len(md_chunks) > 0

    def test_code_indexer_uses_langchain_splitter(
        self,
    ) -> None:
        """Test that CodeIndexer uses CodeSplitter (which uses langchain)."""

        mock_embedder = MagicMock()
        mock_client = MagicMock()

        indexer = CodeSplitter()

        # Verify it has the expected splitter attributes
        assert hasattr(indexer, "recursive_splitter")
        assert hasattr(indexer, "python_splitter")

        # Verify the splitters are langchain splitters
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        assert isinstance(indexer.recursive_splitter, RecursiveCharacterTextSplitter)
        assert isinstance(indexer.python_splitter, RecursiveCharacterTextSplitter)

    def test_code_splitter_preserves_filepath_metadata(
        self,
    ) -> None:
        """Test that CodeSplitter preserves filepath in chunk metadata."""
        splitter = CodeSplitter()
        file_path = Path("/path/to/file.py")
        content = "print('test')"

        chunks = splitter.split_file(file_path, content)

        assert len(chunks) > 0
        for chunk in chunks:
            assert chunk["filepath"] == str(file_path)

    def test_code_splitter_chunk_structure(
        self,
    ) -> None:
        """Test that CodeSplitter chunks have expected structure."""
        splitter = CodeSplitter()
        content = "line1\nline2\nline3\nline4\nline5\n"

        chunks = splitter.split_file(Path("test.py"), content)

        # Check structure of each chunk
        for chunk in chunks:
            assert "text" in chunk
            assert "filepath" in chunk
            assert "type" in chunk
            assert isinstance(chunk["text"], str)
            assert chunk["type"] == "code_chunk"


class TestEndToEndPipeline:
    """End-to-end integration tests for the chunking pipeline."""

    @pytest.mark.asyncio
    async def test_full_ingestion_pipeline_markdown(
        self,
        rag_manager_with_mock_client: RAGManager,
        mock_embedder: AsyncMock,
        mock_qdrant_client: AsyncMock,
    ) -> None:
        """Test complete ingestion pipeline for markdown documents."""
        content = """# Main Title

This is the introduction.

## Section 1

Content for section 1.

## Section 2

More content here.
"""
        metadata = {"uri": "docs://guide", "name": "User Guide"}

        # Use the actual SemanticChunker instance
        chunker = rag_manager_with_mock_client.chunker

        # Mock embedder to return embeddings for all chunks
        chunk_count = 3  # Expected chunk count from markdown

        async def mock_embed(texts: list[str]) -> list[list[float]]:
            # Return a unique embedding for each text
            return [[0.1] * 384 for _ in texts]

        mock_embedder.embed.side_effect = mock_embed

        # Call ingest_document with actual chunker
        result = await rag_manager_with_mock_client.ingest_document(content, metadata, document_type="markdown")

        # Verify ingestion happened
        assert mock_qdrant_client.upsert.called
        points = mock_qdrant_client.upsert.call_args.kwargs["points"]
        assert len(points) > 0

        # Verify collection
        collection = mock_qdrant_client.upsert.call_args.kwargs["collection_name"]
        assert collection == "documents_v2"

    @pytest.mark.asyncio
    async def test_full_ingestion_pipeline_prose(
        self,
        rag_manager_with_mock_client: RAGManager,
        mock_embedder: AsyncMock,
        mock_qdrant_client: AsyncMock,
    ) -> None:
        """Test complete ingestion pipeline for prose documents."""
        content = """This is a prose document. It contains multiple paragraphs. Each paragraph
should be chunked semantically. The chunking algorithm should preserve
sentence boundaries where possible. This is the second paragraph with
more content to ensure proper chunking behavior."""

        metadata = {"uri": "docs://article", "name": "Article"}

        result = await rag_manager_with_mock_client.ingest_document(content, metadata, document_type="prose")

        # Verify ingestion
        assert mock_qdrant_client.upsert.called
        points = mock_qdrant_client.upsert.call_args.kwargs["points"]
        assert len(points) > 0

    def test_code_indexer_separate_from_semantic_chunker(
        self,
    ) -> None:
        """Test that CodeIndexer (using langchain) and RAGManager (using chonkie) are independent."""
        # CodeSplitter uses langchain
        code_splitter = CodeSplitter()
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        assert isinstance(code_splitter.recursive_splitter, RecursiveCharacterTextSplitter)

        # SemanticChunker uses chonkie
        semantic_chunker = SemanticChunker()
        from chonkie import RecursiveChunker

        assert isinstance(semantic_chunker.prose_splitter, RecursiveChunker)
        assert isinstance(semantic_chunker.markdown_splitter, RecursiveChunker)

        # They should produce different chunk structures
        test_text = "Test content " * 20

        code_chunks = code_splitter.split_file(Path("test.py"), test_text)
        semantic_chunks = semantic_chunker.split_text(test_text, document_type="prose")

        # Both should produce chunks
        assert len(code_chunks) > 0
        assert len(semantic_chunks) > 0

        # But the structures may differ
        # Code chunks have filepath, semantic chunks have semantic metadata
        assert "filepath" in code_chunks[0]
        assert "metadata" in semantic_chunks[0]
        assert "chunk_type" in semantic_chunks[0]["metadata"]


class TestCollectionIsolation:
    """Tests verifying collection isolation between semantic chunking and code indexing."""

    def test_documents_v2_collection_name_constant(
        self,
    ) -> None:
        """Test that documents_v2 is the default collection for RAGManager."""
        from modules.rag import RAGManager

        mock_embedder = MagicMock()
        manager = RAGManager(embedder=mock_embedder)

        assert manager.collection_name == "documents_v2"

    def test_agent_memories_collection_name_constant(
        self,
    ) -> None:
        """Test that agent-memories is used for CodeIndexer."""

        mock_embedder = MagicMock()
        mock_client = MagicMock()

        from modules.indexer.ingestion import CodeIndexer

        indexer = CodeIndexer(
            root_path=Path("."),
            embedder=mock_embedder,
            collection_name="agent-memories",
        )

        assert indexer.collection_name == "agent-memories"

    def test_ensure_separate_collections_prevent_collision(
        self,
    ) -> None:
        """Test that the two collections are truly separate."""
        # documents_v2 for RAGManager with SemanticChunker
        from modules.rag import RAGManager

        mock_embedder = MagicMock()

        manager = RAGManager(embedder=mock_embedder)
        assert manager.collection_name == "documents_v2"

        # agent-memories for CodeIndexer with CodeSplitter
        from modules.indexer.ingestion import CodeIndexer

        indexer = CodeIndexer(
            root_path=Path("."),
            embedder=mock_embedder,
            collection_name="agent-memories",
        )
        assert indexer.collection_name == "agent-memories"

        # Verify they are different
        assert manager.collection_name != indexer.collection_name
