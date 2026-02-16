"""Unit tests for CodeIndexer module."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.indexer.ingestion import CodeIndexer


class MockEmbedder:
    """Mock embedder for testing."""

    @property
    def dimension(self) -> int:
        """Return embedding dimension."""
        return 3

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return dummy embeddings."""
        return [[0.1, 0.2, 0.3] for _ in texts]


@pytest.fixture
def mock_embedder() -> MockEmbedder:
    """Create a mock embedder."""
    return MockEmbedder()


@pytest.fixture
def code_indexer(tmp_path: Path, mock_embedder: MockEmbedder) -> CodeIndexer:
    """Create a CodeIndexer with mock dependencies."""
    return CodeIndexer(root_path=tmp_path, embedder=mock_embedder)


class TestCodeIndexerInitialization:
    """Test CodeIndexer initialization."""

    def test_initialization_with_dependencies(
        self, tmp_path: Path, mock_embedder: MockEmbedder
    ) -> None:
        """Test that CodeIndexer initializes with correct dependencies."""
        indexer = CodeIndexer(root_path=tmp_path, embedder=mock_embedder)
        assert indexer.root_path == tmp_path
        assert indexer.embedder is mock_embedder
        assert indexer.client is not None
        assert indexer.splitter is not None

    def test_configuration_from_constructor(
        self, tmp_path: Path, mock_embedder: MockEmbedder
    ) -> None:
        """Test configuration passed via constructor parameters."""
        indexer = CodeIndexer(
            root_path=tmp_path,
            embedder=mock_embedder,
            qdrant_url="http://test-qdrant:9999",
            collection_name="test-code-collection",
        )
        assert indexer.collection_name == "test-code-collection"


class TestCodeIndexerHashCalculation:
    """Test hash calculation for content."""

    def test_calculate_hash_consistent(self, code_indexer: CodeIndexer) -> None:
        """Test that hash calculation is consistent."""
        content = "test content"
        hash1 = code_indexer._calculate_hash(content)
        hash2 = code_indexer._calculate_hash(content)
        assert hash1 == hash2

    def test_calculate_hash_different_content(self, code_indexer: CodeIndexer) -> None:
        """Test that different content produces different hashes."""
        hash1 = code_indexer._calculate_hash("content1")
        hash2 = code_indexer._calculate_hash("content2")
        assert hash1 != hash2

    def test_calculate_hash_matches_sha256(self, code_indexer: CodeIndexer) -> None:
        """Test that hash matches expected SHA256 format."""
        content = "test"
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        result = code_indexer._calculate_hash(content)
        assert result == expected


class TestCodeIndexerFileNeedsUpdate:
    """Test file update detection logic."""

    @pytest.mark.asyncio
    async def test_file_needs_update_no_existing_record(
        self, code_indexer: CodeIndexer, tmp_path: Path
    ) -> None:
        """Test that file needs update when no record exists."""
        # Mock Qdrant to return no records
        with patch.object(code_indexer.client, "scroll", new=AsyncMock(return_value=([], None))):
            file_path = tmp_path / "test.py"
            needs_update = await code_indexer._file_needs_update(file_path, "hash123")
            assert needs_update is True

    @pytest.mark.asyncio
    async def test_file_needs_update_hash_changed(
        self, code_indexer: CodeIndexer, tmp_path: Path
    ) -> None:
        """Test that file needs update when hash changed."""
        # Mock Qdrant to return existing record with different hash
        mock_point = MagicMock()
        mock_point.payload = {"file_hash": "old_hash"}
        with patch.object(
            code_indexer.client, "scroll", new=AsyncMock(return_value=([mock_point], None))
        ):
            file_path = tmp_path / "test.py"
            needs_update = await code_indexer._file_needs_update(file_path, "new_hash")
            assert needs_update is True

    @pytest.mark.asyncio
    async def test_file_needs_update_hash_unchanged(
        self, code_indexer: CodeIndexer, tmp_path: Path
    ) -> None:
        """Test that file does not need update when hash unchanged."""
        # Mock Qdrant to return existing record with same hash
        mock_point = MagicMock()
        mock_point.payload = {"file_hash": "same_hash"}
        with patch.object(
            code_indexer.client, "scroll", new=AsyncMock(return_value=([mock_point], None))
        ):
            file_path = tmp_path / "test.py"
            needs_update = await code_indexer._file_needs_update(file_path, "same_hash")
            assert needs_update is False


class TestCodeIndexerDeleteOldChunks:
    """Test deletion of old chunks."""

    @pytest.mark.asyncio
    async def test_delete_old_chunks(self, code_indexer: CodeIndexer, tmp_path: Path) -> None:
        """Test that delete_old_chunks calls Qdrant delete with correct filter."""
        mock_delete = AsyncMock()
        with patch.object(code_indexer.client, "delete", new=mock_delete):
            file_path = tmp_path / "test.py"
            await code_indexer._delete_old_chunks(file_path)

            # Verify delete was called
            mock_delete.assert_called_once()
            call_args = mock_delete.call_args
            assert call_args[1]["collection_name"] == code_indexer.collection_name


