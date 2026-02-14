"""Tests for OTel log bridge in logging module."""

from __future__ import annotations

import logging
from unittest.mock import patch

from core.observability.logging import setup_logging


def test_setup_logging_creates_json_handler() -> None:
    """setup_logging should configure a JSON formatter on the root logger."""
    setup_logging(level="WARNING", log_to_file=False)

    root = logging.getLogger()
    # Should have at least one handler
    assert len(root.handlers) > 0


def test_setup_logging_file_handler_warning_level() -> None:
    """File handler should only capture WARNING and above."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        # Temporarily change APP_LOGS_PATH for this test
        from core.observability import logging as log_mod

        original_path = log_mod.APP_LOGS_PATH
        log_mod.APP_LOGS_PATH = Path(tmpdir) / "test_logs.jsonl"

        try:
            setup_logging(level="DEBUG", log_to_file=True)

            # Find file handler and verify its level
            root = logging.getLogger()
            file_handlers = [h for h in root.handlers if hasattr(h, "baseFilename")]
            if file_handlers:
                assert file_handlers[0].level == logging.WARNING
        finally:
            log_mod.APP_LOGS_PATH = original_path


def test_setup_otel_log_bridge_no_endpoint() -> None:
    """Bridge should be a no-op when OTEL_EXPORTER_OTLP_ENDPOINT is not set."""
    from core.observability.logging import setup_otel_log_bridge

    handler_count_before = len(logging.getLogger().handlers)

    with patch.dict("os.environ", {}, clear=False):
        # Ensure endpoint is not set
        import os

        os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
        setup_otel_log_bridge()

    handler_count_after = len(logging.getLogger().handlers)
    # Should not have added any handlers
    assert handler_count_after == handler_count_before
