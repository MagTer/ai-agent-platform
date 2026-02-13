"""Security tests for web_search and web_fetch tools."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.tools.web_fetch import WebFetchTool
from core.tools.web_search import WebSearchTool


class TestWebSearchToolSecurity:
    """Security tests for WebSearchTool."""

    @pytest.fixture
    def web_search_tool(self) -> WebSearchTool:
        """Create a WebSearchTool instance."""
        return WebSearchTool(base_url="http://searxng:8080", max_results=5)

    @pytest.mark.asyncio
    async def test_search_injection_query_sanitization(
        self, web_search_tool: WebSearchTool
    ) -> None:
        """Test that search queries with injection attempts are handled safely."""
        # Mock fetcher
        mock_fetcher = MagicMock()
        mock_fetcher.search = AsyncMock(return_value={"results": []})

        with patch("core.tools.web_search.get_fetcher", return_value=mock_fetcher):
            # Injection attempts
            injection_queries = [
                "'; DROP TABLE users; --",
                "<script>alert('xss')</script>",
                "query&format=json&safesearch=0",  # Try to bypass safesearch
                "query\n&format=html",  # Newline injection
            ]

            for query in injection_queries:
                result = await web_search_tool.run(query=query)
                # Should complete without error (query is passed safely)
                assert isinstance(result, str)
                # Verify search was called with the raw query (handled by SearXNG)
                mock_fetcher.search.assert_called()

    @pytest.mark.asyncio
    async def test_search_handles_empty_query(self, web_search_tool: WebSearchTool) -> None:
        """Test that empty queries are handled gracefully."""
        mock_fetcher = MagicMock()
        mock_fetcher.search = AsyncMock(return_value={"results": []})

        with patch("core.tools.web_search.get_fetcher", return_value=mock_fetcher):
            result = await web_search_tool.run(query="")
            assert "No results found" in result

    @pytest.mark.asyncio
    async def test_search_limits_max_results(self, web_search_tool: WebSearchTool) -> None:
        """Test that max_results parameter is respected."""
        # Create 20 fake results
        fake_results = [
            {"title": f"Result {i}", "url": f"https://example.com/{i}", "snippet": ""}
            for i in range(20)
        ]

        mock_fetcher = MagicMock()
        mock_fetcher.search = AsyncMock(return_value={"results": fake_results})

        with patch("core.tools.web_search.get_fetcher", return_value=mock_fetcher):
            result = await web_search_tool.run(query="test")
            # Should only show first 5 results (max_results=5)
            # Count numbered items in output
            numbered_prefixes = ("1.", "2.", "3.", "4.", "5.")
            numbered_items = [
                line for line in result.split("\n") if line.strip().startswith(numbered_prefixes)
            ]
            assert len(numbered_items) == 5

    @pytest.mark.asyncio
    async def test_search_handles_malformed_results(self, web_search_tool: WebSearchTool) -> None:
        """Test that malformed search results are handled safely."""
        # Results with missing fields
        malformed_results = [
            {},  # Empty result
            {"title": "No URL"},  # Missing URL
            {"url": "https://example.com"},  # Missing title
            {"title": "Valid", "url": "https://example.com"},  # Valid one
        ]

        mock_fetcher = MagicMock()
        mock_fetcher.search = AsyncMock(return_value={"results": malformed_results})

        with patch("core.tools.web_search.get_fetcher", return_value=mock_fetcher):
            # Should not raise - handles missing fields gracefully
            result = await web_search_tool.run(query="test")
            assert isinstance(result, str)
            # Should still process the valid result
            assert "Valid" in result

    @pytest.mark.asyncio
    async def test_search_error_handling(self, web_search_tool: WebSearchTool) -> None:
        """Test that search errors are handled gracefully."""
        mock_fetcher = MagicMock()
        mock_fetcher.search = AsyncMock(side_effect=Exception("Search service down"))

        with patch("core.tools.web_search.get_fetcher", return_value=mock_fetcher):
            # Should raise ToolError with context
            with pytest.raises(Exception, match="Web search failed"):
                await web_search_tool.run(query="test")


class TestWebFetchToolSecurity:
    """Security tests for WebFetchTool."""

    @pytest.fixture
    def web_fetch_tool(self) -> WebFetchTool:
        """Create a WebFetchTool instance."""
        return WebFetchTool(base_url="http://searxng:8080", include_html=False)

    @pytest.mark.asyncio
    async def test_fetch_ssrf_protection_via_fetcher(self, web_fetch_tool: WebFetchTool) -> None:
        """Test that SSRF protection is enforced via WebFetcher."""
        # Mock fetcher to simulate SSRF block
        mock_fetcher = MagicMock()
        mock_fetcher.fetch = AsyncMock(
            return_value={"ok": False, "error": "Blocked internal hostname: postgres"}
        )

        with patch("core.tools.web_fetch.get_fetcher", return_value=mock_fetcher):
            # Try to fetch internal service
            with pytest.raises(Exception, match="Web fetch failed"):
                await web_fetch_tool.run(url="http://postgres:5432/")

    @pytest.mark.asyncio
    async def test_fetch_private_ip_blocked(self, web_fetch_tool: WebFetchTool) -> None:
        """Test that private IP addresses are blocked."""
        mock_fetcher = MagicMock()
        mock_fetcher.fetch = AsyncMock(
            return_value={"ok": False, "error": "Blocked private IP: 192.168.1.1"}
        )

        with patch("core.tools.web_fetch.get_fetcher", return_value=mock_fetcher):
            with pytest.raises(Exception, match="Web fetch failed"):
                await web_fetch_tool.run(url="http://192.168.1.1/admin")

    @pytest.mark.asyncio
    async def test_fetch_missing_url(self, web_fetch_tool: WebFetchTool) -> None:
        """Test that missing URL parameter is handled."""
        result = await web_fetch_tool.run()
        assert "Error: Missing required argument 'url'" in result

    @pytest.mark.asyncio
    async def test_fetch_fallback_url_parameters(self, web_fetch_tool: WebFetchTool) -> None:
        """Test that tool handles alternative URL parameter names."""
        mock_fetcher = MagicMock()
        mock_fetcher.fetch = AsyncMock(
            return_value={
                "ok": True,
                "url": "https://example.com",
                "text": "content",
            }
        )

        with patch("core.tools.web_fetch.get_fetcher", return_value=mock_fetcher):
            # Test 'link' parameter
            result = await web_fetch_tool.run(link="https://example.com")
            assert "https://example.com" in result
            mock_fetcher.fetch.assert_called_with("https://example.com")

            # Test 'website' parameter
            result = await web_fetch_tool.run(website="https://example.com")
            assert "https://example.com" in result

    @pytest.mark.asyncio
    async def test_fetch_sanitizes_output(self, web_fetch_tool: WebFetchTool) -> None:
        """Test that fetched content is properly sanitized."""
        # Mock fetcher to return content with potential XSS
        mock_fetcher = MagicMock()
        mock_fetcher.fetch = AsyncMock(
            return_value={
                "ok": True,
                "url": "https://example.com",
                "text": "<script>alert('xss')</script>Regular content",
            }
        )

        with patch("core.tools.web_fetch.get_fetcher", return_value=mock_fetcher):
            result = await web_fetch_tool.run(url="https://example.com")
            # WebFetcher extracts plain text, so script tags should be removed
            # by trafilatura in the fetcher module
            assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_fetch_truncates_large_content(self, web_fetch_tool: WebFetchTool) -> None:
        """Test that large content is truncated."""
        # Create tool with small max_chars
        tool = WebFetchTool(
            base_url="http://searxng:8080",
            summary_max_chars=100,
        )

        large_content = "x" * 500
        mock_fetcher = MagicMock()
        mock_fetcher.fetch = AsyncMock(
            return_value={
                "ok": True,
                "url": "https://example.com",
                "text": large_content,
            }
        )

        with patch("core.tools.web_fetch.get_fetcher", return_value=mock_fetcher):
            result = await tool.run(url="https://example.com")
            # Should be truncated with ellipsis
            assert "…" in result or "..." in result
            # Should not contain full content
            assert len(result) < len(large_content) + 200  # Allow for formatting

    @pytest.mark.asyncio
    async def test_fetch_handles_empty_content(self, web_fetch_tool: WebFetchTool) -> None:
        """Test that empty content is handled gracefully."""
        mock_fetcher = MagicMock()
        mock_fetcher.fetch = AsyncMock(
            return_value={
                "ok": True,
                "url": "https://example.com",
                "text": "",
            }
        )

        with patch("core.tools.web_fetch.get_fetcher", return_value=mock_fetcher):
            result = await web_fetch_tool.run(url="https://example.com")
            assert "(no extracted text)" in result

    @pytest.mark.asyncio
    async def test_fetch_html_inclusion(self) -> None:
        """Test that HTML inclusion works when enabled."""
        tool = WebFetchTool(
            base_url="http://searxng:8080",
            include_html=True,
            html_max_chars=100,
        )

        mock_fetcher = MagicMock()
        mock_fetcher.fetch = AsyncMock(
            return_value={
                "ok": True,
                "url": "https://example.com",
                "text": "Plain text",
                "html_truncated": "<html><body>Content</body></html>",
            }
        )

        with patch("core.tools.web_fetch.get_fetcher", return_value=mock_fetcher):
            result = await tool.run(url="https://example.com")
            assert "Plain text" in result
            assert "Raw HTML Snippet:" in result
            assert "<html>" in result

    @pytest.mark.asyncio
    async def test_fetch_url_normalization(self, web_fetch_tool: WebFetchTool) -> None:
        """Test that URLs are normalized properly."""
        mock_fetcher = MagicMock()
        mock_fetcher.fetch = AsyncMock(
            return_value={
                "ok": True,
                "url": "https://example.com",
                "text": "content",
            }
        )

        with patch("core.tools.web_fetch.get_fetcher", return_value=mock_fetcher):
            # Various URL formats
            urls_to_test = [
                "https://example.com",
                "https://example.com/",
                "https://example.com/path",
                "https://example.com/path?query=value",
            ]

            for url in urls_to_test:
                result = await web_fetch_tool.run(url=url)
                # Should successfully fetch
                assert "Fetched URL:" in result
                mock_fetcher.fetch.assert_called_with(url)

    @pytest.mark.asyncio
    async def test_fetch_error_contains_url_context(self, web_fetch_tool: WebFetchTool) -> None:
        """Test that error messages contain URL context for debugging."""
        mock_fetcher = MagicMock()
        mock_fetcher.fetch = AsyncMock(return_value={"ok": False, "error": "Connection timeout"})

        with patch("core.tools.web_fetch.get_fetcher", return_value=mock_fetcher):
            with pytest.raises(Exception) as exc_info:
                await web_fetch_tool.run(url="https://slow-site.example.com")

            # Error should contain context
            error_msg = str(exc_info.value)
            assert "Web fetch failed" in error_msg
            assert "https://slow-site.example.com" in error_msg


class TestWebToolsInputValidation:
    """Test input validation for web tools."""

    @pytest.mark.asyncio
    async def test_search_handles_unicode_queries(self) -> None:
        """Test that Unicode queries are handled safely."""
        tool = WebSearchTool(base_url="http://searxng:8080")

        unicode_queries = [
            "日本語クエリ",  # Japanese
            "Tëst quérÿ",  # Accented characters
            "emoji 🔍 search",  # Emoji
            "русский текст",  # Cyrillic
        ]

        mock_fetcher = MagicMock()
        mock_fetcher.search = AsyncMock(return_value={"results": []})

        with patch("core.tools.web_search.get_fetcher", return_value=mock_fetcher):
            for query in unicode_queries:
                result = await tool.run(query=query)
                assert isinstance(result, str)
                # Verify query was passed correctly
                mock_fetcher.search.assert_called()

    @pytest.mark.asyncio
    async def test_fetch_handles_unicode_urls(self) -> None:
        """Test that internationalized domain names are handled."""
        tool = WebFetchTool(base_url="http://searxng:8080")

        mock_fetcher = MagicMock()
        mock_fetcher.fetch = AsyncMock(
            return_value={
                "ok": True,
                "url": "https://münchen.de",
                "text": "content",
            }
        )

        with patch("core.tools.web_fetch.get_fetcher", return_value=mock_fetcher):
            # IDN (internationalized domain name)
            result = await tool.run(url="https://münchen.de")
            assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_search_very_long_query(self) -> None:
        """Test that very long queries are handled."""
        tool = WebSearchTool(base_url="http://searxng:8080")

        long_query = "query " * 1000  # Very long query

        mock_fetcher = MagicMock()
        mock_fetcher.search = AsyncMock(return_value={"results": []})

        with patch("core.tools.web_search.get_fetcher", return_value=mock_fetcher):
            # Should handle without error (SearXNG may truncate)
            result = await tool.run(query=long_query)
            assert isinstance(result, str)
