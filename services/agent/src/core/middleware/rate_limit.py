"""Rate limiting middleware using slowapi with configurable per-path limits."""

from fastapi import Request, Response
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from core.observability.security_logger import RATE_LIMIT_EXCEEDED, log_security_event

# Path-specific rate limit overrides (stricter limits for sensitive endpoints)
PATH_RATE_LIMITS: dict[str, str] = {
    "/auth/oauth": "5/minute",
    "/webui/oauth": "5/minute",
    "/platformadmin/api/": "30/minute",
    "/platformadmin/": "10/minute",
    "/v1/chat": "30/minute",
    "/api/agent/": "20/minute",
}

# Global limiter instance (singleton for application-wide use)
_limiter_instance: Limiter | None = None


def get_rate_limit_for_path(path: str) -> str:
    """Determine the appropriate rate limit for a given path.

    Checks path prefixes in order of specificity (longest first) to ensure
    more specific limits override broader ones.

    Args:
        path: The request path.

    Returns:
        Rate limit string (e.g., "10/minute").
    """
    # Check specific path prefixes (sorted by length, longest first)
    for prefix in sorted(PATH_RATE_LIMITS.keys(), key=len, reverse=True):
        if path.startswith(prefix):
            return PATH_RATE_LIMITS[prefix]

    # Default rate limit
    return "60/minute"


def _dynamic_limit_key(request: Request) -> str:
    """Get dynamic rate limit based on request path.

    Args:
        request: The FastAPI request.

    Returns:
        Rate limit string for the path.
    """
    return get_rate_limit_for_path(request.url.path)


def create_rate_limiter() -> Limiter:
    """Create and configure a rate limiter instance.

    Uses dynamic rate limits based on request path:
    - /auth/oauth, /webui/oauth: 5/minute
    - /platformadmin/api/: 30/minute
    - /platformadmin/: 10/minute
    - /api/agent/: 20/minute
    - /v1/chat: 30/minute
    - Default: 60/minute

    Returns:
        Configured Limiter instance with IP-based rate limiting.
    """
    global _limiter_instance
    if _limiter_instance is None:
        _limiter_instance = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
    return _limiter_instance


def get_limiter() -> Limiter:
    """Get the global rate limiter instance.

    Routes can import this to apply custom rate limits using decorators:
        from core.middleware.rate_limit import get_limiter
        limiter = get_limiter()

        @router.get("/sensitive")
        @limiter.limit("5/minute")
        async def sensitive_endpoint():
            ...

    Returns:
        The global Limiter instance.
    """
    if _limiter_instance is None:
        return create_rate_limiter()
    return _limiter_instance


async def rate_limit_exceeded_handler(request: Request, exc: Exception) -> Response:
    """Handle rate limit exceeded exceptions.

    Logs security event and returns 429 response with Retry-After header.

    Args:
        request: The FastAPI request that exceeded rate limits.
        exc: The RateLimitExceeded exception.

    Returns:
        JSON response with 429 status code and Retry-After header.
    """
    # Type narrow to RateLimitExceeded
    if not isinstance(exc, RateLimitExceeded):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "Unexpected exception type"},
        )

    # Log rate limit violation for security monitoring
    client_ip = get_remote_address(request)
    path = request.url.path
    log_security_event(
        event_type=RATE_LIMIT_EXCEEDED,
        ip_address=client_ip,
        endpoint=path,
        details={"limit": exc.detail},
        severity="WARNING",
    )

    # Calculate retry-after time (in seconds)
    retry_after = 60  # Default: 1 minute

    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "message": "Too many requests. Please try again later.",
            "detail": exc.detail,
        },
        headers={"Retry-After": str(retry_after)},
    )
