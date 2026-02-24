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


def _make_enabled_session(enabled: bool = True) -> AsyncMock:
    """Create a mock AsyncSession that returns the given debug_enabled value."""
    session = AsyncMock(spec=AsyncSession)
    mock_execute = AsyncMock()
    mock_result = MagicMock()
    mock_config = MagicMock()
    mock_config.value = "true" if enabled else "false"
    mock_result.scalar_one_or_none.return_value = mock_config
    mock_execute.return_value = mock_result
    session.execute = mock_execute
    return session


@pytest.mark.asyncio
async def test_log_event_adds_span_event_when_enabled() -> None:
    """When enabled, log_event should add an OTel span event."""
    session = _make_enabled_session(enabled=True)
    logger = DebugLogger(session)

    # Mock the current span
    mock_span = MagicMock()
    mock_span.is_recording.return_value = True

    with patch("core.observability.debug_logger.trace") as mock_trace:
        mock_trace.get_current_span.return_value = mock_span

        await logger.log_event(
            trace_id="test-trace-123",
            event_type="test_event",
            event_data={"key": "value"},
            conversation_id="conv-456",
        )

        # Verify span event was added
        mock_span.add_event.assert_called_once()
        call_args = mock_span.add_event.call_args
        assert call_args[0][0] == "debug.test_event"
        attrs = call_args[1]["attributes"]
        assert attrs["debug.trace_id"] == "test-trace-123"
        assert attrs["debug.event_type"] == "test_event"
        assert attrs["debug.conversation_id"] == "conv-456"
        assert '"key": "value"' in attrs["debug.event_data"]


@pytest.mark.asyncio
async def test_log_event_is_noop_when_disabled() -> None:
    """When disabled, log_event should not call add_event."""
    session = _make_enabled_session(enabled=False)
    logger = DebugLogger(session)

    mock_span = MagicMock()
    mock_span.is_recording.return_value = True

    with patch("core.observability.debug_logger.trace") as mock_trace:
        mock_trace.get_current_span.return_value = mock_span

        await logger.log_event(
            trace_id="test-trace-123",
            event_type="test_event",
            event_data={"key": "value"},
        )

        # Verify no span event was added
        mock_span.add_event.assert_not_called()


@pytest.mark.asyncio
async def test_log_event_skips_add_event_when_span_not_recording() -> None:
    """When the span is not recording, no event should be added."""
    session = _make_enabled_session(enabled=True)
    logger = DebugLogger(session)

    mock_span = MagicMock()
    mock_span.is_recording.return_value = False

    with patch("core.observability.debug_logger.trace") as mock_trace:
        mock_trace.get_current_span.return_value = mock_span

        await logger.log_event(
            trace_id="test-trace-123",
            event_type="test_event",
            event_data={"key": "value"},
        )

        mock_span.add_event.assert_not_called()


@pytest.mark.asyncio
async def test_is_enabled_caches_result() -> None:
    """is_enabled should cache the result for TTL period."""
    session = _make_enabled_session(enabled=True)
    logger = DebugLogger(session)

    # First call should query DB
    enabled1 = await logger.is_enabled()
    assert enabled1 is True
    assert session.execute.call_count == 1

    # Second call should use cache (no additional DB query)
    enabled2 = await logger.is_enabled()
    assert enabled2 is True
    assert session.execute.call_count == 1  # Still 1


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


def test_configure_debug_log_handler_is_noop() -> None:
    """configure_debug_log_handler should be a no-op (backward compat)."""
    # Should not raise; no file should be created
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "debug_test.jsonl"
        configure_debug_log_handler(log_path=log_path)
        # No file should be created since the function is now a no-op
        assert not log_path.exists()


@pytest.mark.asyncio
async def test_read_debug_logs_filters_by_trace_id() -> None:
    """read_debug_logs should filter by trace_id from span events."""
    with tempfile.TemporaryDirectory() as tmpdir:
        spans_path = Path(tmpdir) / "spans.jsonl"

        # Write span records with embedded debug events
        span1 = {
            "name": "agent.request",
            "context": {"trace_id": "trace-1", "span_id": "span-1"},
            "start_time": "2024-01-01T00:00:00",
            "duration_ms": 100,
            "status": "OK",
            "attributes": {},
            "events": [
                {
                    "name": "debug.request",
                    "timestamp": "2024-01-01T00:00:00",
                    "attributes": {
                        "debug.trace_id": "trace-1",
                        "debug.event_type": "request",
                        "debug.event_data": '{"prompt": "hello"}',
                    },
                },
                {
                    "name": "debug.tool_call",
                    "timestamp": "2024-01-01T00:00:01",
                    "attributes": {
                        "debug.trace_id": "trace-1",
                        "debug.event_type": "tool_call",
                        "debug.event_data": '{"tool_name": "search"}',
                    },
                },
            ],
        }
        span2 = {
            "name": "agent.request",
            "context": {"trace_id": "trace-2", "span_id": "span-2"},
            "start_time": "2024-01-01T00:01:00",
            "duration_ms": 200,
            "status": "OK",
            "attributes": {},
            "events": [
                {
                    "name": "debug.plan",
                    "timestamp": "2024-01-01T00:01:00",
                    "attributes": {
                        "debug.trace_id": "trace-2",
                        "debug.event_type": "plan",
                        "debug.event_data": '{"plan": "step1"}',
                    },
                },
            ],
        }

        with spans_path.open("w") as f:
            f.write(json.dumps(span1) + "\n")
            f.write(json.dumps(span2) + "\n")

        with patch(
            "core.observability.debug_logger._get_spans_path",
            return_value=spans_path,
        ):
            logs = await read_debug_logs(trace_id="trace-1", limit=10)

            assert len(logs) == 2
            assert all(log["trace_id"] == "trace-1" for log in logs)


