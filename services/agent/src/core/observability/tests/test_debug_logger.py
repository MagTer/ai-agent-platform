"""Tests for OTel-based debug logger."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.observability.debug_logger import (
    DebugLogger,
    _sanitize_args,
    configure_debug_log_handler,
    read_debug_logs,
)


@pytest.mark.asyncio
async def test_log_event_writes_to_file_when_enabled() -> None:
    """When enabled, log_event should write JSON line to file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "debug_test.jsonl"
        configure_debug_log_handler(log_path=log_path)

        # Mock session with enabled config
        session = AsyncMock(spec=AsyncSession)
        mock_execute = AsyncMock()
        mock_result = MagicMock()
        mock_config = MagicMock()
        mock_config.value = "true"
        mock_result.scalar_one_or_none.return_value = mock_config
        mock_execute.return_value = mock_result
        session.execute = mock_execute

        logger = DebugLogger(session)

        # Log an event
        await logger.log_event(
            trace_id="test-trace-123",
            event_type="test_event",
            event_data={"key": "value"},
            conversation_id="conv-456",
        )

        # Verify file was written
        assert log_path.exists()
        content = log_path.read_text()
        assert "test-trace-123" in content
        assert "test_event" in content

        # Verify JSON is valid
        entry = json.loads(content.strip())
        assert entry["trace_id"] == "test-trace-123"
        assert entry["event_type"] == "test_event"
        assert entry["conversation_id"] == "conv-456"
        assert entry["event_data"] == {"key": "value"}


@pytest.mark.asyncio
async def test_log_event_is_noop_when_disabled() -> None:
    """When disabled, log_event should not write anything."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "debug_test.jsonl"
        configure_debug_log_handler(log_path=log_path)

        # Mock session with disabled config
        session = AsyncMock(spec=AsyncSession)
        mock_execute = AsyncMock()
        mock_result = MagicMock()
        mock_config = MagicMock()
        mock_config.value = "false"
        mock_result.scalar_one_or_none.return_value = mock_config
        mock_execute.return_value = mock_result
        session.execute = mock_execute

        logger = DebugLogger(session)

        # Log an event
        await logger.log_event(
            trace_id="test-trace-123",
            event_type="test_event",
            event_data={"key": "value"},
        )

        # Verify file was NOT written (or is empty)
        if log_path.exists():
            content = log_path.read_text().strip()
            assert content == ""


@pytest.mark.asyncio
async def test_is_enabled_caches_result() -> None:
    """is_enabled should cache the result for TTL period."""
    session = AsyncMock(spec=AsyncSession)
    mock_execute = AsyncMock()
    mock_result = MagicMock()
    mock_config = MagicMock()
    mock_config.value = "true"
    mock_result.scalar_one_or_none.return_value = mock_config
    mock_execute.return_value = mock_result
    session.execute = mock_execute

    logger = DebugLogger(session)

    # First call should query DB
    enabled1 = await logger.is_enabled()
    assert enabled1 is True
    assert mock_execute.call_count == 1

    # Second call should use cache (no additional DB query)
    enabled2 = await logger.is_enabled()
    assert enabled2 is True
    assert mock_execute.call_count == 1  # Still 1


def test_sanitize_args_redacts_sensitive_keys() -> None:
    """_sanitize_args should redact password, token, secret, key, credential."""
    args = {
        "username": "alice",
        "password": "secret123",
        "api_key": "sk-1234",
        "bearer_token": "abc",
        "oauth_secret": "xyz",
        "config": {
            "db_password": "hidden",
            "normal_field": "visible",
        },
    }

    sanitized = _sanitize_args(args)

    # Sensitive keys should be redacted
    assert sanitized["password"] == "***REDACTED***"
    assert sanitized["api_key"] == "***REDACTED***"
    assert sanitized["bearer_token"] == "***REDACTED***"
    assert sanitized["oauth_secret"] == "***REDACTED***"

    # Nested sensitive keys should be redacted
    assert sanitized["config"]["db_password"] == "***REDACTED***"

    # Non-sensitive keys should be preserved
    assert sanitized["username"] == "alice"
    assert sanitized["config"]["normal_field"] == "visible"


def test_sanitize_args_handles_non_dict() -> None:
    """_sanitize_args should handle non-dict inputs gracefully."""
    assert _sanitize_args("not a dict") == {}
    assert _sanitize_args(123) == {}
    assert _sanitize_args(None) == {}
    assert _sanitize_args([1, 2, 3]) == {}


@pytest.mark.asyncio
async def test_read_debug_logs_filters_by_trace_id() -> None:
    """read_debug_logs should filter by trace_id."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "debug_test.jsonl"

        # Write test data
        with log_path.open("w") as f:
            f.write(
                json.dumps(
                    {
                        "trace_id": "trace-1",
                        "event_type": "request",
                        "timestamp": "2024-01-01T00:00:00",
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "trace_id": "trace-2",
                        "event_type": "plan",
                        "timestamp": "2024-01-01T00:01:00",
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "trace_id": "trace-1",
                        "event_type": "tool_call",
                        "timestamp": "2024-01-01T00:02:00",
                    }
                )
                + "\n"
            )

        # Patch DEBUG_LOGS_PATH to point to our temp file
        with patch("core.observability.debug_logger.DEBUG_LOGS_PATH", log_path):
            # Filter by trace-1
            logs = await read_debug_logs(trace_id="trace-1", limit=10)

            # Should only return trace-1 entries (newest first)
            assert len(logs) == 2
            assert logs[0]["event_type"] == "tool_call"
            assert logs[1]["event_type"] == "request"
            assert all(log["trace_id"] == "trace-1" for log in logs)


