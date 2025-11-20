"""Structured logging helpers used across agents."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel

from agent.observability.tracing import current_trace_ids


def _json_formatter(record: logging.LogRecord) -> str:
    payload: dict[str, Any] = {
        "level": record.levelname,
        "message": record.getMessage(),
        "logger": record.name,
    }
    payload.update(current_trace_ids())
    if record.args and isinstance(record.args, dict):
        payload.update(record.args)
    return json.dumps(payload)


class JsonFormatter(logging.Formatter):
    """JSON formatter that injects trace context when available."""

    def format(self, record: logging.LogRecord) -> str:  # pragma: no cover - trivial
        return _json_formatter(record)


def get_logger(name: str = "agent.observability") -> logging.Logger:
    """Create a logger configured for structured JSON output."""

    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def log_event(event: BaseModel, *, logger: logging.Logger | None = None, level: int = logging.INFO) -> None:
    """Emit a structured event derived from a Pydantic model."""

    log = logger or get_logger()
    payload = event.model_dump(mode="json")
    log.log(level, json.dumps(payload))


__all__ = ["get_logger", "log_event", "JsonFormatter"]
