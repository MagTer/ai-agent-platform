"""Tool to search TIBP Wiki."""

from __future__ import annotations

from modules.rag import RAGManager

from .base import Tool


class TibpWikiSearchTool(Tool):
    """Searches the TIBP corporate wiki for guidelines and requirements."""

    name = "tibp_wiki_search"
    description = (
        "Searches the TIBP corporate wiki for guidelines, security requirements, and rules."
    )
    category = "tibp"

    async def run(self, query: str) -> str:
        """
        Search the wiki.

        Args:
            query: The search query.
        """
        rag = RAGManager()
        try:
            results = await rag.retrieve(query, top_k=5, collection_name="tibp-wiki")
            if not results:
                return "No relevant guidelines found in TIBP Wiki."

            output = [f"Found {len(results)} wiki pages:\n"]
            for r in results:
                output.append(f"--- [ {r.get('uri')} ] ---\n{r.get('text')}\n")

            return "\n".join(output)
        finally:
            await rag.close()