class TestCodeIndexerIndexFile:
    """Test file indexing functionality."""

    @pytest.mark.asyncio
    async def test_index_file_success(self, code_indexer: CodeIndexer, tmp_path: Path) -> None:
        """Test successful file indexing."""
        # Create test file
        test_file = tmp_path / "test.py"
        test_file.write_text("def test():\n    pass\n", encoding="utf-8")

        # Mock dependencies
        mock_scroll = AsyncMock(return_value=([], None))
        mock_delete = AsyncMock()
        mock_upsert = AsyncMock()
        mock_split = MagicMock(
            return_value=[
                {"text": "chunk1", "filepath": str(test_file)},
                {"text": "chunk2", "filepath": str(test_file)},
            ]
        )

        with (
            patch.object(code_indexer.client, "scroll", new=mock_scroll),
            patch.object(code_indexer.client, "delete", new=mock_delete),
            patch.object(code_indexer.client, "upsert", new=mock_upsert),
            patch.object(code_indexer.splitter, "split_file", new=mock_split),
        ):
            await code_indexer.index_file(test_file)

            # Verify upsert was called with correct number of chunks
            mock_upsert.assert_called_once()
            call_args = mock_upsert.call_args
            points = call_args[1]["points"]
            assert len(points) == 2

    @pytest.mark.asyncio
    async def test_index_file_binary_file_skipped(
        self, code_indexer: CodeIndexer, tmp_path: Path
    ) -> None:
        """Test that binary files are skipped."""
        # Create binary file
        test_file = tmp_path / "binary.bin"
        test_file.write_bytes(b"\x00\x01\x02\x03")

        # Mock dependencies
        mock_upsert = AsyncMock()

        # Mock open to raise UnicodeDecodeError
        with patch("builtins.open", side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "")):
            with patch.object(code_indexer.client, "upsert", new=mock_upsert):
                await code_indexer.index_file(test_file)

                # Verify upsert was NOT called
                mock_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_index_file_unchanged_file_skipped(
        self, code_indexer: CodeIndexer, tmp_path: Path
    ) -> None:
        """Test that unchanged files are skipped."""
        # Create test file
        test_file = tmp_path / "test.py"
        content = "def test():\n    pass\n"
        test_file.write_text(content, encoding="utf-8")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # Mock Qdrant to return existing record with same hash
        mock_point = MagicMock()
        mock_point.payload = {"file_hash": content_hash}
        mock_scroll = AsyncMock(return_value=([mock_point], None))
        mock_upsert = AsyncMock()

        with (
            patch.object(code_indexer.client, "scroll", new=mock_scroll),
            patch.object(code_indexer.client, "upsert", new=mock_upsert),
        ):
            await code_indexer.index_file(test_file)

            # Verify upsert was NOT called (file unchanged)
            mock_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_index_file_no_chunks(self, code_indexer: CodeIndexer, tmp_path: Path) -> None:
        """Test that files with no chunks are handled gracefully."""
        # Create test file
        test_file = tmp_path / "empty.py"
        test_file.write_text("", encoding="utf-8")

        # Mock dependencies
        mock_scroll = AsyncMock(return_value=([], None))
        mock_delete = AsyncMock()
        mock_upsert = AsyncMock()
        mock_split = MagicMock(return_value=[])

        with (
            patch.object(code_indexer.client, "scroll", new=mock_scroll),
            patch.object(code_indexer.client, "delete", new=mock_delete),
            patch.object(code_indexer.client, "upsert", new=mock_upsert),
            patch.object(code_indexer.splitter, "split_file", new=mock_split),
        ):
            await code_indexer.index_file(test_file)

            # Verify upsert was NOT called (no chunks)
            mock_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_index_file_adds_metadata(
        self, code_indexer: CodeIndexer, tmp_path: Path
    ) -> None:
        """Test that indexing adds correct metadata to chunks."""
        # Create test file
        test_file = tmp_path / "test.py"
        content = "def test():\n    pass\n"
        test_file.write_text(content, encoding="utf-8")

        # Mock dependencies
        mock_scroll = AsyncMock(return_value=([], None))
        mock_delete = AsyncMock()
        mock_upsert = AsyncMock()
        mock_split = MagicMock(return_value=[{"text": "chunk1", "filepath": str(test_file)}])

        with (
            patch.object(code_indexer.client, "scroll", new=mock_scroll),
            patch.object(code_indexer.client, "delete", new=mock_delete),
            patch.object(code_indexer.client, "upsert", new=mock_upsert),
            patch.object(code_indexer.splitter, "split_file", new=mock_split),
        ):
            await code_indexer.index_file(test_file)

            # Verify metadata in upserted points
            call_args = mock_upsert.call_args
            points = call_args[1]["points"]
            assert points[0].payload["source"] == "codebase"
            assert "file_hash" in points[0].payload


