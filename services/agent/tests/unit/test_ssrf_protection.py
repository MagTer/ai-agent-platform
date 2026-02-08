"""Tests for SSRF protection in WebFetcher."""

import pytest

from modules.fetcher import WebFetcher


@pytest.mark.asyncio
async def test_validate_url_blocks_private_ips() -> None:
    """Test that private IP addresses are blocked."""
    fetcher = WebFetcher()

    # Test various private IP addresses
    blocked_urls = [
        "http://127.0.0.1/",
        "http://localhost/",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://169.254.169.254/latest/meta-data/",  # AWS metadata
    ]

    for url in blocked_urls:
        with pytest.raises(ValueError, match="Blocked|DNS resolution failed"):
            await fetcher._validate_url(url)


@pytest.mark.asyncio
async def test_validate_url_blocks_internal_hostnames() -> None:
    """Test that internal Docker service hostnames are blocked."""
    fetcher = WebFetcher()

    blocked_hostnames = [
        "http://postgres:5432/",
        "http://qdrant:6333/",
        "http://litellm:4000/",
        "http://redis:6379/",
        "http://searxng:8080/",
    ]

    for url in blocked_hostnames:
        with pytest.raises(ValueError, match="Blocked internal hostname"):
            await fetcher._validate_url(url)


@pytest.mark.asyncio
async def test_validate_url_blocks_non_http_schemes() -> None:
    """Test that non-HTTP(S) schemes are blocked."""
    fetcher = WebFetcher()

    blocked_schemes = [
        "ftp://example.com/",
        "file:///etc/passwd",
        "gopher://example.com/",
        "data:text/plain,hello",
    ]

    for url in blocked_schemes:
        with pytest.raises(ValueError, match="Blocked URL scheme"):
            await fetcher._validate_url(url)


@pytest.mark.asyncio
async def test_validate_url_allows_public_urls() -> None:
    """Test that public URLs are allowed."""
    fetcher = WebFetcher()

    # These should not raise exceptions
    public_urls = [
        "https://www.google.com/",
        "https://github.com/",
        "http://example.com/",
        "https://api.openai.com/v1/models",
    ]

    for url in public_urls:
        # Should not raise
        await fetcher._validate_url(url)


@pytest.mark.asyncio
async def test_fetch_validates_url() -> None:
    """Test that fetch() calls validation."""
    fetcher = WebFetcher()

    # Try to fetch a blocked URL
    with pytest.raises(ValueError, match="Blocked"):
        await fetcher.fetch("http://postgres:5432/")

    await fetcher.close()
