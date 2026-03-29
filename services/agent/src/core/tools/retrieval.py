"""Retrieval tool for RAG-based document search with structured output."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import SystemConfig
from core.providers import get_rag_manager
from core.tools.base import Tool

# Default threshold used when no SystemConfig value is set
DEFAULT_RAG_SUFFICIENCY_THRESHOLD = 0.65


class RetrievalTool(Tool):
    """Retrieves relevant documents from the RAG vector store with structured output.

    Wraps IRAGManager.retrieve() with context isolation, structured scoring metrics,
    and attempt capping to prevent infinite loops.
    """

    name = "rag_search"
    description = (
        "Retrieves relevant documents from the knowledge base using semantic search. "
        "Returns structured results with relevance scores and sufficiency assessment. "
        "Context-isolated: only retrieves documents for the current user's context."
    )
    category = "domain"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query to find relevant documents.",
            },
            "top_k": {
                "type": "integer",
                "description": "Maximum number of results to return (default: 5).",
                "default": 5,
                "minimum": 1,
                "maximum": 20,
            },
            "collection_name": {
                "type": "string",
                "description": "Optional collection name to search (default: agent-memories).",
            },
        },
        "required": ["query"],
    }
    activity_hint = {"query": 'Retrieving: "{query}"'}

    # Class-level attempt tracking for capping (3 calls per plan step max)
    _attempt_counts: dict[str, int] = {}
    _MAX_ATTEMPTS = 3

    def __init__(self) -> None:
        """Initialize the retrieval tool.

        Note: The sufficiency threshold is loaded dynamically from SystemConfig
        at runtime via _get_threshold(). The instance attribute is only used
        as a fallback cache.
        """
        self._sufficiency_threshold: float = DEFAULT_RAG_SUFFICIENCY_THRESHOLD

    async def _get_threshold(self, db_session: AsyncSession | None = None) -> float:
        """Get the RAG sufficiency threshold from SystemConfig.

        Reads 'rag_retrieval_min_score' from SystemConfig with fallback
        to the default value (0.65) if not configured.

        Args:
            db_session: Optional database session. If not provided, the default
                threshold will be returned.

        Returns:
            The threshold value as a float (default: 0.65).
        """
        if db_session is None:
            return self._sufficiency_threshold

        try:
            stmt = select(SystemConfig).where(SystemConfig.key == "rag_retrieval_min_score")
            result = await db_session.execute(stmt)
            config = result.scalar_one_or_none()

            if config and config.value:
                # SystemConfig stores value as JSONB, so it could be a string or number
                value = config.value
                if isinstance(value, str):
                    return float(value)
                elif isinstance(value, (int, float)):
                    return float(value)

            return DEFAULT_RAG_SUFFICIENCY_THRESHOLD
        except Exception:
            # If anything goes wrong, fall back to default
            return DEFAULT_RAG_SUFFICIENCY_THRESHOLD

    async def run(
        self,
        query: str,
        top_k: int = 5,
        collection_name: str | None = None,
        context_id: str | None = None,
        db_session: AsyncSession | None = None,
    ) -> str:
        """Execute RAG retrieval with structured output.

        Args:
            query: The search query.
            top_k: Maximum number of results (default: 5).
            collection_name: Optional collection override.
            context_id: Context ID for isolation (injected by executor).
            db_session: Optional database session for threshold lookup.

        Returns:
            JSON string with structured retrieval results including:
            - results: list of document dicts
            - result_count: number of results
            - min_score: minimum relevance score
            - max_score: maximum relevance score
            - avg_score: average relevance score
            - retrieval_sufficient: bool indicating if avg_score >= threshold
        """
        # Attempt capping per context+query combination
        attempt_key = f"{context_id}:{query}" if context_id else query
        current_attempts = self._attempt_counts.get(attempt_key, 0)
        if current_attempts >= self._MAX_ATTEMPTS:
            return json.dumps(
                {
                    "results": [],
                    "result_count": 0,
                    "min_score": 0.0,
                    "max_score": 0.0,
                    "avg_score": 0.0,
                    "retrieval_sufficient": False,
                    "error": f"Attempt cap exceeded (max {self._MAX_ATTEMPTS} per plan step)",
                }
            )
        self._attempt_counts[attempt_key] = current_attempts + 1

        # Build context isolation filters
        filters: dict[str, Any] = {}
        if context_id:
            filters["context_id"] = context_id

        # Retrieve from RAG
        rag = get_rag_manager()
        results = await rag.retrieve(
            query=query,
            top_k=top_k,
            filters=filters if filters else None,
            collection_name=collection_name,
        )

        # Calculate score metrics
        scores = [r.get("score", 0.0) for r in results if "score" in r]

        if scores:
            min_score = min(scores)
            max_score = max(scores)
            avg_score = sum(scores) / len(scores)
        else:
            min_score = 0.0
            max_score = 0.0
            avg_score = 0.0

        # Get dynamic threshold from SystemConfig (or use default)
        threshold = await self._get_threshold(db_session)

        # Determine sufficiency based on average score threshold
        retrieval_sufficient = avg_score >= threshold

        # Build structured response
        output = {
            "results": results,
            "result_count": len(results),
            "min_score": round(min_score, 4),
            "max_score": round(max_score, 4),
            "avg_score": round(avg_score, 4),
            "retrieval_sufficient": retrieval_sufficient,
            "threshold": round(threshold, 4),  # Include actual threshold used for transparency
        }

        return json.dumps(output)
