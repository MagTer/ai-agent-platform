"""HTTP fetch tool used to augment the agent with browsing capability."""

from __future__ import annotations

import httpx

from .base import Tool, ToolError


class WebFetchTool(Tool):
    """Call the internal webfetch service and return the HTML payload."""

    name = "web_fetch"
    description = "Retrieve the rendered HTML content for a given URL using the webfetch service."

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    async def run(self, url: str) -> str:
        endpoint = f"{self._base_url}/fetch"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(endpoint, json={"url": url}, timeout=30.0)
                response.raise_for_status()
            except httpx.HTTPError as exc:  # pragma: no cover - network errors are environmental
                raise ToolError(f"Web fetch failed: {exc}") from exc
        data = response.json()
        content = data.get("content")
        if not isinstance(content, str):
            raise ToolError("Unexpected response from webfetch")
        return content


__all__ = ["WebFetchTool"]