@pytest.mark.asyncio
async def test_read_debug_logs_filters_by_event_type() -> None:
    """read_debug_logs should filter by event_type."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "debug_test.jsonl"

        # Write test data
        with log_path.open("w") as f:
            f.write(
                json.dumps(
                    {
                        "trace_id": "trace-1",
                        "event_type": "request",
                        "timestamp": "2024-01-01T00:00:00",
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "trace_id": "trace-1",
                        "event_type": "plan",
                        "timestamp": "2024-01-01T00:01:00",
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "trace_id": "trace-1",
                        "event_type": "tool_call",
                        "timestamp": "2024-01-01T00:02:00",
                    }
                )
                + "\n"
            )

        # Patch DEBUG_LOGS_PATH
        with patch("core.observability.debug_logger.DEBUG_LOGS_PATH", log_path):
            # Filter by event_type=plan
            logs = await read_debug_logs(event_type="plan", limit=10)

            # Should only return plan entries
            assert len(logs) == 1
            assert logs[0]["event_type"] == "plan"


@pytest.mark.asyncio
async def test_read_debug_logs_respects_limit() -> None:
    """read_debug_logs should respect the limit parameter."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "debug_test.jsonl"

        # Write 10 entries
        with log_path.open("w") as f:
            for i in range(10):
                f.write(
                    json.dumps(
                        {
                            "trace_id": f"trace-{i}",
                            "event_type": "request",
                            "timestamp": f"2024-01-01T00:{i:02d}:00",
                        }
                    )
                    + "\n"
                )

        # Patch DEBUG_LOGS_PATH
        with patch("core.observability.debug_logger.DEBUG_LOGS_PATH", log_path):
            # Request only 3 entries
            logs = await read_debug_logs(limit=3)

            # Should return exactly 3 (newest first)
            assert len(logs) == 3
            assert logs[0]["trace_id"] == "trace-9"
            assert logs[1]["trace_id"] == "trace-8"
            assert logs[2]["trace_id"] == "trace-7"


@pytest.mark.asyncio
async def test_read_debug_logs_returns_empty_if_file_missing() -> None:
    """read_debug_logs should return empty list if file doesn't exist."""
    with patch("core.observability.debug_logger.DEBUG_LOGS_PATH", Path("/nonexistent/file.jsonl")):
        logs = await read_debug_logs(limit=10)
        assert logs == []


@pytest.mark.asyncio
async def test_log_tool_call_sanitizes_args() -> None:
    """log_tool_call should sanitize tool arguments."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "debug_test.jsonl"
        configure_debug_log_handler(log_path=log_path)

        # Mock enabled session
        session = AsyncMock(spec=AsyncSession)
        mock_execute = AsyncMock()
        mock_result = MagicMock()
        mock_config = MagicMock()
        mock_config.value = "true"
        mock_result.scalar_one_or_none.return_value = mock_config
        mock_execute.return_value = mock_result
        session.execute = mock_execute

        logger = DebugLogger(session)

        # Log tool call with sensitive args
        await logger.log_tool_call(
            trace_id="test-trace",
            conversation_id="conv-1",
            tool_name="api_call",
            args={"url": "https://api.example.com", "api_key": "secret-key-123"},
        )

        # Read the log file
        content = log_path.read_text()
        entry = json.loads(content.strip())

        # Verify api_key is redacted
        assert entry["event_data"]["args"]["api_key"] == "***REDACTED***"
        assert entry["event_data"]["args"]["url"] == "https://api.example.com"
