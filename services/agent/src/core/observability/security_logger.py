"""Security event logging for audit trail and incident detection."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.observability.tracing import add_span_event, current_trace_ids

# Security event types
AUTH_SUCCESS = "AUTH_SUCCESS"
AUTH_FAILURE = "AUTH_FAILURE"
ADMIN_ACCESS = "ADMIN_ACCESS"
ADMIN_ACTION = "ADMIN_ACTION"
OAUTH_INITIATED = "OAUTH_INITIATED"
OAUTH_COMPLETED = "OAUTH_COMPLETED"
OAUTH_FAILED = "OAUTH_FAILED"
CREDENTIAL_CREATED = "CREDENTIAL_CREATED"
CREDENTIAL_DELETED = "CREDENTIAL_DELETED"
RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
SUSPICIOUS_ACTIVITY = "SUSPICIOUS_ACTIVITY"

# Dedicated security logger
SECURITY_LOGGER = logging.getLogger("security")

# System events file for events outside trace context
SYSTEM_EVENTS_PATH = Path("data/system_events.jsonl")

# Background write queue
_event_queue: deque[dict[str, Any]] = deque()
_write_task: asyncio.Task[None] | None = None
_queue_lock = asyncio.Lock()


async def _background_event_writer() -> None:
    """Background task that writes queued events to file."""
    while True:
        try:
            if _event_queue:
                async with _queue_lock:
                    events_to_write = list(_event_queue)
                    _event_queue.clear()

                if events_to_write:
                    # Use asyncio.to_thread for file I/O
                    await asyncio.to_thread(_write_events_sync, events_to_write)

            await asyncio.sleep(0.1)  # Batch writes every 100ms
        except Exception as e:
            SECURITY_LOGGER.warning(f"Background event writer error: {e}")
            await asyncio.sleep(1)  # Back off on error


def _write_events_sync(events: list[dict[str, Any]]) -> None:
    """Synchronous write of multiple events (called in thread)."""
    try:
        SYSTEM_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with SYSTEM_EVENTS_PATH.open("a", encoding="utf-8") as f:
            for event_data in events:
                f.write(json.dumps(event_data) + "\n")
    except Exception as e:
        SECURITY_LOGGER.warning(f"Failed to write system events to file: {e}")


def _ensure_writer_running() -> None:
    """Ensure background writer task is running."""
    global _write_task
    try:
        loop = asyncio.get_running_loop()
        if _write_task is None or _write_task.done():
            _write_task = loop.create_task(_background_event_writer())
    except RuntimeError:
        # No running loop - will be started when loop is available
        pass


def _write_system_event(event_data: dict[str, Any]) -> None:
    """Queue event for background writing (non-blocking)."""
    _event_queue.append(event_data)
    _ensure_writer_running()


def log_security_event(
    event_type: str,
    user_email: str | None = None,
    user_id: str | None = None,
    ip_address: str | None = None,
    endpoint: str | None = None,
    details: dict[str, Any] | None = None,
    severity: str = "INFO",
) -> None:
    """Log a structured security event for audit trail and SIEM integration.

    Events are logged in JSON format for easy parsing by security tools.

    Args:
        event_type: Type of security event (use constants: AUTH_SUCCESS, etc.)
        user_email: Email of the user involved in the event
        user_id: Database ID of the user
        ip_address: IP address of the request
        endpoint: API endpoint or resource accessed
        details: Additional event-specific details
        severity: Log severity (INFO, WARNING, ERROR, CRITICAL)

    Example:
        log_security_event(
            event_type=AUTH_FAILURE,
            user_email="attacker@example.com",
            ip_address="1.2.3.4",
            endpoint="/admin/users/list",
            details={"reason": "User not found"},
            severity="WARNING"
        )
    """
    # Build structured event payload
    event_data: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event_type": event_type,
        "severity": severity.upper(),
    }

    # Add optional fields if provided
    if user_email:
        event_data["user_email"] = user_email
    if user_id:
        event_data["user_id"] = user_id
    if ip_address:
        event_data["ip_address"] = ip_address
    if endpoint:
        event_data["endpoint"] = endpoint
    if details:
        event_data["details"] = details

    # Map severity to logging level
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    log_level = level_map.get(severity.upper(), logging.INFO)

    # Log with extra fields for JSON formatter
    SECURITY_LOGGER.log(
        log_level,
        f"Security event: {event_type}",
        extra={
            "security_event": event_data,
            "event_type": event_type,
            "user_email": user_email,
            "user_id": user_id,
            "ip_address": ip_address,
            "endpoint": endpoint,
        },
    )

    # Check if we're in a trace context
    trace_ids = current_trace_ids()
    if trace_ids:
        # Add event to current span - will be included in spans.jsonl
        span_attributes = {
            "security.event_type": event_type,
            "security.severity": severity.upper(),
        }
        if user_email:
            span_attributes["security.user_email"] = user_email
        if user_id:
            span_attributes["security.user_id"] = user_id
        if ip_address:
            span_attributes["security.ip_address"] = ip_address
        if endpoint:
            span_attributes["security.endpoint"] = endpoint
        if details:
            # Flatten details for span attributes (OpenTelemetry prefers flat keys)
            for key, value in details.items():
                span_attributes[f"security.{key}"] = str(value)

        add_span_event(f"security.{event_type}", attributes=span_attributes)
    else:
        # No trace context - write to system events file
        _write_system_event(event_data)


def get_client_ip(request: Any) -> str | None:
    """Extract client IP address from FastAPI request.

    Checks X-Forwarded-For header first (for proxied requests),
    then falls back to direct client IP.

    Args:
        request: FastAPI Request object

    Returns:
        IP address string or None if unavailable
    """
    # Check X-Forwarded-For header (comma-separated list, leftmost is original)
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()

    # Fall back to direct client
    if hasattr(request, "client") and request.client:
        return request.client.host

    return None


async def flush_security_events() -> None:
    """Flush any pending security events (call on shutdown)."""
    if _event_queue:
        events = list(_event_queue)
        _event_queue.clear()
        await asyncio.to_thread(_write_events_sync, events)


__all__ = [
    "SECURITY_LOGGER",
    "log_security_event",
    "get_client_ip",
    "flush_security_events",
    # Event type constants
    "AUTH_SUCCESS",
    "AUTH_FAILURE",
    "ADMIN_ACCESS",
    "ADMIN_ACTION",
    "OAUTH_INITIATED",
    "OAUTH_COMPLETED",
    "OAUTH_FAILED",
    "CREDENTIAL_CREATED",
    "CREDENTIAL_DELETED",
    "RATE_LIMIT_EXCEEDED",
    "SUSPICIOUS_ACTIVITY",
]
