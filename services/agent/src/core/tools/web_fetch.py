"""HTTP fetch tool used to augment the agent with browsing capability."""

from __future__ import annotations

import httpx

from .base import Tool, ToolError


class WebFetchTool(Tool):
    """Call the internal webfetch service and return structured context."""

    name = "web_fetch"
    description = (
        "Retrieve rendered page context (and optional HTML) using the webfetch service."
    )

    def __init__(
        self,
        base_url: str,
        *,
        include_html: bool = False,
        summary_max_chars: int = 1200,
        html_max_chars: int = 4000,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._include_html = include_html
        self._summary_max_chars = summary_max_chars
        self._html_max_chars = html_max_chars

    async def run(self, url: str) -> str:
        endpoint = f"{self._base_url}/fetch"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(endpoint, json={"url": url}, timeout=30.0)
                response.raise_for_status()
            except (
                httpx.HTTPError
            ) as exc:  # pragma: no cover - network errors are environmental
                raise ToolError(f"Web fetch failed: {exc}") from exc
        data = response.json()
        item = data.get("item")
        if not isinstance(item, dict):
            raise ToolError("Unexpected response from webfetch")

        if not item.get("ok", False):
            error_detail = item.get("error") or "unknown error"
            raise ToolError(f"Web fetch failed for {url}: {error_detail}")

        extracted_text = item.get("text")
        if not isinstance(extracted_text, str):
            extracted_text = ""
        extracted_text = extracted_text.strip()

        snippet = extracted_text[: self._summary_max_chars]
        if extracted_text and len(extracted_text) > self._summary_max_chars:
            snippet = snippet.rstrip() + "…"
        if not snippet:
            snippet = "(no extracted text)"

        sections = [f"Fetched URL: {item.get('url') or url}"]
        sections.append("")
        sections.append("Extracted Text Snippet:")
        sections.append(snippet)

        if self._include_html:
            raw_html = item.get("html")
            if isinstance(raw_html, str) and raw_html.strip():
                html_snippet = raw_html[: self._html_max_chars]
                if len(raw_html) > self._html_max_chars:
                    html_snippet = html_snippet.rstrip() + "…"
                sections.append("")
                sections.append("Raw HTML Snippet:")
                sections.append(html_snippet)

        return "\n".join(sections)


__all__ = ["WebFetchTool"]
