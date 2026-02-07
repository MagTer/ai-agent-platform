import asyncio
import hashlib
import json
import logging
import os
import time
from collections import deque
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx
import trafilatura
from litellm import acompletion

from core.protocols import IRAGManager

logger = logging.getLogger(__name__)


class _PlainTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._chunks.append(data.strip())

    def get_text(self) -> str:
        return "\n".join(self._chunks)


class WebFetcher:
    def __init__(self, rag_manager: IRAGManager | None = None) -> None:
        self.searxng_url = os.getenv("SEARXNG_URL", "http://searxng:8080")
        self.request_timeout = int(os.getenv("FETCHER_REQUEST_TIMEOUT", "15"))
        self.max_chars = int(os.getenv("FETCHER_MAX_CHARS", "12000"))
        # Respect CACHE_DIR or default to user home cache.
        # Fallback to /app/.cache for legacy Docker.
        default_cache = Path(os.getenv("HOME", "/root")) / ".cache" / "agent-fetcher"
        if os.access("/app", os.W_OK):
            default_cache = Path("/app/.cache")

        self.cache_dir = Path(os.getenv("CACHE_DIR", str(default_cache)))
        self.cache_ttl = int(os.getenv("CACHE_TTL", "86400"))

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.http_client = httpx.AsyncClient(
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=50,
                max_keepalive_connections=20,
            ),
        )
        self.rag_manager = rag_manager

        # Simple Rate Limiting
        self._hits: deque[float] = deque()
        self.rate_window = 60
        self.rate_max_req = 60

    async def close(self) -> None:
        await self.http_client.aclose()
        if self.rag_manager:
            await self.rag_manager.close()

    def _check_rate_limit(self) -> None:
        now = time.time()
        while self._hits and now - self._hits[0] > self.rate_window:
            self._hits.popleft()
        if len(self._hits) >= self.rate_max_req:
            logger.warning("Rate limit hit")
            # In a module, we might want to sleep or raise. Sleep is nicer.
            # But async sleep cannot be done in sync function.
            # For now, we proceed but log. Use external rate limiter if needed.
            pass
        self._hits.append(now)

    def _cache_key(self, s: str) -> str:
        return hashlib.sha256(s.encode("utf-8")).hexdigest()

    def _cache_get(self, url: str) -> dict[str, Any] | None:
        p = self.cache_dir / self._cache_key(url)
        if p.exists() and (time.time() - p.stat().st_mtime) < self.cache_ttl:
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
        return None

    def _cache_set(self, url: str, data: dict[str, Any]) -> None:
        (self.cache_dir / self._cache_key(url)).write_text(json.dumps(data), encoding="utf-8")

    def _extract_text(self, html: str) -> str:
        if trafilatura:
            return trafilatura.extract(html, include_images=False, include_tables=False) or ""
        # Fallback
        parser = _PlainTextExtractor()
        parser.feed(html)
        return parser.get_text()

    async def fetch(self, url: str) -> dict[str, Any]:
        cached = self._cache_get(url)
        if cached:
            return cached

        self._check_rate_limit()
        try:
            # Use realistic browser headers to avoid 403 blocks
            user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
            accept_header = (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            )
            headers = {
                "User-Agent": user_agent,
                "Accept": accept_header,
                "Accept-Language": "sv-SE,sv;q=0.9,en-US;q=0.8,en;q=0.7",
                # Don't specify Accept-Encoding - let httpx handle it
                # (brotli 'br' is not auto-decompressed by httpx)
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
            r = await self.http_client.get(
                url,
                timeout=self.request_timeout,
                headers=headers,
            )
            r.raise_for_status()
            raw_html = r.text
            text = await asyncio.to_thread(self._extract_text, raw_html)
            text = text.strip()
            if len(text) > self.max_chars:
                text = text[: self.max_chars] + "\n...\n"

            data = {
                "url": url,
                "ok": True,
                "text": text,
                "html_truncated": raw_html[:20000],
            }
            self._cache_set(url, data)
            return data
        except Exception as e:
            logger.error(f"Fetch failed for {url}: {e}")
            # Don't cache failed responses - they should be retried
            return {"url": url, "ok": False, "error": str(e), "text": ""}

    async def search(self, query: str, k: int = 5, lang: str = "en") -> dict[str, Any]:
        url = self.searxng_url.rstrip("/") + "/search"
        params: dict[str, Any] = {
            "q": query,
            "format": "json",
            "language": lang,
            "safesearch": 1,
        }
        # Let exceptions propagate to the Tool for better visibility
        r = await self.http_client.get(url, params=params, timeout=self.request_timeout)
        r.raise_for_status()
        data = r.json()
        results = []
        for item in data.get("results", [])[:k]:
            results.append(
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "snippet": item.get("content") or item.get("snippet"),
                }
            )
        return {"query": query, "results": results}

    async def p_search(self, query: str, k: int = 5) -> dict[str, Any]:
        return await self.search(query, k=k)

    async def research(
        self, query: str, k: int = 5, model: str = "gpt-3.5-turbo"
    ) -> dict[str, Any]:
        if not self.rag_manager:
            raise RuntimeError("RAGManager not configured for research operation")

        # 1. Parallel: Search Web + Search Memory
        search_task = asyncio.create_task(self.search(query, k=k))
        mem_task = asyncio.create_task(self.rag_manager.retrieve(query, top_k=k))

        search_res = await search_task
        mem_res = await mem_task

        web_urls = [r["url"] for r in search_res["results"] if r.get("url")]

        # 2. Fetch Web Content
        web_extracts = await asyncio.gather(*(self.fetch(u) for u in web_urls))

        # 3. Merge
        # Prefer memory if needed, but here we just list them.
        all_extracts = mem_res + list(web_extracts)

        # 4. Summarize with LiteLLM
        summary = await self._summarize(query, all_extracts, model)

        return {
            "query": query,
            "sources": [e.get("url") for e in all_extracts if e.get("url")],
            "summary": summary,
        }

    async def _summarize(self, query: str, items: list[dict[str, Any]], model: str) -> str:
        chunks = []
        for i, item in enumerate(items, 1):
            if item.get("text"):
                chunks.append(f"Source [{i}] {item.get('url')}\n{item['text']}\n")

        if not chunks:
            return "No sources found."

        context = "\n\n".join(chunks[:6])  # Limit to 6

        prompt = f"""
        Query: {query}
        
        Context:
        {context}
        
        Summarize the key points from the context in a bulleted list. Cite sources as [n].
        """

        try:
            response = await acompletion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Summarization failed: {e}")
            return f"Error creating summary: {e}"


# Global instance
_fetcher: WebFetcher | None = None


def get_fetcher() -> WebFetcher:
    """Get global WebFetcher instance.

    Note: RAGManager must be injected later via set_rag_manager() or
    by directly setting fetcher.rag_manager if research() is needed.
    """
    global _fetcher
    if _fetcher is None:
        _fetcher = WebFetcher()
    return _fetcher
