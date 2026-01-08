from __future__ import annotations

import logging

from core.providers import get_fetcher

from .base import Tool, ToolError

LOGGER = logging.getLogger(__name__)


class WebSearchTool(Tool):
    """Perform a web search using the internal webfetch module (backed by SearXNG)."""

    name = "web_search"
    description = (
        "Search the web for a given query. Returns a list of relevant results with titles, "
        "URLs, and snippets."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query to look up on the web.",
            },
        },
        "required": ["query"],
    }
    activity_hint = {"query": 'Searching: "{query}"'}

    def __init__(
        self,
        base_url: str,
        *,
        max_results: int = 5,
        lang: str = "en",
    ) -> None:
        # base_url is ignored in the modular monolith
        self._max_results = max_results
        self._lang = lang

    async def run(self, query: str) -> str:
        LOGGER.info(f"Searching web for: '{query}'")

        try:
            fetcher = get_fetcher()
            data = await fetcher.search(query, k=self._max_results, lang=self._lang)
        except Exception as exc:
            raise ToolError(f"Web search failed: {exc}") from exc

        results = data.get("results", [])
        LOGGER.info(f"Found {len(results)} results from SearXNG")

        if not results:
            return "No results found."

        # Format results for the agent
        output_lines = [f"Search results for '{query}':\n"]
        for i, res in enumerate(results, start=1):
            title = res.get("title", "No Title")
            url = res.get("url", "#")
            snippet = res.get("snippet", "").strip()

            output_lines.append(f"{i}. {title}")
            output_lines.append(f"   URL: {url}")
            if snippet:
                output_lines.append(f"   Snippet: {snippet}")
            output_lines.append("")

        return "\n".join(output_lines)
