"""OpenTelemetry tracing utilities for the agent platform.

The module gracefully degrades to a no-op implementation when the
``opentelemetry`` package is unavailable so that unit tests can run in
restricted environments without installing optional dependencies.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections import deque
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Any

try:  # pragma: no cover - exercised implicitly during imports
    from openinference.instrumentation.litellm import (
        LiteLLMInstrumentor,
    )
    from opentelemetry import trace as _otel_trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
        SimpleSpanProcessor,
        SpanExporter,
    )
    from opentelemetry.trace import SpanKind as _OtelSpanKind

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover - fallback branch for offline CI
    _OTEL_AVAILABLE = False
    _otel_trace = None  # type: ignore[assignment]
    BatchSpanProcessor = ConsoleSpanExporter = SimpleSpanProcessor = None  # type: ignore[assignment, misc]
    OTLPSpanExporter = None  # type: ignore[assignment, misc]
    LiteLLMInstrumentor = None  # type: ignore[assignment, misc]

    class SpanExporter:  # type: ignore[no-redef]
        """Fallback for SpanExporter when opentelemetry is missing."""

        pass

    Resource = TracerProvider = SERVICE_NAME = None  # type: ignore[assignment, misc]

    class _OtelSpanKind(str, Enum):  # type: ignore[no-redef]
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

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:  # pragma: no cover - trivial
        return None

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_attributes(self, attrs: dict[str, Any]) -> None:
        self.attributes.update(attrs)

    def get_span_context(self) -> _NoOpSpanContext:
        return _NoOpSpanContext()

    def add_event(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
        timestamp: int | None = None,
    ) -> None:
        pass

    def set_status(self, status: Any, description: str = "") -> None:
        self.attributes["status"] = status
        if description:
            self.attributes["status_description"] = description


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


class _FileSpanExporter(SpanExporter):
    """JSONL file exporter for spans with async write batching and rotation.

    Uses asyncio.to_thread() to offload file I/O when an event loop is available.
    Falls back to synchronous writes when called outside an async context.

    Rotation: When the file exceeds max_size_mb, rotates to .1, .2, etc.
    """

    def __init__(self, path: str, max_size_mb: int = 10, max_files: int = 3) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._queue: deque[dict[str, Any]] = deque()
        self._write_task: asyncio.Task[None] | None = None
        self._max_size_bytes = max_size_mb * 1024 * 1024
        self._max_files = max_files
        self._rotation_lock = threading.Lock()

    def export(self, spans: Sequence[Any]) -> Any:
        """Export spans to file. Batches writes when async context is available."""
        records = []
        for span in spans:
            ctx = span.get_span_context()
            parent = getattr(span, "parent", None)

            # reliable timestamp conversion (ns to ISO or ms)
            start_ns = getattr(span, "start_time", 0) or 0
            end_ns = getattr(span, "end_time", 0) or 0

            duration_ms = (end_ns - start_ns) / 1e6 if end_ns and start_ns else 0.0
            start_iso = datetime.utcfromtimestamp(start_ns / 1e9).isoformat() if start_ns else None

            # Status extraction
            status_name = "UNSET"
            status_obj = getattr(span, "status", None)

            # OTLP/Real Span
            if status_obj and hasattr(status_obj, "status_code"):
                status_code = status_obj.status_code
                status_name = getattr(status_code, "name", "UNSET")
            # NoOp Span (Attributes)
            elif span and hasattr(span, "attributes"):
                status_name = str(span.attributes.get("status", "UNSET"))

            record = {
                "name": getattr(span, "name", "unknown"),
                "context": {
                    "trace_id": format(getattr(ctx, "trace_id", 0), "032x"),
                    "span_id": format(getattr(ctx, "span_id", 0), "016x"),
                    "parent_id": (
                        format(getattr(parent, "span_id", 0), "016x") if parent else None
                    ),
                },
                "kind": getattr(getattr(span, "kind", None), "name", "INTERNAL"),
                "attributes": dict(getattr(span, "attributes", {}) or {}),
                "start_time": start_iso,
                "end_time": (
                    datetime.utcfromtimestamp(end_ns / 1e9).isoformat() if end_ns else None
                ),
                "duration_ms": duration_ms,
                "status": status_name,
            }
            records.append(record)

        # Queue records and ensure writer is running
        self._queue.extend(records)
        self._ensure_writer_running()

        if _OTEL_AVAILABLE:
            return _otel_trace.Status(_otel_trace.StatusCode.OK)
        return None

    def _ensure_writer_running(self) -> None:
        """Start background writer if event loop is available, otherwise write sync."""
        try:
            loop = asyncio.get_running_loop()
            if self._write_task is None or self._write_task.done():
                self._write_task = loop.create_task(self._background_writer())
        except RuntimeError:
            # No event loop - fall back to synchronous write
            self._write_sync()

    async def _background_writer(self) -> None:
        """Background task that batches and writes records asynchronously."""
        while self._queue:
            batch: list[dict[str, Any]] = []
            # Collect up to 100 records for batching
            while self._queue and len(batch) < 100:
                batch.append(self._queue.popleft())

            if batch:
                # Offload file I/O to thread pool
                await asyncio.to_thread(self._write_batch_sync, batch)

            # Small delay to allow more records to accumulate
            await asyncio.sleep(0.05)

    def _rotate_if_needed(self) -> None:
        """Rotate log file if it exceeds size threshold.

        Thread-safe rotation using file locking. Rotates spans.jsonl to spans.jsonl.1,
        spans.jsonl.1 to spans.jsonl.2, etc. Deletes oldest file if max_files exceeded.
        """
        with self._rotation_lock:
            # Check if rotation needed
            if not self._path.exists():
                return

            try:
                file_size = self._path.stat().st_size
                if file_size < self._max_size_bytes:
                    return

                # Rotate existing files: .2 -> .3, .1 -> .2, etc.
                for i in range(self._max_files - 1, 0, -1):
                    old_file = Path(f"{self._path}.{i}")
                    new_file = Path(f"{self._path}.{i + 1}")

                    if old_file.exists():
                        if i + 1 > self._max_files:
                            # Delete oldest file
                            old_file.unlink()
                            logger.info(f"Deleted oldest span log: {old_file}")
                        else:
                            # Rename to next number
                            old_file.rename(new_file)

                # Rotate current file to .1
                rotated_path = Path(f"{self._path}.1")
                self._path.rename(rotated_path)
                logger.info(
                    f"Rotated span log: {self._path} -> {rotated_path} "
                    f"(size: {file_size / 1024 / 1024:.2f} MB)"
                )

            except Exception as e:
                logger.warning(f"Failed to rotate span log {self._path}: {e}")

    def _write_batch_sync(self, records: list[dict[str, Any]]) -> None:
        """Synchronous write of a batch of records."""
        try:
            # Check if rotation needed before writing
            self._rotate_if_needed()

            with self._path.open("a", encoding="utf-8") as fp:
                for record in records:
                    fp.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.warning(f"Failed to write span batch to {self._path}: {e}")

    def _write_sync(self) -> None:
        """Fallback synchronous write when no event loop is available."""
        records = list(self._queue)
        self._queue.clear()
        if records:
            self._write_batch_sync(records)

    def shutdown(self) -> None:
        """Flush any remaining queued records on shutdown."""
        if self._queue:
            self._write_sync()


def configure_tracing(
    service_name: str,
    *,
    span_log_path: str | None = None,
    span_log_max_size_mb: int = 10,
    span_log_max_files: int = 3,
) -> None:
    """Initialise the tracer provider with OTLP, console, and file exporters if available.

    Args:
        service_name: Name of the service for tracing
        span_log_path: Optional path to write spans (JSONL format)
        span_log_max_size_mb: Maximum size of span log before rotation (default: 10MB)
        span_log_max_files: Maximum number of rotated files to keep (default: 3)
    """
    if not _OTEL_AVAILABLE:
        logger.info("OpenTelemetry not available; using no-op tracer")
        return

    resource = Resource.create({SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    # 1. Console Exporter (Simple) - Only if OTLP is missing (to reduce noise) or
    # explicitly requested
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not otlp_endpoint or os.getenv("FORCE_CONSOLE_TRACES", "false").lower() == "true":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    # 2. File Exporter (Batch) with rotation
    span_log_file = span_log_path or os.getenv("SPAN_LOG_PATH")
    if span_log_file:
        exporter = _FileSpanExporter(
            span_log_file,
            max_size_mb=span_log_max_size_mb,
            max_files=span_log_max_files,
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))

    # 3. OTLP Exporter (Batch) - Phoenix
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otlp_endpoint:
        logger.info(f"Configuring OTLP exporter to {otlp_endpoint}")
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))

    _otel_trace.set_tracer_provider(provider)

    # 4. Instrument LiteLLM
    if LiteLLMInstrumentor is not None:
        logger.info("Instrumenting LiteLLM for OpenInference")
        LiteLLMInstrumentor().instrument(tracer_provider=provider)

    # 5. Instrument SQLAlchemy
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument(
            tracer_provider=provider,
            enable_commenter=True,
        )
        logger.info("Instrumenting SQLAlchemy for query tracing")
    except ImportError:
        logger.debug("SQLAlchemy instrumentation not available")
    except Exception as e:
        logger.warning("Failed to instrument SQLAlchemy: %s", e)


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
            span.set_attribute("span.end_time", datetime.now().isoformat())


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


def set_span_attributes(attributes: dict[str, Any]) -> None:
    """Set attributes on the current active span.

    Filters out None values to prevent OTel warnings.
    """
    trace_api = _otel_trace if _OTEL_AVAILABLE else _NoOpTraceAPI()
    span = trace_api.get_current_span()

    # Filter out None values - OTel only accepts bool, str, bytes, int, float
    filtered_attrs = {k: v for k, v in attributes.items() if v is not None}

    if span and hasattr(span, "set_attributes"):
        span.set_attributes(filtered_attrs)
    elif span and hasattr(span, "set_attribute"):
        # Fallback for spans that only support single attribute setting
        for key, value in filtered_attrs.items():
            span.set_attribute(key, value)


def add_span_event(
    name: str,
    attributes: dict[str, Any] | None = None,
    timestamp: int | None = None,
) -> None:
    """Add an event to the current active span."""
    trace_api = _otel_trace if _OTEL_AVAILABLE else _NoOpTraceAPI()
    span = trace_api.get_current_span()
    if span and hasattr(span, "add_event"):
        span.add_event(name, attributes=attributes, timestamp=timestamp)


def set_span_status(status: str, description: str = "") -> None:
    """Set the status of the current active span.

    Args:
        status: One of "OK", "ERROR", "UNSET"
        description: Optional description of the status (e.g. error message)
    """
    trace_api = _otel_trace if _OTEL_AVAILABLE else _NoOpTraceAPI()
    span = trace_api.get_current_span()

    if _OTEL_AVAILABLE and span:
        try:
            unset_code = _otel_trace.StatusCode.UNSET
            status_code = getattr(_otel_trace.StatusCode, status.upper(), unset_code)
            span.set_status(_otel_trace.Status(status_code, description=description))
        except Exception as e:
            logger.warning(f"Failed to set span status: {e}")
    elif span and hasattr(span, "set_status"):
        span.set_status(status, description=description)


__all__ = [
    "configure_tracing",
    "get_tracer",
    "start_span",
    "current_trace_ids",
    "set_span_attributes",
    "add_span_event",
    "set_span_status",
]
