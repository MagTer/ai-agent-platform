"""Retrieval tool for RAG-based document search with structured output."""

from __future__ import annotations

import json
from typing import Any

from core.providers import get_rag_manager
from core.tools.base import Tool


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
        """Initialize the retrieval tool."""
        self._sufficiency_threshold = 0.65

    async def run(
        self,
        query: str,
        top_k: int = 5,
        collection_name: str | None = None,
        context_id: str | None = None,
    ) -> str:
        """Execute RAG retrieval with structured output.

        Args:
            query: The search query.
            top_k: Maximum number of results (default: 5).
            collection_name: Optional collection override.
            context_id: Context ID for isolation (injected by executor).

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
            return json.dumps({
                "results": [],
                "result_count": 0,
                "min_score": 0.0,
                "max_score": 0.0,
                "avg_score": 0.0,
                "retrieval_sufficient": False,
                "error": f"Attempt cap exceeded (max {self._MAX_ATTEMPTS} per plan step)",
            })
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

        # Determine sufficiency based on average score threshold
        retrieval_sufficient = avg_score >= self._sufficiency_threshold

        # Build structured response
        output = {
            "results": results,
            "result_count": len(results),
            "min_score": round(min_score, 4),
            "max_score": round(max_score, 4),
            "avg_score": round(avg_score, 4),
            "retrieval_sufficient": retrieval_sufficient,
        }

        return json.dumps(output)
