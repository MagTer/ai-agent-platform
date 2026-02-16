import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import socket
import time
from collections import deque
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

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
    # Blocked hostnames (common internal Docker services)
    BLOCKED_HOSTNAMES = frozenset(
        {
            "postgres",
            "qdrant",
            "litellm",
            "redis",
            "searxng",
            "openwebui",
            "traefik",
            "webfetch",
            "embedder",
            "agent",
        }
    )

    # Private/reserved IP ranges
    PRIVATE_RANGES = [
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("169.254.0.0/16"),
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("fc00::/7"),
        ipaddress.ip_network("fe80::/10"),
    ]

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
            follow_redirects=False,  # Disable auto-redirect to validate each URL
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

    async def _cache_get(self, url: str) -> dict[str, Any] | None:
        p = self.cache_dir / self._cache_key(url)
        if not p.exists():
            return None

        # Check expiry
        stat = await asyncio.to_thread(p.stat)
        if (time.time() - stat.st_mtime) >= self.cache_ttl:
            return None

        # Read cache file asynchronously
        try:
            text = await asyncio.to_thread(p.read_text, encoding="utf-8")
            return json.loads(text)
        except (OSError, json.JSONDecodeError):
            logger.debug(
                "Failed to read or parse cache file %s, treating as missing", p, exc_info=True
            )
            return None

    async def _evict_old_cache_entries(self, max_entries: int = 1000) -> None:
        """Evict oldest cache entries when limit exceeded."""
        # Quick count check before expensive sort+stat
        entry_count = sum(1 for _ in self.cache_dir.iterdir())
        if entry_count < max_entries:
            return

        # Get all cache files sorted by modification time
        entries = await asyncio.to_thread(
            lambda: sorted(self.cache_dir.glob("*"), key=lambda p: p.stat().st_mtime)
        )

        # Delete oldest entries if we exceed the limit
        while len(entries) >= max_entries:
            oldest = entries.pop(0)
            await asyncio.to_thread(oldest.unlink, missing_ok=True)

    async def _cache_set(self, url: str, data: dict[str, Any]) -> None:
        # Evict old entries before writing new one
        await self._evict_old_cache_entries()

        # Write cache file asynchronously
        cache_path = self.cache_dir / self._cache_key(url)
        content = json.dumps(data)
        await asyncio.to_thread(cache_path.write_text, content, encoding="utf-8")

    def _extract_text(self, html: str) -> str:
        if trafilatura:
            return trafilatura.extract(html, include_images=False, include_tables=False) or ""
        # Fallback
        parser = _PlainTextExtractor()
        parser.feed(html)
        return parser.get_text()

    async def _validate_url(self, url: str) -> None:
        """Validate URL to prevent SSRF attacks.

        Blocks:
        - Non-HTTP(S) schemes
        - Private/reserved IP ranges
        - Common internal Docker service hostnames

        Args:
            url: The URL to validate

        Raises:
            ValueError: If the URL is blocked
        """
        parsed = urlparse(url)

        # Only allow HTTP(S)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Blocked URL scheme: {parsed.scheme}")

        hostname = parsed.hostname
        if not hostname:
            raise ValueError("URL missing hostname")

        # Block internal Docker service names
        if hostname.lower() in self.BLOCKED_HOSTNAMES:
            raise ValueError(f"Blocked internal hostname: {hostname}")

        # Resolve hostname to IP addresses (blocking call, wrap in to_thread)
        try:
            addr_info = await asyncio.to_thread(
                socket.getaddrinfo, hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
            )
        except socket.gaierror as e:
            raise ValueError(f"DNS resolution failed for {hostname}: {e}") from e

        # Check all resolved IPs
        for _family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            try:
                ip_addr = ipaddress.ip_address(ip_str)
            except ValueError:
                logger.debug(
                    "Invalid IP address from getaddrinfo: %s (unexpected)", ip_str, exc_info=True
                )
                continue

            # Check against private ranges
            for network in self.PRIVATE_RANGES:
                if ip_addr in network:
                    raise ValueError(f"Blocked private IP: {ip_str} (from {hostname})")

    async def fetch(self, url: str) -> dict[str, Any]:
        # Validate URL before checking cache to prevent cache poisoning
        await self._validate_url(url)

        cached = await self._cache_get(url)
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

            # Manual redirect following with SSRF validation
            current_url = url
            max_redirects = 10
            redirect_count = 0

            while redirect_count < max_redirects:
                r = await self.http_client.get(
                    current_url,
                    timeout=self.request_timeout,
                    headers=headers,
                )

                # Check for redirects (3xx status codes)
                if 300 <= r.status_code < 400:
                    redirect_url = r.headers.get("Location")
                    if not redirect_url:
                        break

                    # Handle relative redirects
                    if not redirect_url.startswith(("http://", "https://")):
                        redirect_url = urljoin(current_url, redirect_url)

                    # Validate redirect target to prevent SSRF via redirect
                    await self._validate_url(redirect_url)
                    current_url = redirect_url
                    redirect_count += 1
                    continue

                # Not a redirect, process the response
                r.raise_for_status()
                break

            if redirect_count >= max_redirects:
                raise ValueError(f"Too many redirects (>{max_redirects})")

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
            await self._cache_set(url, data)
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
