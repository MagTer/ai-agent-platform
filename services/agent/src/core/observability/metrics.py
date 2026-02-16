"""OpenTelemetry metrics for the agent platform.

Provides counters, histograms, and gauges for monitoring:
- HTTP request counts and latency
- LLM token usage and call duration
- Tool execution counts and duration
- Skill execution metrics
- Error rates by category

Gracefully degrades to no-op when OpenTelemetry is unavailable.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

try:
    from opentelemetry import metrics as _otel_metrics
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics._internal.aggregation import (
        ExplicitBucketHistogramAggregation,
    )
    from opentelemetry.sdk.metrics.export import (
        ConsoleMetricExporter,
        PeriodicExportingMetricReader,
    )
    from opentelemetry.sdk.metrics.view import View
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource

    _OTEL_METRICS_AVAILABLE = True
except ImportError:
    _OTEL_METRICS_AVAILABLE = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# No-op fallback types
# ---------------------------------------------------------------------------


class _NoOpCounter:
    """No-op counter when OTel is unavailable."""

    def add(self, amount: int | float, attributes: dict[str, str] | None = None) -> None:
        pass


class _NoOpHistogram:
    """No-op histogram when OTel is unavailable."""

    def record(self, value: float, attributes: dict[str, str] | None = None) -> None:
        pass


class _NoOpUpDownCounter:
    """No-op up-down counter when OTel is unavailable."""

    def add(self, amount: int | float, attributes: dict[str, str] | None = None) -> None:
        pass


# ---------------------------------------------------------------------------
# In-memory snapshot store for admin dashboard
# ---------------------------------------------------------------------------

# Thread-safe snapshot of recent metric values for the diagnostics dashboard.
# Updated by the callback instruments and explicit recording calls.
# This avoids querying the OTel SDK (which does not expose read-back APIs).
_metric_snapshot: dict[str, float] = {}


def get_metric_snapshot() -> dict[str, float]:
    """Return a copy of the current metric snapshot for dashboard display."""
    return dict(_metric_snapshot)


def _increment_snapshot(key: str, amount: float = 1.0) -> None:
    """Increment a snapshot counter (thread-safe for single-writer)."""
    _metric_snapshot[key] = _metric_snapshot.get(key, 0.0) + amount


# ---------------------------------------------------------------------------
# Metric instruments (module-level singletons)
# ---------------------------------------------------------------------------

# Counters
request_counter: Any = _NoOpCounter()
request_error_counter: Any = _NoOpCounter()
llm_call_counter: Any = _NoOpCounter()
llm_token_counter: Any = _NoOpCounter()
tool_call_counter: Any = _NoOpCounter()
tool_error_counter: Any = _NoOpCounter()
skill_step_counter: Any = _NoOpCounter()

# Histograms
request_duration_histogram: Any = _NoOpHistogram()
llm_call_duration_histogram: Any = _NoOpHistogram()
tool_call_duration_histogram: Any = _NoOpHistogram()

# Up-down counters (gauges)
active_requests_gauge: Any = _NoOpUpDownCounter()


def configure_metrics(
    service_name: str,
    *,
    export_interval_ms: int = 30000,
) -> None:
    """Initialize the MeterProvider with OTLP and/or console exporters.

    Args:
        service_name: Name of the service for metric resource.
        export_interval_ms: How often to export metrics (default 30s).
    """
    global request_counter, request_error_counter
    global llm_call_counter, llm_token_counter
    global tool_call_counter, tool_error_counter, skill_step_counter
    global request_duration_histogram, llm_call_duration_histogram
    global tool_call_duration_histogram
    global active_requests_gauge

    if not _OTEL_METRICS_AVAILABLE:
        logger.info("OpenTelemetry metrics not available; using no-op instruments")
        return

    resource = Resource.create({SERVICE_NAME: service_name})

    readers = []

    # OTLP exporter (same endpoint as traces)
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otlp_endpoint:
        logger.info("Configuring OTLP metric exporter to %s", otlp_endpoint)
        otlp_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=otlp_endpoint, insecure=True),
            export_interval_millis=export_interval_ms,
        )
        readers.append(otlp_reader)

    # Console exporter (only when no OTLP or explicitly requested)
    if not otlp_endpoint or os.getenv("FORCE_CONSOLE_METRICS", "false").lower() == "true":
        console_reader = PeriodicExportingMetricReader(
            ConsoleMetricExporter(),
            export_interval_millis=export_interval_ms,
        )
        readers.append(console_reader)

    if not readers:
        logger.info("No metric exporters configured; metrics will not be exported")
        return

    # Define latency bucket boundaries for percentile calculation
    # Buckets: 10ms, 25ms, 50ms, 100ms, 250ms, 500ms, 1s, 2.5s, 5s, 10s, 30s, 60s
    latency_buckets = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000]

    # Create views with explicit bucket boundaries for latency histograms
    views = [
        View(
            instrument_name="agent.requests.duration",
            aggregation=ExplicitBucketHistogramAggregation(boundaries=latency_buckets),
        ),
        View(
            instrument_name="agent.llm.calls.duration",
            aggregation=ExplicitBucketHistogramAggregation(boundaries=latency_buckets),
        ),
        View(
            instrument_name="agent.tools.calls.duration",
            aggregation=ExplicitBucketHistogramAggregation(boundaries=latency_buckets),
        ),
    ]

    provider = MeterProvider(resource=resource, metric_readers=readers, views=views)
    _otel_metrics.set_meter_provider(provider)

    meter = provider.get_meter("agent-platform", version="0.1.0")

    # --- Create instruments ---

    # Request metrics
    request_counter = meter.create_counter(
        name="agent.requests.total",
        description="Total number of agent requests",
        unit="1",
    )
    request_error_counter = meter.create_counter(
        name="agent.requests.errors",
        description="Total number of failed agent requests",
        unit="1",
    )
    request_duration_histogram = meter.create_histogram(
        name="agent.requests.duration",
        description="Agent request duration",
        unit="ms",
    )
    active_requests_gauge = meter.create_up_down_counter(
        name="agent.requests.active",
        description="Number of currently active agent requests",
        unit="1",
    )

    # LLM metrics
    llm_call_counter = meter.create_counter(
        name="agent.llm.calls.total",
        description="Total number of LLM API calls",
        unit="1",
    )
    llm_token_counter = meter.create_counter(
        name="agent.llm.tokens.total",
        description="Total LLM tokens consumed",
        unit="1",
    )
    llm_call_duration_histogram = meter.create_histogram(
        name="agent.llm.calls.duration",
        description="LLM API call duration",
        unit="ms",
    )

    # Tool metrics
    tool_call_counter = meter.create_counter(
        name="agent.tools.calls.total",
        description="Total number of tool executions",
        unit="1",
    )
    tool_error_counter = meter.create_counter(
        name="agent.tools.errors",
        description="Total number of failed tool executions",
        unit="1",
    )
    tool_call_duration_histogram = meter.create_histogram(
        name="agent.tools.calls.duration",
        description="Tool execution duration",
        unit="ms",
    )

    # Skill metrics
    skill_step_counter = meter.create_counter(
        name="agent.skills.steps.total",
        description="Total number of skill step executions",
        unit="1",
    )

    logger.info("OpenTelemetry metrics configured with %d reader(s)", len(readers))


# ---------------------------------------------------------------------------
# Convenience recording functions
# ---------------------------------------------------------------------------


def record_request_start() -> float:
    """Record the start of an agent request. Returns start time."""
    active_requests_gauge.add(1)
    _increment_snapshot("requests.active", 1.0)
    return time.perf_counter()


def record_request_end(
    start_time: float,
    *,
    status: str = "ok",
    platform: str = "unknown",
    error: bool = False,
) -> None:
    """Record the end of an agent request."""
    duration_ms = (time.perf_counter() - start_time) * 1000
    attrs = {"status": status, "platform": platform}

    request_counter.add(1, attributes=attrs)
    request_duration_histogram.record(duration_ms, attributes=attrs)
    active_requests_gauge.add(-1)

    _increment_snapshot("requests.total")
    _increment_snapshot("requests.active", -1.0)
    _increment_snapshot("requests.duration_ms_sum", duration_ms)

    if error:
        request_error_counter.add(1, attributes=attrs)
        _increment_snapshot("requests.errors")


def record_llm_call(
    *,
    model: str,
    duration_ms: float,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> None:
    """Record an LLM API call."""
    attrs = {"model": model}

    llm_call_counter.add(1, attributes=attrs)
    llm_call_duration_histogram.record(duration_ms, attributes=attrs)

    total_tokens = prompt_tokens + completion_tokens
    if total_tokens > 0:
        llm_token_counter.add(prompt_tokens, attributes={"model": model, "type": "prompt"})
        llm_token_counter.add(completion_tokens, attributes={"model": model, "type": "completion"})

    _increment_snapshot("llm.calls.total")
    _increment_snapshot("llm.tokens.total", float(total_tokens))
    _increment_snapshot("llm.duration_ms_sum", duration_ms)


def record_tool_call(
    *,
    tool_name: str,
    duration_ms: float,
    success: bool = True,
) -> None:
    """Record a tool execution."""
    attrs = {"tool": tool_name, "status": "ok" if success else "error"}

    tool_call_counter.add(1, attributes=attrs)
    tool_call_duration_histogram.record(duration_ms, attributes=attrs)

    _increment_snapshot("tools.calls.total")
    _increment_snapshot("tools.duration_ms_sum", duration_ms)

    if not success:
        tool_error_counter.add(1, attributes=attrs)
        _increment_snapshot("tools.errors")


def record_skill_step(
    *,
    skill_name: str,
    outcome: str,
) -> None:
    """Record a skill step execution."""
    attrs = {"skill": skill_name, "outcome": outcome}
    skill_step_counter.add(1, attributes=attrs)
    _increment_snapshot("skills.steps.total")


@contextmanager
def measure_duration() -> Iterator[dict[str, float]]:
    """Context manager that measures elapsed time in milliseconds.

    Usage:
        with measure_duration() as timing:
            do_work()
        print(timing["duration_ms"])
    """
    result: dict[str, float] = {"duration_ms": 0.0}
    start = time.perf_counter()
    try:
        yield result
    finally:
        result["duration_ms"] = (time.perf_counter() - start) * 1000


__all__ = [
    "configure_metrics",
    "get_metric_snapshot",
    "measure_duration",
    "record_llm_call",
    "record_request_end",
    "record_request_start",
    "record_skill_step",
    "record_tool_call",
]