@pytest.mark.asyncio
async def test_read_debug_logs_filters_by_event_type() -> None:
    """read_debug_logs should filter by event_type from span events."""
    with tempfile.TemporaryDirectory() as tmpdir:
        spans_path = Path(tmpdir) / "spans.jsonl"

        span1 = {
            "name": "agent.request",
            "context": {"trace_id": "trace-1", "span_id": "span-1"},
            "start_time": "2024-01-01T00:00:00",
            "duration_ms": 100,
            "status": "OK",
            "attributes": {},
            "events": [
                {
                    "name": "debug.request",
                    "timestamp": "2024-01-01T00:00:00",
                    "attributes": {
                        "debug.trace_id": "trace-1",
                        "debug.event_type": "request",
                        "debug.event_data": '{"prompt": "hello"}',
                    },
                },
                {
                    "name": "debug.plan",
                    "timestamp": "2024-01-01T00:00:01",
                    "attributes": {
                        "debug.trace_id": "trace-1",
                        "debug.event_type": "plan",
                        "debug.event_data": '{"plan": "do stuff"}',
                    },
                },
                {
                    "name": "debug.tool_call",
                    "timestamp": "2024-01-01T00:00:02",
                    "attributes": {
                        "debug.trace_id": "trace-1",
                        "debug.event_type": "tool_call",
                        "debug.event_data": '{"tool_name": "search"}',
                    },
                },
            ],
        }

        with spans_path.open("w") as f:
            f.write(json.dumps(span1) + "\n")

        with patch(
            "core.observability.debug_logger._get_spans_path",
            return_value=spans_path,
        ):
            logs = await read_debug_logs(event_type="plan", limit=10)

            assert len(logs) == 1
            assert logs[0]["event_type"] == "plan"


@pytest.mark.asyncio
async def test_read_debug_logs_respects_limit() -> None:
    """read_debug_logs should respect the limit parameter."""
    with tempfile.TemporaryDirectory() as tmpdir:
        spans_path = Path(tmpdir) / "spans.jsonl"

        # Write 10 span records each with one debug event
        with spans_path.open("w") as f:
            for i in range(10):
                span = {
                    "name": "agent.request",
                    "context": {"trace_id": f"trace-{i}", "span_id": f"span-{i}"},
                    "start_time": f"2024-01-01T00:{i:02d}:00",
                    "duration_ms": 100,
                    "status": "OK",
                    "attributes": {},
                    "events": [
                        {
                            "name": "debug.request",
                            "timestamp": f"2024-01-01T00:{i:02d}:00",
                            "attributes": {
                                "debug.trace_id": f"trace-{i}",
                                "debug.event_type": "request",
                                "debug.event_data": '{"prompt": "hello"}',
                            },
                        },
                    ],
                }
                f.write(json.dumps(span) + "\n")

        with patch(
            "core.observability.debug_logger._get_spans_path",
            return_value=spans_path,
        ):
            logs = await read_debug_logs(limit=3)

            # Should return exactly 3 (newest spans first)
            assert len(logs) == 3


@pytest.mark.asyncio
async def test_read_debug_logs_returns_empty_if_file_missing() -> None:
    """read_debug_logs should return empty list if file doesn't exist."""
    with patch(
        "core.observability.debug_logger._get_spans_path",
        return_value=Path("/nonexistent/spans.jsonl"),
    ):
        logs = await read_debug_logs(limit=10)
        assert logs == []


@pytest.mark.asyncio
async def test_log_tool_call_sanitizes_args() -> None:
    """log_tool_call should sanitize tool arguments in span event attributes."""
    session = _make_enabled_session(enabled=True)
    logger = DebugLogger(session)

    mock_span = MagicMock()
    mock_span.is_recording.return_value = True

    with patch("core.observability.debug_logger.trace") as mock_trace:
        mock_trace.get_current_span.return_value = mock_span

        await logger.log_tool_call(
            trace_id="test-trace",
            conversation_id="conv-1",
            tool_name="api_call",
            args={"url": "https://api.example.com", "api_key": "secret-key-123"},
        )

        # Verify span event was added with sanitized args
        mock_span.add_event.assert_called_once()
        call_args = mock_span.add_event.call_args
        event_data_str = call_args[1]["attributes"]["debug.event_data"]
        event_data = json.loads(event_data_str)

        assert event_data["args"]["api_key"] == "***REDACTED***"
        assert event_data["args"]["url"] == "https://api.example.com"
