"""Web search tool used to find information on the internet."""

from __future__ import annotations

import logging

import httpx

from .base import Tool, ToolError

LOGGER = logging.getLogger(__name__)


class WebSearchTool(Tool):
    """Perform a web search using the internal webfetch service (backed by SearXNG)."""

    name = "web_search"
    description = "Search the web for a given query. Returns a list of relevant results with titles, URLs, and snippets."

    def __init__(
        self,
        base_url: str,
        *,
        max_results: int = 5,
        lang: str = "en",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._max_results = max_results
        self._lang = lang

    async def run(self, query: str) -> str:
        endpoint = f"{self._base_url}/search"
        params = {
            "q": query,
            "k": self._max_results,
            "lang": self._lang,
        }

        LOGGER.info(f"Searching web for: '{query}'")

        async with httpx.AsyncClient() as client:
            try:
                # The fetcher service uses GET for search
                response = await client.get(endpoint, params=params, timeout=30.0)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ToolError(f"Web search failed: {exc}") from exc

        try:
            data = response.json()
        except ValueError:
            raise ToolError("Invalid JSON response from search service")

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
