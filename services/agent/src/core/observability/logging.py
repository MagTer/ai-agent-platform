"""Logging configuration and event emission."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from pythonjsonlogger import jsonlogger

from core.models.pydantic_schemas import (
    PlanEvent,
    StepEvent,
    SupervisorDecision,
    ToolCallEvent,
    UserFacingEvent,
)

# Application logs file path
APP_LOGS_PATH = Path("data/app_logs.jsonl")

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


def setup_logging(
    level: str = "INFO",
    service_name: str = "agent",
    log_to_file: bool = True,
) -> None:
    """Configure root logger with JSON formatting and optional file output.

    Args:
        level: Minimum log level (default INFO).
        service_name: Service name for log context.
        log_to_file: If True, also write logs to app_logs.jsonl (default True).
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear existing handlers
    for handler in root_logger.handlers:
        root_logger.removeHandler(handler)

    # JSON formatter for both stdout and file
    json_formatter = CustomJsonFormatter(  # type: ignore[no-untyped-call]
        "%(timestamp)s %(level)s %(name)s %(message)s", json_ensure_ascii=False
    )

    # Check env to decide format (helpful for local dev to keep text)
    if os.environ.get("LOG_FORMAT", "json").lower() == "json":
        log_handler = logging.StreamHandler(sys.stdout)
        log_handler.setFormatter(json_formatter)
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

    # File handler for application logs (WARNING and above)
    # This makes logs accessible via /diagnostics/logs API
    if log_to_file:
        try:
            APP_LOGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                APP_LOGS_PATH,
                maxBytes=10 * 1024 * 1024,  # 10 MB
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setLevel(logging.WARNING)  # Only WARNING and above to file
            file_handler.setFormatter(json_formatter)
            root_logger.addHandler(file_handler)
        except Exception as e:
            # Don't fail if we can't write to file
            root_logger.warning(f"Could not set up file logging: {e}")

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


def setup_otel_log_bridge(service_name: str = "agent") -> None:
    """Bridge Python logging to OpenTelemetry OTLP log export.

    When OTEL_EXPORTER_OTLP_ENDPOINT is set, adds an OTel LoggingHandler
    to the root logger. This makes Python log records available in the
    OTLP collector alongside traces and metrics, with trace correlation.

    Must be called AFTER setup_logging() and configure_tracing().

    Args:
        service_name: Service name for the log resource.
    """
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not otlp_endpoint:
        return

    try:
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
            OTLPLogExporter,
        )
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource

        resource = Resource.create({SERVICE_NAME: service_name})
        logger_provider = LoggerProvider(resource=resource)

        log_exporter = OTLPLogExporter(endpoint=otlp_endpoint, insecure=True)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))

        # Add OTel handler to root logger (WARNING+ to avoid noise)
        otel_handler = LoggingHandler(
            level=logging.WARNING,
            logger_provider=logger_provider,
        )
        logging.getLogger().addHandler(otel_handler)

        logging.getLogger(__name__).info("OTel log bridge configured to %s", otlp_endpoint)
    except ImportError:
        logging.getLogger(__name__).info(
            "OTel log export packages not available; skipping log bridge"
        )
    except Exception:
        logging.getLogger(__name__).warning("Failed to configure OTel log bridge", exc_info=True)
