"""Logging configuration and event emission."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from pythonjsonlogger import jsonlogger

from core.models.pydantic_schemas import (
    PlanEvent,
    StepEvent,
    SupervisorDecision,
    ToolCallEvent,
    UserFacingEvent,
)

# Common event types for type hinting
LoggableEvent = StepEvent | PlanEvent | SupervisorDecision | ToolCallEvent | UserFacingEvent


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        if not log_record.get("timestamp"):
            # Use ISO8601 format
            log_record["timestamp"] = self.formatTime(record, self.datefmt)
        if log_record.get("level"):
            log_record["level"] = log_record["level"].upper()
        else:
            log_record["level"] = record.levelname


def setup_logging(level: str = "INFO", service_name: str = "agent") -> None:
    """Configure root logger with JSON formatting."""

    log_level = getattr(logging, level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear existing handlers
    for handler in root_logger.handlers:
        root_logger.removeHandler(handler)

    # Check env to decide format (helpful for local dev to keep text)
    if os.environ.get("LOG_FORMAT", "json").lower() == "json":
        log_handler = logging.StreamHandler(sys.stdout)
        formatter = CustomJsonFormatter(  # type: ignore[no-untyped-call]
            "%(timestamp)s %(level)s %(name)s %(message)s", json_ensure_ascii=False
        )
        log_handler.setFormatter(formatter)
        root_logger.addHandler(log_handler)
    else:
        # Rich Text Format
        from rich.logging import RichHandler

        rich_handler = RichHandler(
            rich_tracebacks=True,
            markup=True,
            show_time=True,
            show_path=False,
        )
        # RichHandler handles formatting internally, no need for setFormatter
        root_logger.addHandler(rich_handler)

    # Silence noisy libs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def log_event(event: LoggableEvent) -> None:
    """Log a structured domain event."""
    logger = logging.getLogger("event")

    # Convert pydantic model to dict
    payload = event.model_dump(mode="json", exclude_none=True)

    # Extract trace info if present for top-level promotion
    trace = payload.pop("trace", None)

    extra = {"event_type": event.__class__.__name__, "event_data": payload}

    if trace:
        extra["trace_id"] = trace.get("trace_id")
        extra["span_id"] = trace.get("span_id")

    logger.info(f"Event: {event.__class__.__name__}", extra=extra)
