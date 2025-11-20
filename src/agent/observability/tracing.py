"""OpenTelemetry tracing utilities for the agent platform.

The module gracefully degrades to a no-op implementation when the
``opentelemetry`` package is unavailable so that unit tests can run in
restricted environments without installing optional dependencies.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

try:  # pragma: no cover - exercised implicitly during imports
    from opentelemetry import trace as _otel_trace
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (  # type: ignore
        BatchSpanProcessor,
        ConsoleSpanExporter,
        SimpleSpanProcessor,
        SpanExporter,
    )
    from opentelemetry.trace import SpanKind as _OtelSpanKind

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover - fallback branch for offline CI
    _OTEL_AVAILABLE = False
    _otel_trace = None
    BatchSpanProcessor = ConsoleSpanExporter = SimpleSpanProcessor = SpanExporter = None  # type: ignore
    Resource = TracerProvider = SERVICE_NAME = None  # type: ignore

    class _OtelSpanKind(str, Enum):
        INTERNAL = "INTERNAL"


logger = logging.getLogger(__name__)


class _NoOpSpanContext:
    """Minimal span context used when OpenTelemetry is absent."""

    trace_id: int = 0
    span_id: int = 0
    is_valid: bool = False


class _NoOpSpan:
    """Span placeholder implementing the methods we use."""

    name: str = "noop"
    kind: Any = _OtelSpanKind.INTERNAL
    attributes: dict[str, Any] = {}
    start_time: int | None = None
    end_time: int | None = None

    def __enter__(self) -> _NoOpSpan:  # pragma: no cover - trivial
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # pragma: no cover - trivial
        return None

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_attributes(self, attrs: dict[str, Any]) -> None:
        self.attributes.update(attrs)

    def get_span_context(self) -> _NoOpSpanContext:
        return _NoOpSpanContext()


class _NoOpTracer:
    def start_as_current_span(self, name: str, *, kind: Any | None = None) -> Any:
        return _NoOpSpan()


class _NoOpTraceAPI:
    """Lightweight shim replicating the subset of the trace API we consume."""

    def __init__(self) -> None:
        self._tracer = _NoOpTracer()

    def get_tracer(self, name: str | None = None) -> _NoOpTracer:
        return self._tracer

    def set_tracer_provider(self, provider: Any) -> None:  # pragma: no cover - no-op
        return None

    def get_current_span(self) -> _NoOpSpan:
        return _NoOpSpan()


class _FileSpanExporter(SpanExporter if _OTEL_AVAILABLE else object):
    """Simple JSONL file exporter for spans."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, spans: list[Any]) -> Any:  # pragma: no cover - thin wrapper
        records = []
        for span in spans:
            ctx = span.get_span_context()
            record = {
                "name": getattr(span, "name", "unknown"),
                "context": {
                    "trace_id": format(getattr(ctx, "trace_id", 0), "032x"),
                    "span_id": format(getattr(ctx, "span_id", 0), "016x"),
                },
                "kind": getattr(getattr(span, "kind", None), "name", "INTERNAL"),
                "attributes": getattr(span, "attributes", {}),
                "start_time": getattr(span, "start_time", None),
                "end_time": getattr(span, "end_time", None),
            }
            records.append(record)
        with self._path.open("a", encoding="utf-8") as fp:
            for record in records:
                fp.write(json.dumps(record) + "\n")
        if _OTEL_AVAILABLE:
            return _otel_trace.Status(_otel_trace.StatusCode.OK)  # type: ignore[attr-defined]
        return None

    def shutdown(self) -> None:  # pragma: no cover - no-op
        return None


def configure_tracing(service_name: str, *, span_log_path: str | None = None) -> None:
    """Initialise the tracer provider with console and file exporters if available."""

    if not _OTEL_AVAILABLE:
        logger.info("OpenTelemetry not available; using no-op tracer")
        return

    resource = Resource.create({SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    span_log_file = span_log_path or os.getenv("SPAN_LOG_PATH")
    if span_log_file:
        provider.add_span_processor(BatchSpanProcessor(_FileSpanExporter(span_log_file)))
    _otel_trace.set_tracer_provider(provider)


def get_tracer() -> Any:
    """Return the global tracer used by internal agents."""

    return (
        _otel_trace.get_tracer(__name__)
        if _OTEL_AVAILABLE
        else _NoOpTraceAPI().get_tracer(__name__)
    )


@contextmanager
def start_span(
    name: str,
    *,
    kind: Any = _OtelSpanKind.INTERNAL,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """Context manager to create and activate a span with optional attributes."""

    tracer = get_tracer()
    with tracer.start_as_current_span(name=name, kind=kind) as span:
        if attributes:
            span.set_attributes(attributes)
        yield span
        if hasattr(span, "set_attribute"):
            span.set_attribute("span.end_time", datetime.utcnow().isoformat())


def current_trace_ids() -> dict[str, str]:
    """Return the current trace and span identifiers if available."""

    trace_api = _otel_trace if _OTEL_AVAILABLE else _NoOpTraceAPI()
    span = trace_api.get_current_span()
    context = span.get_span_context()
    if not context or not getattr(context, "is_valid", False):
        return {}
    return {
        "trace_id": format(getattr(context, "trace_id", 0), "032x"),
        "span_id": format(getattr(context, "span_id", 0), "016x"),
    }


__all__ = [
    "configure_tracing",
    "get_tracer",
    "start_span",
    "current_trace_ids",
]
