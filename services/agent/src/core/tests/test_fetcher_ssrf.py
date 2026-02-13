"""Security tests for WebFetcher SSRF protection."""

from __future__ import annotations

import ipaddress
import socket
from unittest.mock import MagicMock, patch

import pytest

from modules.fetcher import WebFetcher


@pytest.fixture
def web_fetcher() -> WebFetcher:
    """Create a WebFetcher instance for testing."""
    return WebFetcher()


class TestWebFetcherSSRFProtection:
    """Test SSRF protection mechanisms in WebFetcher."""

    @pytest.mark.asyncio
    async def test_validate_url_blocks_non_http_schemes(self, web_fetcher: WebFetcher) -> None:
        """Test that non-HTTP(S) schemes are blocked."""
        blocked_schemes = [
            "ftp://example.com/file.txt",
            "file:///etc/passwd",
            "gopher://example.com/",
            "data:text/plain,hello",
            "javascript:alert('xss')",
            "ssh://git@github.com/repo.git",
        ]

        for url in blocked_schemes:
            with pytest.raises(ValueError, match="Blocked URL scheme"):
                await web_fetcher._validate_url(url)

    @pytest.mark.asyncio
    async def test_validate_url_allows_http_and_https(self, web_fetcher: WebFetcher) -> None:
        """Test that HTTP and HTTPS are allowed (but still validated)."""
        # Mock getaddrinfo to return a safe public IP
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            # Return a fake public IP (8.8.8.8)
            mock_getaddrinfo.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443))
            ]

            # Should not raise for valid public URLs
            await web_fetcher._validate_url("https://example.com")
            await web_fetcher._validate_url("http://example.com")

    @pytest.mark.asyncio
    async def test_validate_url_blocks_missing_hostname(self, web_fetcher: WebFetcher) -> None:
        """Test that URLs without hostnames are blocked."""
        with pytest.raises(ValueError, match="URL missing hostname"):
            await web_fetcher._validate_url("http://")

    @pytest.mark.asyncio
    async def test_validate_url_blocks_internal_docker_hostnames(
        self, web_fetcher: WebFetcher
    ) -> None:
        """Test that internal Docker service hostnames are blocked."""
        internal_hostnames = [
            "http://postgres/",
            "http://qdrant:6333",
            "http://litellm:4000",
            "http://redis:6379",
            "http://searxng:8080",
            "http://agent:8000",
            "http://POSTGRES/",  # Case insensitive
            "https://QDRANT/api",
        ]

        for url in internal_hostnames:
            with pytest.raises(ValueError, match="Blocked internal hostname"):
                await web_fetcher._validate_url(url)

    @pytest.mark.asyncio
    async def test_validate_url_blocks_private_ipv4_addresses(
        self, web_fetcher: WebFetcher
    ) -> None:
        """Test that private IPv4 addresses are blocked."""
        # Mock getaddrinfo to return private IPs
        private_ips = [
            ("10.0.0.1", "10.0.0.0/8"),
            ("172.16.0.1", "172.16.0.0/12"),
            ("192.168.1.1", "192.168.0.0/16"),
            ("127.0.0.1", "127.0.0.0/8 (localhost)"),
            ("169.254.1.1", "169.254.0.0/16 (link-local)"),
        ]

        for ip, _network_desc in private_ips:
            with patch("socket.getaddrinfo") as mock_getaddrinfo:
                mock_getaddrinfo.return_value = [
                    (socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 80))
                ]

                with pytest.raises(ValueError, match=f"Blocked private IP: {ip}"):
                    await web_fetcher._validate_url("http://example.com")

    @pytest.mark.asyncio
    async def test_validate_url_blocks_private_ipv6_addresses(
        self, web_fetcher: WebFetcher
    ) -> None:
        """Test that private IPv6 addresses are blocked."""
        private_ipv6 = [
            "::1",  # Loopback
            "fc00::1",  # Unique local
            "fe80::1",  # Link-local
        ]

        for ip in private_ipv6:
            with patch("socket.getaddrinfo") as mock_getaddrinfo:
                mock_getaddrinfo.return_value = [
                    (socket.AF_INET6, socket.SOCK_STREAM, 0, "", (ip, 80, 0, 0))
                ]

                with pytest.raises(ValueError, match=f"Blocked private IP: {ip}"):
                    await web_fetcher._validate_url("http://example.com")

    @pytest.mark.asyncio
    async def test_validate_url_handles_dns_resolution_failure(
        self, web_fetcher: WebFetcher
    ) -> None:
        """Test handling of DNS resolution failures."""
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.side_effect = socket.gaierror("Name resolution failed")

            with pytest.raises(ValueError, match="DNS resolution failed"):
                await web_fetcher._validate_url("http://nonexistent.invalid")

    @pytest.mark.asyncio
    async def test_validate_url_allows_public_ips(self, web_fetcher: WebFetcher) -> None:
        """Test that public IP addresses are allowed."""
        public_ips = [
            "8.8.8.8",  # Google DNS
            "1.1.1.1",  # Cloudflare DNS
            "2001:4860:4860::8888",  # Google DNS IPv6
        ]

        for ip in public_ips:
            with patch("socket.getaddrinfo") as mock_getaddrinfo:
                if ":" in ip:
                    # IPv6
                    mock_getaddrinfo.return_value = [
                        (socket.AF_INET6, socket.SOCK_STREAM, 0, "", (ip, 443, 0, 0))
                    ]
                else:
                    # IPv4
                    mock_getaddrinfo.return_value = [
                        (socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 443))
                    ]

                # Should not raise
                await web_fetcher._validate_url("http://example.com")

    @pytest.mark.asyncio
    async def test_fetch_validates_url_before_cache_lookup(self, web_fetcher: WebFetcher) -> None:
        """Test that URL validation happens before cache lookup to prevent cache poisoning."""
        # Mock cache to return a value (we want to verify validation happens BEFORE cache check)
        with patch.object(
            web_fetcher, "_cache_get", return_value={"url": "http://postgres/", "ok": True}
        ):
            with pytest.raises(ValueError, match="Blocked internal hostname"):
                await web_fetcher.fetch("http://postgres/")

    @pytest.mark.asyncio
    async def test_fetch_validates_redirect_targets(self, web_fetcher: WebFetcher) -> None:
        """Test that redirect targets are validated for SSRF."""
        # Mock HTTP client to return a redirect to private IP
        mock_response = MagicMock()
        mock_response.status_code = 302
        mock_response.headers = {"Location": "http://192.168.1.1/admin"}

        with patch.object(web_fetcher.http_client, "get", return_value=mock_response):
            # Mock DNS resolution for initial URL (public IP)
            with patch("socket.getaddrinfo") as mock_getaddrinfo:
                mock_getaddrinfo.side_effect = [
                    # First call: public IP for example.com
                    [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 80))],
                    # Second call: private IP for redirect target
                    [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.1", 80))],
                ]

                result = await web_fetcher.fetch("http://example.com")
                assert not result.get("ok", False)
                assert "Blocked private IP" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_fetch_handles_relative_redirects(self, web_fetcher: WebFetcher) -> None:
        """Test that relative redirects are converted to absolute URLs."""
        mock_responses = [
            MagicMock(status_code=302, headers={"Location": "/redirect-path"}),
            MagicMock(status_code=200, text="<html>Success</html>"),
        ]

        with patch.object(web_fetcher.http_client, "get", side_effect=mock_responses) as mock_get:
            # Mock DNS resolution (public IP)
            with patch("socket.getaddrinfo") as mock_getaddrinfo:
                mock_getaddrinfo.return_value = [
                    (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443))
                ]

                # Mock cache operations
                with patch.object(web_fetcher, "_cache_get", return_value=None):
                    with patch.object(web_fetcher, "_cache_set", return_value=None):
                        await web_fetcher.fetch("https://example.com")

                        # Verify second call used absolute URL
                        assert mock_get.call_count == 2
                        second_call_url = mock_get.call_args_list[1][0][0]
                        assert second_call_url == "https://example.com/redirect-path"

    @pytest.mark.asyncio
    async def test_fetch_enforces_max_redirects(self, web_fetcher: WebFetcher) -> None:
        """Test that excessive redirects are blocked."""
        # Create a redirect loop response
        mock_response = MagicMock()
        mock_response.status_code = 302
        mock_response.headers = {"Location": "https://example.com/redirect"}

        with patch.object(web_fetcher.http_client, "get", return_value=mock_response):
            with patch("socket.getaddrinfo") as mock_getaddrinfo:
                mock_getaddrinfo.return_value = [
                    (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443))
                ]

                result = await web_fetcher.fetch("https://example.com")
                assert not result.get("ok", False)
                assert "Too many redirects" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_fetch_raises_on_private_ip(self, web_fetcher: WebFetcher) -> None:
        """Test that fetch raises ValueError for private IPs."""
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 80))
            ]

            # Should raise ValueError with clear error message
            with pytest.raises(ValueError, match="Blocked private IP: 127.0.0.1"):
                await web_fetcher.fetch("http://localhost:8080/admin")


