"""Security event logging for audit trail and incident detection."""

from __future__ import annotations

import json
import logging
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


def _write_system_event(event_data: dict[str, Any]) -> None:
    """Write event to system_events.jsonl for events without trace context."""
    try:
        SYSTEM_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with SYSTEM_EVENTS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event_data) + "\n")
    except Exception as e:
        SECURITY_LOGGER.warning(f"Failed to write system event to file: {e}")


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


__all__ = [
    "SECURITY_LOGGER",
    "log_security_event",
    "get_client_ip",
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