class TestCodeIndexerScanAndIndex:
    """Test directory scanning and indexing."""

    @pytest.mark.asyncio
    async def test_scan_and_index_filters_hidden_dirs(
        self, code_indexer: CodeIndexer, tmp_path: Path
    ) -> None:
        """Test that hidden directories are filtered."""
        # Create structure
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("git config", encoding="utf-8")
        (tmp_path / "visible.py").write_text("code", encoding="utf-8")

        # Mock index_file
        mock_index = AsyncMock()
        with patch.object(code_indexer, "index_file", new=mock_index):
            await code_indexer.scan_and_index()

            # Verify .git was not indexed
            calls = mock_index.call_args_list
            indexed_files = [call[0][0] for call in calls]
            assert not any(".git" in str(f) for f in indexed_files)

    @pytest.mark.asyncio
    async def test_scan_and_index_filters_ignored_dirs(
        self, code_indexer: CodeIndexer, tmp_path: Path
    ) -> None:
        """Test that common ignored directories are filtered."""
        # Create structure
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "node_modules").mkdir()
        (tmp_path / ".venv").mkdir()
        (tmp_path / "__pycache__" / "file.pyc").write_text("", encoding="utf-8")

        # Mock index_file
        mock_index = AsyncMock()
        with patch.object(code_indexer, "index_file", new=mock_index):
            await code_indexer.scan_and_index()

            # Verify ignored dirs were not indexed
            calls = mock_index.call_args_list
            indexed_files = [call[0][0] for call in calls]
            assert not any("__pycache__" in str(f) for f in indexed_files)
            assert not any("node_modules" in str(f) for f in indexed_files)

    @pytest.mark.asyncio
    async def test_scan_and_index_filters_hidden_files(
        self, code_indexer: CodeIndexer, tmp_path: Path
    ) -> None:
        """Test that hidden files are filtered."""
        # Create files
        (tmp_path / ".hidden").write_text("hidden", encoding="utf-8")
        (tmp_path / "visible.py").write_text("code", encoding="utf-8")

        # Mock index_file
        mock_index = AsyncMock()
        with patch.object(code_indexer, "index_file", new=mock_index):
            await code_indexer.scan_and_index()

            # Verify .hidden was not indexed
            calls = mock_index.call_args_list
            indexed_files = [call[0][0] for call in calls]
            assert not any(".hidden" in str(f) for f in indexed_files)

    @pytest.mark.asyncio
    async def test_scan_and_index_only_text_extensions(
        self, code_indexer: CodeIndexer, tmp_path: Path
    ) -> None:
        """Test that only specific text file extensions are indexed."""
        # Create files with various extensions
        (tmp_path / "code.py").write_text("python", encoding="utf-8")
        (tmp_path / "doc.md").write_text("markdown", encoding="utf-8")
        (tmp_path / "config.yml").write_text("yaml", encoding="utf-8")
        (tmp_path / "image.png").write_bytes(b"\x89PNG")
        (tmp_path / "binary.exe").write_bytes(b"MZ")

        # Mock index_file
        mock_index = AsyncMock()
        with patch.object(code_indexer, "index_file", new=mock_index):
            await code_indexer.scan_and_index()

            # Verify only text files were indexed
            calls = mock_index.call_args_list
            indexed_files = [call[0][0].name for call in calls]
            assert "code.py" in indexed_files
            assert "doc.md" in indexed_files
            assert "config.yml" in indexed_files
            assert "image.png" not in indexed_files
            assert "binary.exe" not in indexed_files

    @pytest.mark.asyncio
    async def test_scan_and_index_respects_gitignore(
        self, code_indexer: CodeIndexer, tmp_path: Path
    ) -> None:
        """Test that gitignore patterns are respected."""
        # Create .gitignore
        (tmp_path / ".gitignore").write_text("ignored.py\nignored_dir/\n", encoding="utf-8")

        # Create files
        (tmp_path / "ignored.py").write_text("ignored", encoding="utf-8")
        (tmp_path / "included.py").write_text("included", encoding="utf-8")
        ignored_dir = tmp_path / "ignored_dir"
        ignored_dir.mkdir()
        (ignored_dir / "file.py").write_text("ignored", encoding="utf-8")

        # Mock index_file
        mock_index = AsyncMock()
        with patch.object(code_indexer, "index_file", new=mock_index):
            await code_indexer.scan_and_index()

            # Verify gitignored files were not indexed
            calls = mock_index.call_args_list
            indexed_files = [call[0][0] for call in calls]
            assert not any("ignored.py" in str(f) for f in indexed_files)
            assert any("included.py" in str(f) for f in indexed_files)


class TestCodeIndexerClose:
    """Test cleanup functionality."""

    @pytest.mark.asyncio
    async def test_close(self, code_indexer: CodeIndexer) -> None:
        """Test that close properly closes Qdrant client."""
        mock_close = AsyncMock()
        with patch.object(code_indexer.client, "close", new=mock_close):
            await code_indexer.close()

            mock_close.assert_called_once()
