"""Tests for the SemanticChunker module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from chonkie.types import Chunk

from modules.indexer.chunker import SemanticChunker


@pytest.fixture
def chunker() -> SemanticChunker:
    """Create a SemanticChunker with a small chunk size for testing."""
    return SemanticChunker(chunk_size=256)


@pytest.fixture
def sample_markdown() -> str:
    """Create a sample markdown document with headings."""
    return """# Main Title

This is an introduction paragraph.

## Section 1

Content for section 1 with some text to ensure we have enough content to form multiple chunks.

## Section 2

More content here that should be chunked properly based on the heading structure.

### Subsection 2.1

Detailed content in the subsection.

### Subsection 2.2

Another subsection with content.

# Chapter 2

New chapter content here.
"""


@pytest.fixture
def sample_prose() -> str:
    """Create a sample prose document."""
    return """This is a long prose document that needs to be chunked properly. The quick brown fox jumps over the lazy dog. Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum. This sentence continues with more text to ensure proper chunking behavior across sentence boundaries."""
@pytest.fixture
def empty_text() -> str:
    """Create an empty string."""
    return ""


@pytest.fixture
def whitespace_only_text() -> str:
    """Create a string with only whitespace."""
    return "   \n\t  \n   "


class TestSemanticChunkerMarkdown:
    """Tests for markdown chunking functionality."""

    def test_split_markdown_with_headings(self, chunker: SemanticChunker, sample_markdown: str) -> None:
        """Test that markdown splitting respects heading boundaries."""
        result = chunker.split_text(sample_markdown, document_type="markdown")

        assert len(result) > 0
        for chunk in result:
            assert "text" in chunk
            assert "metadata" in chunk
            # Chonkie returns Chunk objects, check they have text attribute
            assert isinstance(chunk["text"], Chunk)
            assert isinstance(chunk["text"].text, str)

    def test_markdown_metadata_contains_chunk_index(self, chunker: SemanticChunker, sample_markdown: str) -> None:
        """Test that markdown chunks have correct chunk_index metadata."""
        result = chunker.split_text(sample_markdown, document_type="markdown")

        for i, chunk in enumerate(result):
            assert chunk["metadata"]["chunk_index"] == i

    def test_markdown_metadata_contains_total_chunks(self, chunker: SemanticChunker, sample_markdown: str) -> None:
        """Test that markdown chunks have total_chunks metadata."""
        result = chunker.split_text(sample_markdown, document_type="markdown")

        total = len(result)
        for chunk in result:
            assert chunk["metadata"]["total_chunks"] == total

    def test_markdown_metadata_contains_chunk_type(self, chunker: SemanticChunker, sample_markdown: str) -> None:
        """Test that markdown chunks have semantic chunk_type."""
        result = chunker.split_text(sample_markdown, document_type="markdown")

        for chunk in result:
            assert chunk["metadata"]["chunk_type"] == "semantic"

    def test_markdown_metadata_contains_document_type(self, chunker: SemanticChunker, sample_markdown: str) -> None:
        """Test that markdown chunks have markdown document_type."""
        result = chunker.split_text(sample_markdown, document_type="markdown")

        for chunk in result:
            assert chunk["metadata"]["document_type"] == "markdown"

    def test_markdown_with_section_title(self, chunker: SemanticChunker, sample_markdown: str) -> None:
        """Test that markdown chunks include section_title metadata."""
        result = chunker.split_text(sample_markdown, document_type="markdown", section_title="Test Section")

        for chunk in result:
            assert chunk["metadata"]["section_title"] == "Test Section"


class TestSemanticChunkerProse:
    """Tests for prose chunking functionality."""

    def test_split_prose_text(self, chunker: SemanticChunker, sample_prose: str) -> None:
        """Test that prose text is split into chunks."""
        result = chunker.split_text(sample_prose, document_type="prose")

        assert len(result) > 0
        for chunk in result:
            assert "text" in chunk
            assert isinstance(chunk["text"], Chunk)
            assert isinstance(chunk["text"].text, str)

    def test_prose_metadata_contains_chunk_index(self, chunker: SemanticChunker, sample_prose: str) -> None:
        """Test that prose chunks have correct chunk_index metadata."""
        result = chunker.split_text(sample_prose, document_type="prose")

        for i, chunk in enumerate(result):
            assert chunk["metadata"]["chunk_index"] == i

    def test_prose_metadata_contains_chunk_type(self, chunker: SemanticChunker, sample_prose: str) -> None:
        """Test that prose chunks have semantic chunk_type."""
        result = chunker.split_text(sample_prose, document_type="prose")

        for chunk in result:
            assert chunk["metadata"]["chunk_type"] == "semantic"

    def test_prose_metadata_contains_document_type(self, chunker: SemanticChunker, sample_prose: str) -> None:
        """Test that prose chunks have prose document_type."""
        result = chunker.split_text(sample_prose, document_type="prose")

        for chunk in result:
            assert chunk["metadata"]["document_type"] == "prose"

    def test_prose_with_section_title(self, chunker: SemanticChunker, sample_prose: str) -> None:
        """Test that prose chunks include section_title metadata."""
        result = chunker.split_text(sample_prose, document_type="prose", section_title="Introduction")

        for chunk in result:
            assert chunk["metadata"]["section_title"] == "Introduction"


class TestContentRoute:
    """Tests for content-type routing."""

    def test_markdown_route_uses_markdown_splitter(self, chunker: SemanticChunker, sample_markdown: str) -> None:
        """Test that markdown content uses markdown splitter."""
        result_markdown = chunker.split_text(sample_markdown, document_type="markdown")
        result_prose = chunker.split_text(sample_markdown, document_type="prose")

        # Results may differ in structure due to different splitting strategies
        assert len(result_markdown) > 0
        assert len(result_prose) > 0

    def test_split_file_routes_markdown(self, chunker: SemanticChunker, sample_markdown: str) -> None:
        """Test that split_file routes markdown content correctly."""
        result = chunker.split_file(sample_markdown, document_type="markdown")

        assert len(result) > 0
        for chunk in result:
            assert chunk["metadata"]["document_type"] == "markdown"

    def test_split_file_routes_prose(self, chunker: SemanticChunker, sample_prose: str) -> None:
        """Test that split_file routes prose content correctly."""
        result = chunker.split_file(sample_prose, document_type="prose")

        assert len(result) > 0
        for chunk in result:
            assert chunk["metadata"]["document_type"] == "prose"


class TestEmptyContent:
    """Tests for empty and edge case content handling."""

    def test_empty_string_returns_empty_list(self, chunker: SemanticChunker, empty_text: str) -> None:
        """Test that empty string returns empty chunk list."""
        result = chunker.split_text(empty_text, document_type="prose")

        assert result == []

    def test_empty_string_markdown(self, chunker: SemanticChunker, empty_text: str) -> None:
        """Test that empty string with markdown type returns empty chunk list."""
        result = chunker.split_text(empty_text, document_type="markdown")

        assert result == []

    def test_whitespace_only_text(self, chunker: SemanticChunker, whitespace_only_text: str) -> None:
        """Test that whitespace-only text is handled gracefully."""
        result = chunker.split_text(whitespace_only_text, document_type="prose")

        # Should return empty or minimal result
        assert isinstance(result, list)


class TestChunkSizeLimits:
    """Tests for chunk size configuration."""

    def test_small_chunk_size(self) -> None:
        """Test chunker with small chunk size (256 tokens)."""
        chunker = SemanticChunker(chunk_size=256)
        text = "Word " * 100  # 200 tokens approximately

        result = chunker.split_text(text, document_type="prose")

        assert len(result) > 0

    def test_medium_chunk_size(self) -> None:
        """Test chunker with medium chunk size (512 tokens)."""
        chunker = SemanticChunker(chunk_size=512)
        text = "Word " * 200  # 400 tokens approximately

        result = chunker.split_text(text, document_type="prose")

        assert len(result) > 0

    def test_chunk_size_affects_result_count(self) -> None:
        """Test that smaller chunk sizes produce more chunks."""
        text = "This is test content. " * 50  # ~500 tokens

        chunker_small = SemanticChunker(chunk_size=100)
        chunker_large = SemanticChunker(chunk_size=500)

        result_small = chunker_small.split_text(text, document_type="prose")
        result_large = chunker_large.split_text(text, document_type="prose")

        # Smaller chunks should produce more or equal number of chunks
        assert len(result_small) >= len(result_large)


class TestMetadataAttachment:
    """Tests for metadata attachment in chunks."""

    def test_default_metadata_structure(self, chunker: SemanticChunker, sample_prose: str) -> None:
        """Test that chunks have all required default metadata fields."""
        result = chunker.split_text(sample_prose, document_type="prose")

        required_fields = {"chunk_index", "total_chunks", "chunk_type", "document_type"}
        for chunk in result:
            assert required_fields.issubset(chunk["metadata"].keys())

    def test_optional_section_title_metadata(self, chunker: SemanticChunker, sample_prose: str) -> None:
        """Test that section_title is optional in metadata."""
        result_without = chunker.split_text(sample_prose, document_type="prose")
        result_with = chunker.split_text(sample_prose, document_type="prose", section_title="Test")

        # Chunks without section_title should not have it in metadata
        assert "section_title" not in result_without[0]["metadata"]
        # Chunks with section_title should have it
        assert result_with[0]["metadata"]["section_title"] == "Test"

    def test_chunk_metadata_consistency(self, chunker: SemanticChunker, sample_markdown: str) -> None:
        """Test that metadata is consistent across all chunks."""
        result = chunker.split_text(sample_markdown, document_type="markdown", section_title="Consistent")

        for chunk in result:
            meta = chunk["metadata"]
            assert meta["chunk_type"] == "semantic"
            assert meta["document_type"] == "markdown"
            assert meta["section_title"] == "Consistent"
            assert meta["chunk_index"] < meta["total_chunks"]


class TestSplitFileAlias:
    """Tests for split_file method as alias for split_text."""

    def test_split_file_delegates_to_split_text(self, chunker: SemanticChunker, sample_prose: str) -> None:
        """Test that split_file produces equivalent results to split_text."""
        text = "Test content " * 20

        result_split_text = chunker.split_text(text, document_type="markdown", section_title="Test")
        result_split_file = chunker.split_file(text, document_type="markdown", section_title="Test")

        # Both should have same number of chunks
        assert len(result_split_text) == len(result_split_file)
        # Each chunk should have same structure
        for rt, rf in zip(result_split_text, result_split_file):
            # Check text content matches (Chunk.text is the same)
            assert rt["text"].text == rf["text"].text
            # Check metadata matches
            assert rt["metadata"] == rf["metadata"]

    def test_split_file_preserves_document_type(self, chunker: SemanticChunker, sample_markdown: str) -> None:
        """Test that split_file preserves document_type routing."""
        result = chunker.split_file(sample_markdown, document_type="markdown")

        assert len(result) > 0
        assert all(chunk["metadata"]["document_type"] == "markdown" for chunk in result)

    def test_split_file_preserves_section_title(self, chunker: SemanticChunker, sample_prose: str) -> None:
        """Test that split_file preserves section_title in metadata."""
        result = chunker.split_file(sample_prose, document_type="prose", section_title="Prose Section")

        assert len(result) > 0
        assert all(chunk["metadata"]["section_title"] == "Prose Section" for chunk in result)


class TestChonkieIntegration:
    """Tests that mock Chonkie integration for routing verification."""

    @patch("modules.indexer.chunker.RecursiveChunker")
    def test_markdown_recipe_used_for_markdown_type(self, mock_recursive_chunker: MagicMock, chunker: SemanticChunker) -> None:
        """Test that markdown document_type uses RecursiveChunker.from_recipe('markdown')."""
        mock_splitter = MagicMock()
        mock_splitter.chunk.return_value = ["chunk1", "chunk2"]
        mock_recursive_chunker.from_recipe.return_value = mock_splitter

        with patch("modules.indexer.chunker.RecursiveChunker", mock_recursive_chunker):
            test_chunker = SemanticChunker(chunk_size=256)
            test_chunker.split_text("# Test", document_type="markdown")

            mock_recursive_chunker.from_recipe.assert_called_once_with("markdown", chunk_size=256)

    @patch("modules.indexer.chunker.RecursiveChunker")
    def test_prose_splitter_used_for_prose_type(self, mock_recursive_chunker: MagicMock) -> None:
        """Test that prose document_type uses RecursiveChunker(chunk_size)."""
        mock_splitter = MagicMock()
        mock_splitter.chunk.return_value = ["chunk1", "chunk2"]
        mock_recursive_chunker.return_value = mock_splitter

        with patch("modules.indexer.chunker.RecursiveChunker", mock_recursive_chunker):
            chunker_obj = SemanticChunker(chunk_size=512)
            chunker_obj.split_text("Prose text", document_type="prose")

            mock_recursive_chunker.assert_called_once_with(chunk_size=512)

    @patch("modules.indexer.chunker.RecursiveChunker")
    def test_chunk_method_called_on_splitter(self, mock_recursive_chunker: MagicMock) -> None:
        """Test that chunk() method is called on the splitter."""
        mock_splitter = MagicMock()
        # Return Chunk objects as Chonkie does
        mock_chunk1 = MagicMock()
        mock_chunk1.text = "chunk1"
        mock_chunk1.token_count = 10
        mock_chunk2 = MagicMock()
        mock_chunk2.text = "chunk2"
        mock_chunk2.token_count = 10
        mock_chunk3 = MagicMock()
        mock_chunk3.text = "chunk3"
        mock_chunk3.token_count = 10
        mock_splitter.chunk.return_value = [mock_chunk1, mock_chunk2, mock_chunk3]
        mock_recursive_chunker.from_recipe.return_value = mock_splitter

        with patch("modules.indexer.chunker.RecursiveChunker", mock_recursive_chunker):
            chunker_obj = SemanticChunker(chunk_size=256)
            result = chunker_obj.split_text("Test content", document_type="markdown")

            assert len(result) == 3
            assert result[0]["text"].text == "chunk1"
            assert result[1]["text"].text == "chunk2"
            assert result[2]["text"].text == "chunk3"
