from __future__ import annotations

import logging
from typing import Any

from modules.fetcher import get_fetcher

from .base import Tool, ToolError

LOGGER = logging.getLogger(__name__)


class WebFetchTool(Tool):
    """Call the internal webfetch module and return structured context."""

    name = "web_fetch"
    description = (
        "Retrieve rendered page context (and optional HTML) using the internal fetcher module. "
        "Args: url (str) - The URL to fetch."
    )

    def __init__(
        self,
        base_url: str,
        *,
        include_html: bool = False,
        summary_max_chars: int = 1200,
        html_max_chars: int = 4000,
    ) -> None:
        # base_url is ignored in the modular monolith
        self._include_html = include_html
        self._summary_max_chars = summary_max_chars
        self._html_max_chars = html_max_chars

    async def run(self, url: str | None = None, **kwargs: Any) -> str:
        if not url:
            # Fallback for when the agent puts the URL in a different arg or forgets it
            url = kwargs.get("link") or kwargs.get("website")

        if not url:
            return "Error: Missing required argument 'url'. Please provide the URL to fetch."

        LOGGER.info(f"Fetching URL: {url}")

        try:
            fetcher = get_fetcher()
            item = await fetcher.fetch(url)
        except Exception as exc:
            raise ToolError(f"Web fetch failed: {exc}") from exc

        if not item.get("ok", False):
            error_detail = item.get("error") or "unknown error"
            raise ToolError(f"Web fetch failed for {url}: {error_detail}")

        extracted_text = item.get("text")
        if not isinstance(extracted_text, str):
            extracted_text = ""
        extracted_text = extracted_text.strip()

        LOGGER.info(f"Fetched {len(extracted_text)} chars from {url}")

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
            raw_html = item.get("html_truncated") or item.get("html")
            if isinstance(raw_html, str) and raw_html.strip():
                html_snippet = raw_html[: self._html_max_chars]
                if len(raw_html) > self._html_max_chars:
                    html_snippet = html_snippet.rstrip() + "…"
                sections.append("")
                sections.append("Raw HTML Snippet:")
                sections.append(html_snippet)

        return "\n".join(sections)


__all__ = ["WebFetchTool"]
