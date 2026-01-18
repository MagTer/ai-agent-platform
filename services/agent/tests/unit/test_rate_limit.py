"""Unit tests for rate limiting middleware."""

import pytest
from fastapi import Request
from slowapi.errors import RateLimitExceeded

from core.middleware.rate_limit import (
    create_rate_limiter,
    get_rate_limit_for_path,
    rate_limit_exceeded_handler,
)


def test_get_rate_limit_for_path() -> None:
    """Test rate limit path matching."""
    assert get_rate_limit_for_path("/admin/contexts") == "10/minute"
    assert get_rate_limit_for_path("/auth/oauth/callback") == "5/minute"
    assert get_rate_limit_for_path("/webui/oauth/authorize") == "5/minute"
    assert get_rate_limit_for_path("/v1/chat/completions") == "30/minute"
    assert get_rate_limit_for_path("/v1/agent") == "60/minute"
    assert get_rate_limit_for_path("/healthz") == "60/minute"


def test_create_rate_limiter() -> None:
    """Test limiter creation."""
    limiter = create_rate_limiter()
    assert limiter is not None
    assert limiter._default_limits is not None


@pytest.mark.asyncio
async def test_rate_limit_exceeded_handler() -> None:
    """Test rate limit exception handler."""
    from unittest.mock import MagicMock

    # Create a minimal mock request with required attributes
    request = MagicMock(spec=Request)
    request.url.path = "/test"
    request.client.host = "127.0.0.1"

    # Create RateLimitExceeded exception mock
    exc = MagicMock(spec=RateLimitExceeded)
    exc.detail = "10 per minute"

    # Call handler
    response = await rate_limit_exceeded_handler(request, exc)

    # Verify response
    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert response.headers["Retry-After"] == "60"


@pytest.mark.asyncio
async def test_rate_limit_handler_wrong_exception_type() -> None:
    """Test rate limit handler with wrong exception type."""
    from unittest.mock import MagicMock

    # Create a minimal mock request with required attributes
    request = MagicMock(spec=Request)
    request.url.path = "/test"
    request.client.host = "127.0.0.1"

    # Pass wrong exception type
    exc = ValueError("not a rate limit error")

    handler_response = await rate_limit_exceeded_handler(request, exc)

    # Should return 500 for unexpected exception type
    assert handler_response.status_code == 500