class TestWebFetcherPrivateRangesList:
    """Test that all expected private ranges are covered."""

    def test_private_ranges_ipv4(self) -> None:
        """Verify all RFC1918 private IPv4 ranges are included."""
        expected_ranges = [
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
            ipaddress.ip_network("127.0.0.0/8"),
            ipaddress.ip_network("169.254.0.0/16"),
        ]

        for expected in expected_ranges:
            assert expected in WebFetcher.PRIVATE_RANGES

    def test_private_ranges_ipv6(self) -> None:
        """Verify all private IPv6 ranges are included."""
        expected_ranges = [
            ipaddress.ip_network("::1/128"),  # Loopback
            ipaddress.ip_network("fc00::/7"),  # Unique local
            ipaddress.ip_network("fe80::/10"),  # Link-local
        ]

        for expected in expected_ranges:
            assert expected in WebFetcher.PRIVATE_RANGES


class TestWebFetcherIntegration:
    """Integration tests for WebFetcher (with mocked HTTP client)."""

    @pytest.mark.asyncio
    async def test_fetch_success_flow(self) -> None:
        """Test successful fetch with all validations passing."""
        fetcher = WebFetcher()

        # Mock HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>Test content</body></html>"

        with patch.object(fetcher.http_client, "get", return_value=mock_response):
            # Mock DNS resolution (public IP)
            with patch("socket.getaddrinfo") as mock_getaddrinfo:
                mock_getaddrinfo.return_value = [
                    (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443))
                ]

                # Mock cache operations
                with patch.object(fetcher, "_cache_get", return_value=None):
                    with patch.object(fetcher, "_cache_set", return_value=None):
                        result = await fetcher.fetch("https://example.com")

                        assert result.get("ok") is True
                        assert result.get("url") == "https://example.com"
                        assert "text" in result
