"""Tests for Retrieval tool (rag_search)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from core.tools.retrieval import RetrievalTool


@pytest.fixture
def retrieval_tool() -> RetrievalTool:
    """Create a RetrievalTool instance for testing."""
    return RetrievalTool()


@pytest.fixture
def mock_context_id() -> UUID:
    """Create a mock context ID."""
    return uuid4()


@pytest.fixture
def mock_rag_manager() -> AsyncMock:
    """Create a mock RAG manager."""
    return AsyncMock()


class TestRetrievalToolInit:
    """Test RetrievalTool initialization."""

    def test_tool_attributes(self, retrieval_tool: RetrievalTool) -> None:
        """Test that tool has correct attributes."""
        assert retrieval_tool.name == "rag_search"
        desc_lower = retrieval_tool.description.lower()
        assert "retriev" in desc_lower
        assert "knowledge base" in desc_lower
        assert retrieval_tool.category == "domain"

    def test_parameters_schema(self, retrieval_tool: RetrievalTool) -> None:
        """Test that parameters schema is valid."""
        params: dict[str, Any] = retrieval_tool.parameters
        assert params["type"] == "object"
        assert "query" in params["properties"]
        assert "top_k" in params["properties"]
        assert "collection_name" in params["properties"]
        assert params["required"] == ["query"]
        # Check constraints
        assert params["properties"]["top_k"]["minimum"] == 1
        assert params["properties"]["top_k"]["maximum"] == 20
        assert params["properties"]["top_k"]["default"] == 5

    def test_sufficiency_threshold(self, retrieval_tool: RetrievalTool) -> None:
        """Test that sufficiency threshold is set."""
        assert retrieval_tool._sufficiency_threshold == 0.65

    def test_attempt_capping_constants(self, retrieval_tool: RetrievalTool) -> None:
        """Test attempt capping constants."""
        assert retrieval_tool._MAX_ATTEMPTS == 3
        assert isinstance(retrieval_tool._attempt_counts, dict)


class TestRetrievalToolSuccess:
    """Test successful retrieval scenarios."""

    @pytest.mark.asyncio
    async def test_successful_retrieval(
        self,
        retrieval_tool: RetrievalTool,
        mock_rag_manager: AsyncMock,
        mock_context_id: UUID,
    ) -> None:
        """Test successful retrieval with results."""
        # Setup mock results with varying scores
        mock_results = [
            {
                "uri": "https://example.com/doc1",
                "text": "Content 1",
                "score": 0.85,
                "source": "memory",
            },
            {
                "uri": "https://example.com/doc2",
                "text": "Content 2",
                "score": 0.72,
                "source": "memory",
            },
            {
                "uri": "https://example.com/doc3",
                "text": "Content 3",
                "score": 0.68,
                "source": "memory",
            },
        ]
        mock_rag_manager.retrieve.return_value = mock_results

        with patch("core.tools.retrieval.get_rag_manager", return_value=mock_rag_manager):
            result = await retrieval_tool.run(
                query="test query",
                context_id=str(mock_context_id),
            )

        # Parse JSON response
        output = json.loads(result)

        # Verify structure
        assert "results" in output
        assert "result_count" in output
        assert "min_score" in output
        assert "max_score" in output
        assert "avg_score" in output
        assert "retrieval_sufficient" in output

        # Verify data
        assert output["result_count"] == 3
        assert output["min_score"] == 0.68
        assert output["max_score"] == 0.85
        assert output["avg_score"] == pytest.approx(0.75, abs=0.01)
        assert output["retrieval_sufficient"] is True  # 0.75 >= 0.65
        assert len(output["results"]) == 3

    @pytest.mark.asyncio
    async def test_retrieval_with_custom_top_k(
        self,
        retrieval_tool: RetrievalTool,
        mock_rag_manager: AsyncMock,
        mock_context_id: UUID,
    ) -> None:
        """Test retrieval with custom top_k parameter."""
        mock_rag_manager.retrieve.return_value = []

        with patch("core.tools.retrieval.get_rag_manager", return_value=mock_rag_manager):
            await retrieval_tool.run(
                query="test query",
                top_k=10,
                context_id=str(mock_context_id),
            )

        # Verify RAG manager was called with correct top_k
        call_kwargs = mock_rag_manager.retrieve.call_args[1]
        assert call_kwargs["top_k"] == 10

    @pytest.mark.asyncio
    async def test_retrieval_with_custom_collection(
        self,
        retrieval_tool: RetrievalTool,
        mock_rag_manager: AsyncMock,
        mock_context_id: UUID,
    ) -> None:
        """Test retrieval with custom collection name."""
        mock_rag_manager.retrieve.return_value = []

        with patch("core.tools.retrieval.get_rag_manager", return_value=mock_rag_manager):
            await retrieval_tool.run(
                query="test query",
                collection_name="custom-collection",
                context_id=str(mock_context_id),
            )

        # Verify RAG manager was called with correct collection
        call_kwargs = mock_rag_manager.retrieve.call_args[1]
        assert call_kwargs["collection_name"] == "custom-collection"


class TestRetrievalToolContextIsolation:
    """Test context_id filtering for multi-tenant isolation."""

    @pytest.mark.asyncio
    async def test_context_id_filter_applied(
        self,
        retrieval_tool: RetrievalTool,
        mock_rag_manager: AsyncMock,
        mock_context_id: UUID,
    ) -> None:
        """Test that context_id is passed as filter to RAG manager."""
        mock_rag_manager.retrieve.return_value = []

        with patch("core.tools.retrieval.get_rag_manager", return_value=mock_rag_manager):
            await retrieval_tool.run(
                query="test query",
                context_id=str(mock_context_id),
            )

        # Verify filters were passed
        call_kwargs = mock_rag_manager.retrieve.call_args[1]
        assert call_kwargs["filters"] is not None
        assert call_kwargs["filters"]["context_id"] == str(mock_context_id)

    @pytest.mark.asyncio
    async def test_no_context_id_no_filter(
        self,
        retrieval_tool: RetrievalTool,
        mock_rag_manager: AsyncMock,
    ) -> None:
        """Test that no filter is applied when context_id is None."""
        mock_rag_manager.retrieve.return_value = []

        with patch("core.tools.retrieval.get_rag_manager", return_value=mock_rag_manager):
            await retrieval_tool.run(
                query="test query",
                context_id=None,
            )

        # Verify no filters were passed
        call_kwargs = mock_rag_manager.retrieve.call_args[1]
        assert call_kwargs["filters"] is None


class TestRetrievalToolEmptyResults:
    """Test handling of empty results."""

    @pytest.mark.asyncio
    async def test_empty_results(
        self,
        retrieval_tool: RetrievalTool,
        mock_rag_manager: AsyncMock,
        mock_context_id: UUID,
    ) -> None:
        """Test response when no documents are found."""
        mock_rag_manager.retrieve.return_value = []

        with patch("core.tools.retrieval.get_rag_manager", return_value=mock_rag_manager):
            result = await retrieval_tool.run(
                query="test query",
                context_id=str(mock_context_id),
            )

        output = json.loads(result)

        assert output["results"] == []
        assert output["result_count"] == 0
        assert output["min_score"] == 0.0
        assert output["max_score"] == 0.0
        assert output["avg_score"] == 0.0
        assert output["retrieval_sufficient"] is False

    @pytest.mark.asyncio
    async def test_results_without_scores(
        self,
        retrieval_tool: RetrievalTool,
        mock_rag_manager: AsyncMock,
        mock_context_id: UUID,
    ) -> None:
        """Test handling of results that lack score field."""
        mock_results = [
            {"uri": "doc1", "text": "Content 1"},  # No score
            {"uri": "doc2", "text": "Content 2", "score": 0.75},
        ]
        mock_rag_manager.retrieve.return_value = mock_results

        with patch("core.tools.retrieval.get_rag_manager", return_value=mock_rag_manager):
            result = await retrieval_tool.run(
                query="test query",
                context_id=str(mock_context_id),
            )

        output = json.loads(result)

        # Only the second result has a score
        assert output["result_count"] == 2
        assert output["min_score"] == 0.75
        assert output["max_score"] == 0.75
        assert output["avg_score"] == 0.75


class TestRetrievalToolThresholdCalculation:
    """Test threshold calculation for retrieval_sufficient."""

    @pytest.mark.asyncio
    async def test_sufficient_above_threshold(
        self,
        retrieval_tool: RetrievalTool,
        mock_rag_manager: AsyncMock,
        mock_context_id: UUID,
    ) -> None:
        """Test retrieval_sufficient=True when avg >= 0.65."""
        mock_results = [
            {"uri": "doc1", "text": "Content", "score": 0.70},
        ]
        mock_rag_manager.retrieve.return_value = mock_results

        with patch("core.tools.retrieval.get_rag_manager", return_value=mock_rag_manager):
            result = await retrieval_tool.run(
                query="test query",
                context_id=str(mock_context_id),
            )

        output = json.loads(result)
        assert output["retrieval_sufficient"] is True

    @pytest.mark.asyncio
    async def test_insufficient_below_threshold(
        self,
        retrieval_tool: RetrievalTool,
        mock_rag_manager: AsyncMock,
        mock_context_id: UUID,
    ) -> None:
        """Test retrieval_sufficient=False when avg < 0.65."""
        mock_results = [
            {"uri": "doc1", "text": "Content", "score": 0.50},
            {"uri": "doc2", "text": "Content", "score": 0.60},
        ]
        mock_rag_manager.retrieve.return_value = mock_results

        with patch("core.tools.retrieval.get_rag_manager", return_value=mock_rag_manager):
            result = await retrieval_tool.run(
                query="test query",
                context_id=str(mock_context_id),
            )

        output = json.loads(result)
        avg = (0.50 + 0.60) / 2  # 0.55
        assert output["avg_score"] == pytest.approx(avg, abs=0.01)
        assert output["retrieval_sufficient"] is False  # 0.55 < 0.65

    @pytest.mark.asyncio
    async def test_exactly_at_threshold(
        self,
        retrieval_tool: RetrievalTool,
        mock_rag_manager: AsyncMock,
        mock_context_id: UUID,
    ) -> None:
        """Test retrieval_sufficient=True when avg == 0.65."""
        mock_results = [
            {"uri": "doc1", "text": "Content", "score": 0.65},
        ]
        mock_rag_manager.retrieve.return_value = mock_results

        with patch("core.tools.retrieval.get_rag_manager", return_value=mock_rag_manager):
            result = await retrieval_tool.run(
                query="test query",
                context_id=str(mock_context_id),
            )

        output = json.loads(result)
        assert output["avg_score"] == 0.65
        assert output["retrieval_sufficient"] is True  # 0.65 >= 0.65


class TestRetrievalToolAttemptCapping:
    """Test attempt capping to prevent infinite loops."""

    @pytest.mark.asyncio
    async def test_attempt_counting(
        self,
        retrieval_tool: RetrievalTool,
        mock_rag_manager: AsyncMock,
        mock_context_id: UUID,
    ) -> None:
        """Test that attempts are counted per context+query."""
        mock_rag_manager.retrieve.return_value = []

        with patch("core.tools.retrieval.get_rag_manager", return_value=mock_rag_manager):
            # First 3 calls should succeed
            for _i in range(3):
                result = await retrieval_tool.run(
                    query="same query",
                    context_id=str(mock_context_id),
                )
                output = json.loads(result)
                assert "error" not in output  # No error yet

            # 4th call should hit the cap
            result = await retrieval_tool.run(
                query="same query",
                context_id=str(mock_context_id),
            )
            output = json.loads(result)
            assert "error" in output
            assert "Attempt cap exceeded" in output["error"]
            assert output["result_count"] == 0

    @pytest.mark.asyncio
    async def test_attempt_counting_per_query(
        self,
        retrieval_tool: RetrievalTool,
        mock_rag_manager: AsyncMock,
        mock_context_id: UUID,
    ) -> None:
        """Test that attempt counting is isolated per query."""
        mock_rag_manager.retrieve.return_value = []

        with patch("core.tools.retrieval.get_rag_manager", return_value=mock_rag_manager):
            # Use 3 different queries
            for i in range(3):
                result = await retrieval_tool.run(
                    query=f"query {i}",
                    context_id=str(mock_context_id),
                )
                output = json.loads(result)
                assert "error" not in output  # All should succeed

    @pytest.mark.asyncio
    async def test_attempt_counting_no_context_id(
        self, retrieval_tool: RetrievalTool, mock_rag_manager: AsyncMock
    ) -> None:
        """Test attempt counting works without context_id."""
        mock_rag_manager.retrieve.return_value = []

        # Use unique query to avoid interference from previous tests
        # (attempt counter is class-level and persists across tests)
        unique_query = "unique query no context"

        with patch("core.tools.retrieval.get_rag_manager", return_value=mock_rag_manager):
            # First 3 calls should succeed
            for _i in range(3):
                result = await retrieval_tool.run(
                    query=unique_query,
                    context_id=None,
                )
                output = json.loads(result)
                assert "error" not in output

            # 4th call should hit the cap
            result = await retrieval_tool.run(
                query=unique_query,
                context_id=None,
            )
            output = json.loads(result)
            assert "error" in output
            assert "Attempt cap exceeded" in output["error"]


class TestRetrievalToolCollectionConstraints:
    """Test collection parameter constraints."""

    def test_collection_name_in_schema(self, retrieval_tool: RetrievalTool) -> None:
        """Test that collection_name is in the parameters schema."""
        params = retrieval_tool.parameters
        assert "collection_name" in params["properties"]
        # It's optional (no default means it can be null)
        assert "collection_name" not in params["required"]

    @pytest.mark.asyncio
    async def test_collection_name_passed_to_rag(
        self,
        retrieval_tool: RetrievalTool,
        mock_rag_manager: AsyncMock,
        mock_context_id: UUID,
    ) -> None:
        """Test that collection_name is passed through to RAG manager."""
        mock_rag_manager.retrieve.return_value = []

        with patch("core.tools.retrieval.get_rag_manager", return_value=mock_rag_manager):
            await retrieval_tool.run(
                query="test query",
                collection_name="my-collection",
                context_id=str(mock_context_id),
            )

        call_kwargs = mock_rag_manager.retrieve.call_args[1]
        assert call_kwargs["collection_name"] == "my-collection"

    @pytest.mark.asyncio
    async def test_none_collection_passed_to_rag(
        self,
        retrieval_tool: RetrievalTool,
        mock_rag_manager: AsyncMock,
        mock_context_id: UUID,
    ) -> None:
        """Test that None collection_name is passed to RAG manager."""
        mock_rag_manager.retrieve.return_value = []

        with patch("core.tools.retrieval.get_rag_manager", return_value=mock_rag_manager):
            await retrieval_tool.run(
                query="test query",
                collection_name=None,
                context_id=str(mock_context_id),
            )

        call_kwargs = mock_rag_manager.retrieve.call_args[1]
        assert call_kwargs["collection_name"] is None


class TestRetrievalToolScoreRounding:
    """Test that scores are properly rounded."""

    @pytest.mark.asyncio
    async def test_score_rounding(
        self,
        retrieval_tool: RetrievalTool,
        mock_rag_manager: AsyncMock,
        mock_context_id: UUID,
    ) -> None:
        """Test that scores are rounded to 4 decimal places."""
        mock_results = [
            {"uri": "doc1", "text": "Content", "score": 0.123456789},
            {"uri": "doc2", "text": "Content", "score": 0.987654321},
        ]
        mock_rag_manager.retrieve.return_value = mock_results

        with patch("core.tools.retrieval.get_rag_manager", return_value=mock_rag_manager):
            result = await retrieval_tool.run(
                query="test query",
                context_id=str(mock_context_id),
            )

        output = json.loads(result)
        # Verify rounding to 4 decimal places
        assert output["min_score"] == 0.1235
        assert output["max_score"] == 0.9877
        assert output["avg_score"] == pytest.approx(0.5556, abs=0.0001)


class TestRetrievalToolActivityHint:
    """Test activity hint for UI display."""

    def test_activity_hint_present(self, retrieval_tool: RetrievalTool) -> None:
        """Test that activity hint is configured."""
        assert retrieval_tool.activity_hint is not None
        assert "query" in retrieval_tool.activity_hint
        assert "{query}" in retrieval_tool.activity_hint["query"]
