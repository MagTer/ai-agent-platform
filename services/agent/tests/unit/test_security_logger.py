"""Unit tests for security event logging."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from core.observability.security_logger import (
    AUTH_FAILURE,
    AUTH_SUCCESS,
    CREDENTIAL_CREATED,
    get_client_ip,
    log_security_event,
)


def test_log_security_event_all_fields(caplog: pytest.LogCaptureFixture) -> None:
    """Test logging a security event with all fields."""
    caplog.set_level(logging.INFO, logger="security")

    log_security_event(
        event_type=AUTH_SUCCESS,
        user_email="test@example.com",
        user_id="user-123",
        ip_address="192.168.1.1",
        endpoint="/admin/users",
        details={"role": "admin"},
        severity="INFO",
    )

    # Verify log was created
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelname == "INFO"
    assert "AUTH_SUCCESS" in record.message
    # Check extra fields (added dynamically via log_security_event)
    assert getattr(record, "user_email", None) == "test@example.com"
    assert getattr(record, "user_id", None) == "user-123"
    assert getattr(record, "ip_address", None) == "192.168.1.1"
    assert getattr(record, "endpoint", None) == "/admin/users"


def test_log_security_event_minimal_fields(caplog: pytest.LogCaptureFixture) -> None:
    """Test logging a security event with only required fields."""
    caplog.set_level(logging.WARNING, logger="security")

    log_security_event(
        event_type=AUTH_FAILURE,
        severity="WARNING",
    )

    # Verify log was created
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelname == "WARNING"
    assert "AUTH_FAILURE" in record.message


def test_log_security_event_credential_created(caplog: pytest.LogCaptureFixture) -> None:
    """Test logging credential creation event."""
    caplog.set_level(logging.INFO, logger="security")

    log_security_event(
        event_type=CREDENTIAL_CREATED,
        user_email="admin@example.com",
        user_id="admin-456",
        ip_address="10.0.0.1",
        endpoint="/admin/credentials/create",
        details={
            "credential_type": "azure_devops_pat",
            "target_user_email": "user@example.com",
            "target_user_id": "user-789",
        },
        severity="INFO",
    )

    # Verify log was created
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelname == "INFO"
    assert "CREDENTIAL_CREATED" in record.message
    # Check extra fields (added dynamically via log_security_event)
    assert getattr(record, "user_email", None) == "admin@example.com"
    assert getattr(record, "endpoint", None) == "/admin/credentials/create"


def test_get_client_ip_from_forwarded_header() -> None:
    """Test extracting IP from X-Forwarded-For header."""
    # Mock request with X-Forwarded-For header
    request = MagicMock()
    request.headers.get.return_value = "203.0.113.1, 198.51.100.1"

    ip = get_client_ip(request)
    assert ip == "203.0.113.1"


def test_get_client_ip_from_direct_client() -> None:
    """Test extracting IP from direct client."""
    # Mock request without X-Forwarded-For
    request = MagicMock()
    request.headers.get.return_value = None
    request.client.host = "192.0.2.1"

    ip = get_client_ip(request)
    assert ip == "192.0.2.1"


def test_get_client_ip_no_client() -> None:
    """Test IP extraction when no client info is available."""
    # Mock request without headers or client
    request = MagicMock()
    request.headers.get.return_value = None
    del request.client  # Remove client attribute

    ip = get_client_ip(request)
    assert ip is None


def test_log_severity_levels(caplog: pytest.LogCaptureFixture) -> None:
    """Test different severity levels map to correct log levels."""
    caplog.set_level(logging.DEBUG, logger="security")

    severities = [
        ("DEBUG", logging.DEBUG),
        ("INFO", logging.INFO),
        ("WARNING", logging.WARNING),
        ("ERROR", logging.ERROR),
        ("CRITICAL", logging.CRITICAL),
    ]

    for severity, expected_level in severities:
        caplog.clear()
        log_security_event(
            event_type=AUTH_FAILURE,
            severity=severity,
        )

        assert len(caplog.records) == 1
        assert caplog.records[0].levelno == expected_level
