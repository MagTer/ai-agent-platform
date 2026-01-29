"""Tool to search TIBP Wiki."""

from __future__ import annotations

from core.providers import get_rag_manager
from core.tools.base import Tool


class TibpWikiSearchTool(Tool):
    """Searches the TIBP corporate wiki for guidelines and requirements."""

    name = "tibp_wiki_search"
    description = (
        "Searches the TIBP corporate wiki for guidelines, security requirements, and rules."
    )
    category = "tibp"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query to look up in the TIBP wiki.",
            },
        },
        "required": ["query"],
    }
    activity_hint = {"query": 'Searching TIBP Wiki: "{query}"'}

    async def run(self, query: str) -> str:
        """
        Search the wiki.

        Args:
            query: The search query.
        """
        rag = get_rag_manager()
        results = await rag.retrieve(query, top_k=5, collection_name="tibp-wiki")
        if not results:
            return "No relevant guidelines found in TIBP Wiki."

        output = [f"Found {len(results)} wiki pages:\n"]
        for r in results:
            output.append(f"--- [ {r.get('uri')} ] ---\n{r.get('text')}\n")

        return "\n".join(output)
