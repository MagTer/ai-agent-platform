"""HTTP middleware and exception handler registration."""

from __future__ import annotations

import logging
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from opentelemetry import trace

from interfaces.http.admin_auth import AuthRedirectError

LOGGER = logging.getLogger(__name__)


def register_middlewares(app: FastAPI, settings: Any) -> None:
    """Register all HTTP middleware and exception handlers on the FastAPI app.

    Args:
        app: FastAPI application instance.
        settings: Application settings (used for environment, admin_jwt_secret).
    """

    @app.exception_handler(AuthRedirectError)
    async def auth_redirect_handler(request: Request, exc: AuthRedirectError) -> RedirectResponse:
        """Redirect unauthenticated users to login page."""
        return RedirectResponse(url=exc.redirect_url, status_code=302)

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Capture unhandled exceptions and log escape for debugging."""
        import asyncio

        timestamp = datetime.now().isoformat()
        trace_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        error_msg = f"[{timestamp}] CRITICAL: Unhandled exception\n{trace_str}\n" + "-" * 80 + "\n"

        # Log to stderr
        LOGGER.exception("Unhandled exception")

        # Record exception to OpenTelemetry span for trace visibility
        span = trace.get_current_span()
        if span.is_recording():
            span.record_exception(exc)
            span.set_attribute("error.type", type(exc).__name__)
            span.set_attribute("error.message", str(exc)[:1000])  # Truncate long messages

        # Write to crash log asynchronously
        try:
            log_path = Path("data/crash.log")

            def _write_crash_log() -> None:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(error_msg)

            await asyncio.to_thread(_write_crash_log)
        except Exception as log_exc:
            LOGGER.error(f"Failed to write to crash log: {log_exc}")

        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error"},
        )

    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next: Any) -> Any:
        """Add security headers to all responses."""
        response = await call_next(request)

        # Standard security headers for all responses
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'self'"
        )

        # X-Frame-Options: SAMEORIGIN for admin portal, DENY for everything else
        if request.url.path.startswith("/platformadmin/"):
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        else:
            response.headers["X-Frame-Options"] = "DENY"

        # HSTS only in production
        if settings.environment == "production":
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"

        return response

    @app.middleware("http")
    async def csrf_middleware(request: Request, call_next: Any) -> Any:
        """CSRF protection middleware for admin portal endpoints."""
        # Only apply to /platformadmin/ endpoints
        if not request.url.path.startswith("/platformadmin/"):
            return await call_next(request)

        # Skip for GET, HEAD, OPTIONS (safe methods)
        if request.method in ("GET", "HEAD", "OPTIONS"):
            # Set CSRF cookie on GET requests
            response = await call_next(request)

            # Only set cookie if admin_jwt_secret is configured
            if settings.admin_jwt_secret and settings.environment != "test":
                from interfaces.http.csrf import (
                    CSRF_COOKIE_NAME,
                    generate_csrf_token,
                    set_csrf_cookie,
                )

                # Check if cookie already exists
                existing_cookie = request.cookies.get(CSRF_COOKIE_NAME)
                if not existing_cookie:
                    # Generate new token and set cookie
                    token = generate_csrf_token(settings.admin_jwt_secret)
                    set_csrf_cookie(response, token, secure=(settings.environment == "production"))
                    LOGGER.debug("CSRF cookie set for new session")

            return response

        # POST, DELETE, PUT, PATCH require CSRF validation
        # (The validation is handled by the require_csrf dependency in endpoints)
        return await call_next(request)

    @app.middleware("http")
    async def capture_request_response_middleware(request: Request, call_next: Any) -> Any:
        span = trace.get_current_span()
        if not span.is_recording():
            return await call_next(request)

        # Set a meaningful name for the trace in the UI
        path = request.url.path
        method = request.method
        if path.startswith("/v1/chat/completions"):
            span.update_name(f"Agent Chat: {method} {path}")
        elif path.startswith("/v1/agent"):
            span.update_name(f"Agent Task: {method} {path}")
        else:
            span.update_name(f"API: {method} {path}")

        skip_body = path.startswith(
            (
                "/diagnostics",
                "/health",
                "/metrics",
                "/v1/agent",
                "/v1/chat/completions",
            )
        )

        if not skip_body:
            # Capture request body
            try:
                body = await request.body()
                if body:
                    text = body.decode("utf-8", errors="replace")
                    span.set_attribute("http.request.body", text[:2000])

                # Re-seed the body for downstream consumers
                async def receive() -> dict[str, Any]:
                    return {"type": "http.request", "body": body, "more_body": False}

                request._receive = receive
            except Exception:
                LOGGER.warning("Failed to capture request body", exc_info=True)

        response = await call_next(request)

        if skip_body:
            return response

        # Capture response body (only first 2000 bytes to avoid memory bloat)
        try:
            if hasattr(response, "body_iterator"):
                original_iterator = response.body_iterator
                preview_chunks: list[bytes] = []
                preview_bytes_collected = 0
                preview_limit = 2000

                async def response_stream_wrapper() -> Any:
                    nonlocal preview_bytes_collected
                    async for chunk in original_iterator:
                        if isinstance(chunk, bytes) and preview_bytes_collected < preview_limit:
                            remaining = preview_limit - preview_bytes_collected
                            preview_chunks.append(chunk[:remaining])
                            preview_bytes_collected += min(len(chunk), remaining)
                        yield chunk

                    # After stream is consumed - only preview bytes in memory
                    if preview_chunks:
                        preview = b"".join(preview_chunks).decode("utf-8", errors="replace")
                        span.set_attribute("http.response.body", preview[:2000])

                response.body_iterator = response_stream_wrapper()
            elif hasattr(response, "body"):
                text_body = response.body.decode("utf-8", errors="replace")
                span.set_attribute(
                    "http.response.body",
                    text_body[:2000],
                )
        except Exception:
            LOGGER.warning("Failed to capture response body", exc_info=True)

        return response

    @app.middleware("http")
    async def request_metrics_middleware(request: Request, call_next: Any) -> Any:
        """Track request timing and log slow requests."""
        start_time = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000

        # Add timing to response headers
        response.headers["X-Response-Time"] = f"{duration_ms:.1f}ms"

        # Record in OpenTelemetry span
        span = trace.get_current_span()
        if span.is_recording():
            span.set_attribute("http.request.duration_ms", round(duration_ms, 1))
            span.set_attribute("http.route", request.url.path)

        # Record OTel metrics for agent API endpoints
        if request.url.path.startswith(("/v1/agent", "/v1/chat/completions", "/chat/completions")):
            from core.observability.metrics import request_counter, request_duration_histogram

            status_str = "error" if response.status_code >= 400 else "ok"
            attrs = {"http.route": request.url.path, "status": status_str}
            request_counter.add(1, attributes=attrs)
            request_duration_histogram.record(duration_ms, attributes=attrs)

        # Log slow requests (> 5 seconds)
        if duration_ms > 5000:
            LOGGER.warning(
                "Slow request: %s %s took %.1fms",
                request.method,
                request.url.path,
                duration_ms,
            )

        return response


__all__ = ["register_middlewares"]
