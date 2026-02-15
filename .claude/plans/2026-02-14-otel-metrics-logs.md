# OpenTelemetry Metrics, OTLP Log Export, and Observability Improvements

**Created:** 2026-02-14
**Author:** Architect (Opus)
**Status:** Ready for implementation

---

## 1. Feature Overview

Extend the existing OpenTelemetry tracing infrastructure to add **metrics** (MeterProvider), **OTLP log export** (LoggerProvider bridging Python logging), improve **trace coverage** (SQLAlchemy instrumentation), **unify debug logs into OTel** (replacing the DB-backed DebugLog), and surface metrics in the **Admin Portal Diagnostics** dashboard.

**What exists today:**
- TracerProvider with file exporter (JSONL with rotation), console exporter, conditional OTLP gRPC exporter
- FastAPI auto-instrumentation (`opentelemetry-instrumentation-fastapi`)
- LiteLLM auto-instrumentation (`openinference-instrumentation-litellm`)
- DiagnosticsService reads `spans.jsonl` for health metrics, latency percentiles, error hotspots
- Debug logs: DB-backed `DebugLog` model (toggleable) + file-backed `app_logs.jsonl` (WARNING+)
- Security events: `system_events.jsonl` with span event correlation

**What this plan adds:**
1. **MeterProvider** -- counters, histograms, gauges for request counts, LLM token usage, tool execution, error rates
2. **LoggerProvider** -- bridge Python logging to OTLP so logs correlate with traces
3. **Debug log unification** -- migrate DebugLogger from PostgreSQL to OTel structured logs (JSONL file + optional OTLP export), remove `debug_logs` table
4. **SQLAlchemy instrumentation** -- DB query span tracing
5. **Metrics dashboard tab** in Admin Portal Diagnostics
6. **Updated debug log viewer** -- reads from JSONL instead of PostgreSQL

---

## 2. Architecture Decisions

### Layer Placement

All new observability code lives in `core/observability/` (Layer 4 -- core). This is correct because:
- Observability is infrastructure, not business logic
- All layers need to emit metrics/logs
- No upward imports required

```
core/observability/
    tracing.py       -- (EXISTING) TracerProvider, spans
    logging.py       -- (MODIFY) Add OTel LoggerProvider bridge
    metrics.py       -- (NEW) MeterProvider, metric instruments
    debug_logger.py  -- (NEW) OTel-based DebugLogger replacement
    security_logger.py -- (EXISTING, no changes)
    error_codes.py   -- (EXISTING, no changes)
    tests/
        test_span_rotation.py   -- (EXISTING)
        test_metrics.py         -- (NEW)
        test_otel_logging.py    -- (NEW)
        test_debug_logger.py    -- (NEW)
```

### Dependency Flow

```
interfaces/http/app.py
    -> core/observability/tracing.py       (configure_tracing -- existing)
    -> core/observability/metrics.py       (configure_metrics -- NEW)
    -> core/observability/logging.py       (setup_logging -- MODIFY to add OTel bridge)

interfaces/http/admin_diagnostics.py
    -> core/observability/metrics.py       (read in-memory metric snapshots)

interfaces/http/admin_debug.py
    -> core/observability/debug_logger.py  (MODIFY -- read JSONL instead of DB)

core/runtime/service.py
    -> core/observability/debug_logger.py  (MODIFY -- emit OTel logs instead of DB writes)
```

No cross-module imports. No architecture violations.

### Debug Log Unification -- Full Migration to OTel

**Current state (3 parallel log systems):**
- **DebugLog** (DB-backed, `debug_logs` table): Toggleable verbose tracing. Stores full LLM prompts, tool args/results, supervisor decisions. Written to DB via `DebugLogger`. Accessed via `/platformadmin/debug/`.
- **app_logs.jsonl** (file-backed): Standard Python logging at WARNING+.
- **spans.jsonl** (file-backed): OpenTelemetry span data.

**Decision: Full unification. Debug logs ARE just verbose OTel log records.**

The `DebugLogger` currently writes to 3 places: PostgreSQL, OTel span attributes, and Python logger. This is over-engineered. Debug events are structured log records with a `trace_id`, `event_type`, and `event_data` -- exactly what OTel LogRecords are for.

**Migration approach:**
1. Replace `DebugLogger` DB writes with OTel log emission at DEBUG severity
2. Store debug logs to a dedicated JSONL file (`data/debug_logs.jsonl`) via a custom file exporter (same pattern as `spans.jsonl`)
3. Keep the toggle mechanism (via `SystemConfig` table -- only the flag, not the log data)
4. Keep OTel span attribute population (still useful for trace waterfall views)
5. Update admin debug page to read from JSONL instead of PostgreSQL
6. Drop the `debug_logs` DB table via Alembic migration
7. Keep `SystemConfig` table (used for other config too)

**What this eliminates:**
- `DebugLog` model in `core/db/models.py`
- DB writes on every debug event (reduces DB load)
- Manual cleanup endpoint (file rotation handles retention)
- The conceptual split between "debug logs" and "OTel logs"

**What stays:**
- Toggle on/off (stored in `SystemConfig`, cached with 30s TTL)
- Admin debug page at `/platformadmin/debug/` (same UI, different backend)
- Security event logging (`system_events.jsonl`) -- separate concern, stays as-is
- OTel span attributes for debug events (already there, keep for trace views)

---

## 3. Implementation Roadmap

### Phase 1: Core Metrics Infrastructure

#### Step 1.1: Create `core/observability/metrics.py`

**Engineer tasks:**
- Create new file `services/agent/src/core/observability/metrics.py`

**File:** `services/agent/src/core/observability/metrics.py` (CREATE)

```python
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
from contextlib import contextmanager
from collections.abc import Iterator

try:
    from opentelemetry import metrics as _otel_metrics
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import (
        ConsoleMetricExporter,
        PeriodicExportingMetricReader,
    )
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
request_counter: _NoOpCounter = _NoOpCounter()
request_error_counter: _NoOpCounter = _NoOpCounter()
llm_call_counter: _NoOpCounter = _NoOpCounter()
llm_token_counter: _NoOpCounter = _NoOpCounter()
tool_call_counter: _NoOpCounter = _NoOpCounter()
tool_error_counter: _NoOpCounter = _NoOpCounter()
skill_step_counter: _NoOpCounter = _NoOpCounter()

# Histograms
request_duration_histogram: _NoOpHistogram = _NoOpHistogram()
llm_call_duration_histogram: _NoOpHistogram = _NoOpHistogram()
tool_call_duration_histogram: _NoOpHistogram = _NoOpHistogram()

# Up-down counters (gauges)
active_requests_gauge: _NoOpUpDownCounter = _NoOpUpDownCounter()


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

    provider = MeterProvider(resource=resource, metric_readers=readers)
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
        llm_token_counter.add(
            completion_tokens, attributes={"model": model, "type": "completion"}
        )

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
```

**Ops tasks (after Engineer completes):**
- Run `stack check --no-fix` to verify no lint/type errors

**Files affected:**
- `services/agent/src/core/observability/metrics.py` (CREATE)

---

#### Step 1.2: Wire metrics into `app.py` startup

**Engineer tasks:**
- Modify `services/agent/src/interfaces/http/app.py` to call `configure_metrics()` at startup
- Add metrics recording to the `request_metrics_middleware`

**Changes to `services/agent/src/interfaces/http/app.py`:**

1. Add import near the other observability imports (around line 31-33):

```python
from core.observability.metrics import configure_metrics
```

2. In `create_app()`, after the `configure_tracing()` call (around line 136), add:

```python
    configure_metrics(settings.app_name)
```

3. In the `request_metrics_middleware` function (around line 352-377), add metric recording. Replace the existing middleware with an enhanced version that also records OTel metrics:

Find this block (lines 352-377):
```python
    @app.middleware("http")
    async def request_metrics_middleware(request: Request, call_next: Any) -> Any:
        """Track request timing and log slow requests."""
        start_time = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000
        ...
```

Add after `response = await call_next(request)` and `duration_ms = ...` calculation, before the response headers line:

```python
        # Record OTel metrics for agent API endpoints
        if request.url.path.startswith(("/v1/agent", "/v1/chat/completions", "/chat/completions")):
            from core.observability.metrics import request_counter, request_duration_histogram
            status_str = "error" if response.status_code >= 400 else "ok"
            attrs = {"http.route": request.url.path, "status": status_str}
            request_counter.add(1, attributes=attrs)
            request_duration_histogram.record(duration_ms, attributes=attrs)
```

**Ops tasks:**
- Run `stack check`

**Files affected:**
- `services/agent/src/interfaces/http/app.py` (MODIFY -- 3 small changes)

---

#### Step 1.3: Instrument LLM calls with metrics

**Engineer tasks:**
- Modify `services/agent/src/core/runtime/litellm_client.py` to record LLM metrics after each call

Find the main completion method in `litellm_client.py`. After a successful LLM call that returns a response, add:

```python
from core.observability.metrics import record_llm_call

# After getting the response, extract token usage:
usage = getattr(response, "usage", None)
prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
completion_tokens = getattr(usage, "completion_tokens", 0) or 0

record_llm_call(
    model=model,
    duration_ms=duration_ms,  # from existing timing
    prompt_tokens=prompt_tokens,
    completion_tokens=completion_tokens,
)
```

The Engineer should locate the `call()` or `complete()` method in `litellm_client.py` and instrument it. The file already has timing logic (`time.perf_counter()`) that should be reused.

**Files affected:**
- `services/agent/src/core/runtime/litellm_client.py` (MODIFY)

---

#### Step 1.4: Instrument tool execution with metrics

**Engineer tasks:**
- Modify `services/agent/src/core/runtime/tool_runner.py` to record tool metrics

Find the tool execution method. After tool execution completes, add:

```python
from core.observability.metrics import record_tool_call

record_tool_call(
    tool_name=tool_name,
    duration_ms=duration_ms,
    success=not error_occurred,
)
```

**Files affected:**
- `services/agent/src/core/runtime/tool_runner.py` (MODIFY)

---

#### Step 1.5: Instrument skill steps with metrics

**Engineer tasks:**
- Modify `services/agent/src/core/skills/executor.py` to record skill step metrics

After a skill step completes (where `StepOutcome` is determined), add:

```python
from core.observability.metrics import record_skill_step

record_skill_step(
    skill_name=skill_name,
    outcome=outcome.value,  # SUCCESS, RETRY, REPLAN, ABORT
)
```

**Files affected:**
- `services/agent/src/core/skills/executor.py` (MODIFY)

---

### Phase 2: OTLP Log Export

#### Step 2.1: Add OTel LoggerProvider bridge to `logging.py`

**Engineer tasks:**
- Modify `services/agent/src/core/observability/logging.py` to optionally bridge Python logging to OTel

The key change: when `OTEL_EXPORTER_OTLP_ENDPOINT` is set, add an `opentelemetry.sdk._logs.LoggingHandler` to the root logger so log records flow to the OTLP Collector alongside traces and metrics.

**Changes to `services/agent/src/core/observability/logging.py`:**

Add this function after the existing `setup_logging()`:

```python
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

        logging.getLogger(__name__).info(
            "OTel log bridge configured to %s", otlp_endpoint
        )
    except ImportError:
        logging.getLogger(__name__).info(
            "OTel log export packages not available; skipping log bridge"
        )
    except Exception:
        logging.getLogger(__name__).warning(
            "Failed to configure OTel log bridge", exc_info=True
        )
```

Then wire it into `app.py` startup (after `configure_tracing()`):

```python
from core.observability.logging import setup_otel_log_bridge
setup_otel_log_bridge(settings.app_name)
```

**IMPORTANT NOTE for the Engineer:** The OTel log export packages are part of the `opentelemetry-exporter-otlp` package which is already installed. The `opentelemetry.sdk._logs` module is part of `opentelemetry-sdk` (also installed). The underscore prefix (`_logs`) is intentional -- this is the OTel Python SDK's current API surface for logs. It is stable and widely used despite the underscore naming.

**Ops tasks:**
- Run `stack check`

**Files affected:**
- `services/agent/src/core/observability/logging.py` (MODIFY -- add function)
- `services/agent/src/interfaces/http/app.py` (MODIFY -- add 2 lines)

---

### Phase 3: Improve Trace Coverage

#### Step 3.1: Add SQLAlchemy instrumentation

**Engineer tasks:**
- Add `opentelemetry-instrumentation-sqlalchemy` to `pyproject.toml`
- Instrument the SQLAlchemy engine in `tracing.py` or `app.py`

1. Add to `services/agent/pyproject.toml` under `[tool.poetry.dependencies]`:

```toml
opentelemetry-instrumentation-sqlalchemy = "^0.60b1"
```

2. Add instrumentation call in `services/agent/src/core/observability/tracing.py`, at the end of `configure_tracing()`, after the LiteLLM instrumentation block (after line 348):

```python
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
```

**IMPORTANT:** The `SQLAlchemyInstrumentor` uses the `sqlalchemy.event` system and does NOT require the engine instance at instrumentation time when using `instrument()` without the `engine` parameter. It instruments all engines globally. If the Engineer wants to instrument a specific engine, they can pass `engine=engine.sync_engine` (the sync engine from the async wrapper). But global instrumentation is simpler and correct here since we have a single engine.

**Ops tasks:**
- Run `poetry lock` to update lockfile
- Run `stack check`

**Files affected:**
- `services/agent/pyproject.toml` (MODIFY -- add 1 dependency)
- `services/agent/src/core/observability/tracing.py` (MODIFY -- add SQLAlchemy block)

---

### Phase 4: Dashboard, API, and Cross-Linking

#### Step 4.1: Add OTel metrics endpoint to diagnostics router

**Engineer tasks:**
- Add a new endpoint to `services/agent/src/interfaces/http/admin_diagnostics.py` that returns the in-memory metric snapshot

```python
@router.get("/otel-metrics", dependencies=[Depends(verify_admin_user)])
async def get_otel_metrics() -> dict[str, float]:
    """Get OpenTelemetry metric snapshot for dashboard display."""
    from core.observability.metrics import get_metric_snapshot
    return get_metric_snapshot()
```

**Files affected:**
- `services/agent/src/interfaces/http/admin_diagnostics.py` (MODIFY)

---

#### Step 4.2: Add Metrics cards and "Recent Errors" widget to diagnostics dashboard

**Engineer tasks:**
- Modify `_get_diagnostics_content()` in `admin_diagnostics.py` to add:

**a) Live Platform Metrics (OTel) cards:**

```html
<h2 class="diag-section-title" style="margin-top:32px">Live Platform Metrics (OTel)</h2>
<div class="diag-metric-cards" id="otelMetricCards">
    <div class="diag-m-card">
        <div class="diag-m-title">Total Requests</div>
        <div class="diag-m-value" id="otelReqTotal">-</div>
    </div>
    <div class="diag-m-card">
        <div class="diag-m-title">Error Rate</div>
        <div class="diag-m-value" id="otelErrorRate">-</div>
    </div>
    <div class="diag-m-card">
        <div class="diag-m-title">Avg Latency</div>
        <div class="diag-m-value" id="otelAvgLatency">-</div>
    </div>
    <div class="diag-m-card">
        <div class="diag-m-title">LLM Tokens</div>
        <div class="diag-m-value" id="otelLlmTokens">-</div>
    </div>
    <div class="diag-m-card">
        <div class="diag-m-title">Tool Errors</div>
        <div class="diag-m-value" id="otelToolErrors">-</div>
    </div>
    <div class="diag-m-card">
        <div class="diag-m-title">Active Requests</div>
        <div class="diag-m-value" id="otelActiveReqs">-</div>
    </div>
</div>
```

**b) "Recent Errors" section** that links to both traces and debug logs:

```html
<h2 class="diag-section-title" style="margin-top:32px">Recent Errors</h2>
<table id="recentErrorsTable">
    <thead>
        <tr>
            <th>Time</th>
            <th>Trace</th>
            <th>Error</th>
            <th>Actions</th>
        </tr>
    </thead>
    <tbody id="recentErrorsBody">
        <!-- Populated by JS from /api/investigate or trace search -->
    </tbody>
</table>
```

Each error row links to:
- Trace detail (click trace_id → jumps to trace view)
- Debug logs for that trace (link to `/platformadmin/debug/?trace_id=XXX`)

**c) JavaScript enhancements in `_get_diagnostics_js()`:**

```javascript
async function loadOtelMetrics() {
    const res = await fetchWithErrorHandling(`${API_BASE}/otel-metrics`);
    if (!res) return;
    const data = await res.json();

    // Computed fields
    const total = data['requests.total'] || 0;
    const errors = data['requests.errors'] || 0;
    const durationSum = data['requests.duration_ms_sum'] || 0;
    const errorRate = total > 0 ? ((errors / total) * 100).toFixed(1) + '%' : '0%';
    const avgLatency = total > 0 ? Math.round(durationSum / total) + 'ms' : '-';
    const tokens = data['llm.tokens.total'] || 0;

    document.getElementById('otelReqTotal').innerText = Math.round(total);
    document.getElementById('otelErrorRate').innerText = errorRate;
    document.getElementById('otelAvgLatency').innerText = avgLatency;
    document.getElementById('otelLlmTokens').innerText = tokens > 1000 ? (tokens/1000).toFixed(1) + 'k' : Math.round(tokens);
    document.getElementById('otelToolErrors').innerText = Math.round(data['tools.errors'] || 0);
    document.getElementById('otelActiveReqs').innerText = Math.round(data['requests.active'] || 0);
}

async function loadRecentErrors() {
    // Fetch error traces from traces/search?status=ERR
    const res = await fetchWithErrorHandling(`${API_BASE}/traces/search?status=ERR&limit=10`);
    if (!res) return;
    const errors = await res.json();
    const tbody = document.getElementById('recentErrorsBody');
    if (!errors.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="empty-state">No recent errors</td></tr>';
        return;
    }
    tbody.innerHTML = errors.map(e => `
        <tr>
            <td>${new Date(e.start_time).toLocaleTimeString()}</td>
            <td><code><a href="#" onclick="loadTraceDetail('${e.trace_id}')">${e.trace_id.slice(0,12)}...</a></code></td>
            <td>${e.name || '-'}</td>
            <td>
                <a href="/platformadmin/debug/?trace_id=${e.trace_id}" class="btn btn-sm">Debug Logs</a>
            </td>
        </tr>
    `).join('');
}
```

**Files affected:**
- `services/agent/src/interfaces/http/admin_diagnostics.py` (MODIFY -- HTML, JS)

---

#### Step 4.3: Enhanced Diagnostic API endpoints

**Engineer tasks:**
- Modify `services/agent/src/interfaces/http/admin_api.py` to add 3 new endpoints:

**a) `/api/otel-metrics` -- Enhanced metrics with computed insights:**

```python
@router.get("/otel-metrics")
async def get_otel_metrics(
    auth: AdminUser | APIKeyUser = Depends(get_api_key_auth),
) -> dict[str, Any]:
    """Get OpenTelemetry metrics with computed insights.

    Returns raw counters plus computed fields useful for AI diagnosis:
    - error_rate_pct: Error rate as percentage
    - avg_request_duration_ms: Average request latency
    - avg_llm_duration_ms: Average LLM call latency
    - top_failing_tools: Tools with highest error counts
    """
    from core.observability.metrics import get_metric_snapshot

    snapshot = get_metric_snapshot()

    # Compute derived insights
    total_req = snapshot.get("requests.total", 0)
    total_errors = snapshot.get("requests.errors", 0)
    duration_sum = snapshot.get("requests.duration_ms_sum", 0)
    llm_calls = snapshot.get("llm.calls.total", 0)
    llm_duration_sum = snapshot.get("llm.duration_ms_sum", 0)

    return {
        "counters": snapshot,
        "insights": {
            "error_rate_pct": round((total_errors / total_req * 100), 2) if total_req > 0 else 0.0,
            "avg_request_duration_ms": round(duration_sum / total_req, 1) if total_req > 0 else 0.0,
            "avg_llm_duration_ms": round(llm_duration_sum / llm_calls, 1) if llm_calls > 0 else 0.0,
            "total_requests": int(total_req),
            "total_errors": int(total_errors),
            "total_llm_tokens": int(snapshot.get("llm.tokens.total", 0)),
            "total_tool_calls": int(snapshot.get("tools.calls.total", 0)),
            "total_tool_errors": int(snapshot.get("tools.errors", 0)),
            "active_requests": int(snapshot.get("requests.active", 0)),
        },
    }
```

**b) `/api/debug/logs` -- Queryable debug log endpoint:**

```python
@router.get("/debug/logs")
async def get_debug_logs(
    trace_id: str | None = Query(None, description="Filter by trace ID"),
    event_type: str | None = Query(None, description="Filter by event type (request, plan, tool_call, supervisor, etc.)"),
    limit: int = Query(50, le=500, description="Max entries to return"),
    auth: AdminUser | APIKeyUser = Depends(get_api_key_auth),
) -> list[dict[str, Any]]:
    """Query debug log entries from JSONL file.

    Returns structured debug events with full event_data.
    Filter by trace_id to get all events for a specific request,
    or by event_type to find specific event categories.

    Event types: request, history, plan, tool_call, skill_step,
    supervisor, completion_prompt, completion_response
    """
    from core.observability.debug_logger import read_debug_logs

    return await read_debug_logs(
        trace_id=trace_id,
        event_type=event_type,
        limit=limit,
    )
```

**c) `/api/investigate/{trace_id}` -- Unified trace investigation endpoint:**

```python
@router.get("/investigate/{trace_id}")
async def investigate_trace(
    trace_id: str,
    auth: AdminUser | APIKeyUser = Depends(get_api_key_auth),
) -> dict[str, Any]:
    """Get all observability data for a single trace in one call.

    Returns trace spans, debug log entries, and metrics context
    for the given trace_id. This is the primary endpoint for
    AI-driven diagnosis of individual requests.

    Response structure:
    {
        "trace_id": "abc123...",
        "spans": [...],           // All spans in this trace
        "debug_logs": [...],      // All debug events for this trace
        "summary": {              // Computed summary
            "duration_ms": 1234,
            "span_count": 8,
            "error_spans": 1,
            "tools_used": ["search", "homey"],
            "llm_calls": 2,
            "outcome": "SUCCESS" | "ABORT" | "REPLAN" | null
        }
    }
    """
    from core.observability.debug_logger import read_debug_logs

    settings = get_settings()
    diag_service = DiagnosticsService(settings)

    # Get trace spans
    traces = await diag_service.get_recent_traces(limit=5000, show_all=True)
    trace_spans = []
    for tg in traces:
        if tg.trace_id == trace_id:
            trace_spans = [
                {
                    "name": s.name,
                    "duration_ms": s.duration_ms,
                    "status": s.status,
                    "attributes": s.attributes,
                    "start_time": s.start_time,
                }
                for s in tg.spans
            ]
            break

    # Get debug logs for this trace
    debug_logs = await read_debug_logs(trace_id=trace_id, limit=500)

    # Compute summary
    total_duration = max((s.get("duration_ms", 0) for s in trace_spans), default=0)
    error_spans = sum(1 for s in trace_spans if s.get("status") == "ERROR")
    tools_used = list({
        dl.get("event_data", {}).get("tool_name", "")
        for dl in debug_logs
        if dl.get("event_type") == "tool_call"
    } - {""})
    llm_calls = sum(1 for dl in debug_logs if dl.get("event_type") in ("completion_prompt",))

    # Find outcome from supervisor events
    outcome = None
    for dl in debug_logs:
        if dl.get("event_type") == "supervisor":
            outcome = dl.get("event_data", {}).get("outcome")

    return {
        "trace_id": trace_id,
        "spans": trace_spans,
        "debug_logs": debug_logs,
        "summary": {
            "duration_ms": total_duration,
            "span_count": len(trace_spans),
            "error_spans": error_spans,
            "tools_used": tools_used,
            "llm_calls": llm_calls,
            "outcome": outcome,
        },
    }
```

**Files affected:**
- `services/agent/src/interfaces/http/admin_api.py` (MODIFY -- add 3 endpoints, update `/debug/stats` to use JSONL)

---

#### Step 4.4: Update `/api/debug/stats` to read from JSONL

**Engineer tasks:**
- The existing `/api/debug/stats` endpoint queries the `debug_logs` DB table. After Phase 7 drops that table, this endpoint must read from `data/debug_logs.jsonl` instead.
- Keep the same response shape (`DebugLogStats` model): `total_logs`, `by_event_type`, `by_hour`, `recent_errors`
- Use `read_debug_logs()` from the new debug_logger module

**Files affected:**
- `services/agent/src/interfaces/http/admin_api.py` (MODIFY -- rewrite `/debug/stats` internals)

---

#### Step 4.5: Cross-linking in debug log admin page

**Engineer tasks:**
- In `admin_debug.py` (updated in Phase 7 Step 7.3), add cross-links:

**a) Trace ID links:** Each `trace_id` in the debug log table becomes a clickable link to the diagnostics trace view:

```html
<td><code><a href="/platformadmin/diagnostics/?trace={log.trace_id}">{log.trace_id[:12]}...</a></code></td>
```

**b) URL query parameter support:** The debug page should accept `?trace_id=XXX` to pre-filter:

```python
@router.get("/")
async def debug_dashboard(
    trace_id: str | None = Query(None),  # Pre-filter from cross-link
    ...
):
```

When `trace_id` is provided, the table is pre-filtered to that trace and a banner shows: "Showing debug logs for trace `{trace_id}` -- [Show all](/platformadmin/debug/)"

**c) Back-link from diagnostics:** In the diagnostics trace detail view, add a link: "View debug logs for this trace" → `/platformadmin/debug/?trace_id=XXX`

**Files affected:**
- `services/agent/src/interfaces/http/admin_debug.py` (MODIFY -- add links and query param)
- `services/agent/src/interfaces/http/admin_diagnostics.py` (MODIFY -- add debug log link in trace detail)

---

### ~~Phase 5: OTel Collector~~ REMOVED

**Decision:** No additional container. The agent handles its own telemetry export. Point `OTEL_EXPORTER_OTLP_ENDPOINT` directly at any external backend's OTLP ingestion endpoint if needed.

---

### Phase 6: Tests

#### Step 6.1: Unit tests for metrics module

**Engineer tasks:**
- Create `services/agent/src/core/observability/tests/test_metrics.py`

**File:** `services/agent/src/core/observability/tests/test_metrics.py` (CREATE)

```python
"""Tests for OpenTelemetry metrics module."""

from __future__ import annotations

from core.observability.metrics import (
    _NoOpCounter,
    _NoOpHistogram,
    _NoOpUpDownCounter,
    _increment_snapshot,
    _metric_snapshot,
    get_metric_snapshot,
    measure_duration,
    record_llm_call,
    record_request_end,
    record_request_start,
    record_skill_step,
    record_tool_call,
)


def test_noop_counter_does_not_raise() -> None:
    """No-op counter should accept calls without error."""
    counter = _NoOpCounter()
    counter.add(1)
    counter.add(5, attributes={"key": "value"})


def test_noop_histogram_does_not_raise() -> None:
    """No-op histogram should accept calls without error."""
    hist = _NoOpHistogram()
    hist.record(42.0)
    hist.record(1.5, attributes={"key": "value"})


def test_noop_updown_counter_does_not_raise() -> None:
    """No-op up-down counter should accept calls without error."""
    gauge = _NoOpUpDownCounter()
    gauge.add(1)
    gauge.add(-1, attributes={"key": "value"})


def test_metric_snapshot_increment() -> None:
    """Snapshot counters should accumulate correctly."""
    # Clear snapshot for test isolation
    _metric_snapshot.clear()

    _increment_snapshot("test.counter", 1.0)
    _increment_snapshot("test.counter", 2.0)

    snapshot = get_metric_snapshot()
    assert snapshot["test.counter"] == 3.0


def test_metric_snapshot_returns_copy() -> None:
    """get_metric_snapshot should return a copy, not the original dict."""
    _metric_snapshot.clear()
    _increment_snapshot("test.key", 1.0)

    snapshot = get_metric_snapshot()
    snapshot["test.key"] = 999.0  # Modify the copy

    assert _metric_snapshot["test.key"] == 1.0  # Original unchanged


def test_record_request_updates_snapshot() -> None:
    """record_request_start/end should update the metric snapshot."""
    _metric_snapshot.clear()

    start = record_request_start()
    assert _metric_snapshot.get("requests.active", 0) == 1.0

    record_request_end(start, status="ok", platform="test")
    assert _metric_snapshot.get("requests.total", 0) == 1.0
    assert _metric_snapshot.get("requests.active", 0) == 0.0


def test_record_request_error() -> None:
    """Errored requests should increment error counter."""
    _metric_snapshot.clear()

    start = record_request_start()
    record_request_end(start, status="error", platform="test", error=True)

    assert _metric_snapshot.get("requests.errors", 0) == 1.0


def test_record_llm_call_updates_snapshot() -> None:
    """LLM call recording should update token and call counts."""
    _metric_snapshot.clear()

    record_llm_call(
        model="test-model",
        duration_ms=100.0,
        prompt_tokens=50,
        completion_tokens=30,
    )

    assert _metric_snapshot.get("llm.calls.total", 0) == 1.0
    assert _metric_snapshot.get("llm.tokens.total", 0) == 80.0


def test_record_tool_call_updates_snapshot() -> None:
    """Tool call recording should update call and error counts."""
    _metric_snapshot.clear()

    record_tool_call(tool_name="search", duration_ms=50.0, success=True)
    record_tool_call(tool_name="search", duration_ms=100.0, success=False)

    assert _metric_snapshot.get("tools.calls.total", 0) == 2.0
    assert _metric_snapshot.get("tools.errors", 0) == 1.0


def test_record_skill_step_updates_snapshot() -> None:
    """Skill step recording should update the step counter."""
    _metric_snapshot.clear()

    record_skill_step(skill_name="researcher", outcome="SUCCESS")
    record_skill_step(skill_name="researcher", outcome="RETRY")

    assert _metric_snapshot.get("skills.steps.total", 0) == 2.0


def test_measure_duration_context_manager() -> None:
    """measure_duration should capture elapsed time."""
    import time

    with measure_duration() as timing:
        time.sleep(0.01)  # Sleep 10ms

    assert timing["duration_ms"] >= 5.0  # Allow some tolerance
    assert timing["duration_ms"] < 1000.0  # Sanity check
```

#### Step 6.2: Unit tests for OTel log bridge

**File:** `services/agent/src/core/observability/tests/test_otel_logging.py` (CREATE)

```python
"""Tests for OTel log bridge in logging module."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

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
            file_handlers = [
                h
                for h in root.handlers
                if hasattr(h, "baseFilename")
            ]
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
```

**Ops tasks:**
- Run `stack check` to ensure all tests pass
- Verify test count increased

**Files affected:**
- `services/agent/src/core/observability/tests/test_metrics.py` (CREATE)
- `services/agent/src/core/observability/tests/test_otel_logging.py` (CREATE)

---

### Phase 7: Debug Log Migration to OTel

#### Step 7.1: Create OTel-based DebugLogger replacement

**Engineer tasks:**
- Create `services/agent/src/core/observability/debug_logger.py` -- new DebugLogger that emits structured OTel log records to a JSONL file instead of PostgreSQL

The new DebugLogger:
- Emits debug events as structured log records via Python logging at DEBUG level
- A dedicated `RotatingFileHandler` writes to `data/debug_logs.jsonl` (same rotation pattern as `app_logs.jsonl`)
- Each log record contains: `trace_id`, `event_type`, `conversation_id`, `event_data` (full JSON), `timestamp`
- Toggle on/off still stored in `SystemConfig` DB table (only the flag -- no log data in DB)
- Cache mechanism (30s TTL) preserved from existing implementation
- `_sanitize_args()` preserved (redacts password/token/secret/key/credential)
- Keeps `set_span_attributes()` calls for OTel trace waterfall enrichment
- When OTLP endpoint is configured, debug logs also flow to the external backend via the OTel LoggerProvider (set up in Phase 2)

**Key design:**

```python
# core/observability/debug_logger.py

import json
import logging
from logging.handlers import RotatingFileHandler

# Dedicated logger for debug events (separate from root logger)
_debug_log = logging.getLogger("agent.debug")

def configure_debug_log_handler(
    log_path: str = "data/debug_logs.jsonl",
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 3,
) -> None:
    """Set up rotating file handler for debug logs."""
    handler = RotatingFileHandler(log_path, maxBytes=max_bytes, backupCount=backup_count)
    handler.setFormatter(...)  # JSON formatter
    _debug_log.addHandler(handler)
    _debug_log.setLevel(logging.DEBUG)

class DebugLogger:
    """Emits debug events as OTel-correlated structured log records.

    Replaces the DB-backed DebugLogger. Events are written to
    data/debug_logs.jsonl with automatic rotation and optional
    OTLP export when a collector is configured.
    """

    def __init__(self, session: AsyncSession) -> None:
        # Session still needed for SystemConfig toggle reads
        self._session = session

    async def is_enabled(self) -> bool:
        # Same cache logic as before, reads SystemConfig

    async def log_event(self, trace_id: str, event_type: str,
                        event_data: dict, conversation_id: str | None = None) -> None:
        if not await self.is_enabled():
            return
        # 1. Write structured JSON to debug_logs.jsonl via _debug_log
        record = {
            "trace_id": trace_id,
            "event_type": event_type,
            "conversation_id": conversation_id,
            "event_data": _sanitize_args(event_data),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        _debug_log.debug(json.dumps(record, default=str))

        # 2. Keep OTel span attributes (already exists)
        set_span_attributes({f"debug.{event_type}": json.dumps(event_data, default=str)[:4000]})

    # All convenience methods (log_request, log_plan, log_tool_call, etc.)
    # stay with same signatures -- only internal implementation changes
```

**Important:** The `DebugLogger.__init__` still takes `AsyncSession` because it reads `SystemConfig` for the toggle. It just no longer writes `DebugLog` rows.

**Files affected:**
- `services/agent/src/core/observability/debug_logger.py` (CREATE)

---

#### Step 7.2: Update service.py to use new DebugLogger

**Engineer tasks:**
- Change imports in `services/agent/src/core/runtime/service.py` from `core.debug` to `core.observability.debug_logger`
- The call sites remain identical (same method signatures: `log_request()`, `log_plan()`, `log_tool_call()`, etc.)

Affected locations in `service.py`:
- Line ~541: `_execute_step()` -- `log_tool_call()`, `log_supervisor()`
- Line ~925: `_generate_completion()` -- `log_completion_prompt()`, `log_completion_response()`
- Line ~1051: `_execute_adaptive_loop()` -- `log_plan()`
- Line ~1435: `run_agent()` -- `log_request()`, `log_history()`

**Files affected:**
- `services/agent/src/core/runtime/service.py` (MODIFY -- import path change only)

---

#### Step 7.3: Update admin_debug.py to read from JSONL

**Engineer tasks:**
- Rewrite `/platformadmin/debug/` to read debug events from `data/debug_logs.jsonl` instead of PostgreSQL
- Keep the same UI (table, modal detail view, event type badges)
- Replace SQL queries with JSONL file reads (same pattern as DiagnosticsService)
- Keep toggle endpoint (still reads/writes `SystemConfig` via DB)
- Replace "Cleanup Old Logs" button with info about automatic file rotation
- Remove the `/debug/cleanup` endpoint (file rotation handles retention)
- Keep `/debug/log/{log_id}` but change to index-based lookup from JSONL

**Query approach for JSONL:**
```python
async def _read_debug_logs(
    trace_id: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Read debug logs from JSONL file with optional filters."""
    log_path = Path("data/debug_logs.jsonl")
    if not log_path.exists():
        return []

    lines = await asyncio.to_thread(_read_lines, log_path)
    results = []
    for line in reversed(lines):  # newest first
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if trace_id and entry.get("trace_id") != trace_id:
            continue
        if event_type and entry.get("event_type") != event_type:
            continue
        results.append(entry)
        if len(results) >= limit:
            break
    return results
```

**Files affected:**
- `services/agent/src/interfaces/http/admin_debug.py` (MODIFY -- significant rewrite)

---

#### Step 7.4: Wire debug log handler in app.py startup

**Engineer tasks:**
- Call `configure_debug_log_handler()` in `create_app()` startup, after logging setup

```python
from core.observability.debug_logger import configure_debug_log_handler
configure_debug_log_handler()
```

**Files affected:**
- `services/agent/src/interfaces/http/app.py` (MODIFY -- add 2 lines)

---

#### Step 7.5: Create Alembic migration to drop debug_logs table

**Engineer tasks:**
- Create a new Alembic migration that drops the `debug_logs` table
- Keep the `system_config` table (still used for toggle and other config)

```python
def upgrade() -> None:
    op.drop_index("ix_debug_logs_trace_id", table_name="debug_logs")
    op.drop_index("ix_debug_logs_conversation_id", table_name="debug_logs")
    op.drop_index("ix_debug_logs_event_type", table_name="debug_logs")
    op.drop_index("ix_debug_logs_created_at", table_name="debug_logs")
    op.drop_table("debug_logs")

def downgrade() -> None:
    # Recreate for rollback
    op.create_table("debug_logs", ...)
```

**Files affected:**
- `services/agent/alembic/versions/YYYYMMDD_drop_debug_logs.py` (CREATE)
- `services/agent/src/core/db/models.py` (MODIFY -- remove DebugLog class)

---

#### Step 7.6: Clean up old DebugLogger module

**Engineer tasks:**
- Remove or deprecate `services/agent/src/core/debug/logger.py` (the old DB-backed implementation)
- Update any remaining imports
- Remove `DebugLog` from `core/db/models.py`
- Remove the `DebugLogEntry` response model from `admin_debug.py` (no longer needed for DB queries)
- Update tests that reference the old DebugLogger

**Files affected:**
- `services/agent/src/core/debug/logger.py` (DELETE or gut)
- `services/agent/src/core/db/models.py` (MODIFY -- remove DebugLog)
- Any test files that mock `DebugLogger` DB behavior

---

#### Step 7.7: Tests for new DebugLogger

**Engineer tasks:**
- Create `services/agent/src/core/observability/tests/test_debug_logger.py`

Tests should cover:
- `log_event()` writes JSON line to file when enabled
- `log_event()` is a no-op when disabled
- `is_enabled()` cache behavior (returns cached value within TTL)
- `_sanitize_args()` redacts sensitive keys
- JSONL file reading with filters (trace_id, event_type)
- File rotation works (RotatingFileHandler)
- OTel span attributes are set during log_event

**Files affected:**
- `services/agent/src/core/observability/tests/test_debug_logger.py` (CREATE)

---

### Phase 8: Documentation and Troubleshooting Guide

#### Step 8.1: Update CLAUDE.md Diagnostic API section

**Engineer tasks:**
- Update the "Diagnostic API" section in `CLAUDE.md` (around line 827) with the new endpoints and a troubleshooting workflow

Replace the existing "Available Endpoints" table and "Example: AI Self-Diagnosis" section with:

```markdown
### Available Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /platformadmin/api/status` | System health status (HEALTHY/DEGRADED/CRITICAL) |
| `GET /platformadmin/api/otel-metrics` | **Live OTel metrics with computed insights (error rate, avg latency, token usage)** |
| `GET /platformadmin/api/investigate/{trace_id}` | **Unified view: trace spans + debug logs + summary for one request** |
| `GET /platformadmin/api/debug/logs` | **Query debug log entries (filter by trace_id, event_type)** |
| `GET /platformadmin/api/debug/stats` | Debug log statistics by event type |
| `GET /platformadmin/api/traces/search` | Search OpenTelemetry traces (filter by status, duration, trace_id) |
| `GET /platformadmin/api/traces/{trace_id}` | Get full trace detail with all spans |
| `GET /platformadmin/api/conversations` | List conversations with message counts |
| `GET /platformadmin/api/conversations/{id}/messages` | Get messages for a conversation |
| `GET /platformadmin/api/tools/stats` | Tool execution statistics |
| `GET /platformadmin/api/skills/stats` | Skill execution statistics |
| `GET /platformadmin/api/requests/stats` | HTTP request timing statistics |
| `GET /platformadmin/api/config` | Get system configuration entries |
| `GET /platformadmin/api/health` | Simple health check (no auth) |

### Troubleshooting Workflow

When diagnosing an issue, follow this sequence:

**Step 1: Check overall health**
```bash
curl -H "X-Api-Key: $KEY" $BASE/status
```
Look at `status` (HEALTHY/DEGRADED/CRITICAL), `recent_errors`, and `recommended_actions`.

**Step 2: Check metrics for anomalies**
```bash
curl -H "X-Api-Key: $KEY" $BASE/otel-metrics
```
Key fields in `insights`:
- `error_rate_pct` > 5% indicates a problem
- `avg_request_duration_ms` > 30000 indicates slowness
- `total_tool_errors` > 0 indicates tool failures

**Step 3: Find error traces**
```bash
curl -H "X-Api-Key: $KEY" "$BASE/traces/search?status=ERR&limit=10"
```
Returns recent error traces with trace_id, name, duration, and start_time.

**Step 4: Investigate a specific trace**
```bash
curl -H "X-Api-Key: $KEY" $BASE/investigate/{trace_id}
```
Returns everything for that request in one call:
- `spans`: All trace spans (timing, status, attributes)
- `debug_logs`: All debug events (LLM prompts, tool calls, supervisor decisions)
- `summary`: Computed overview (duration, error count, tools used, outcome)

**Step 5: Deep-dive into debug logs**
```bash
# All debug events for a trace
curl -H "X-Api-Key: $KEY" "$BASE/debug/logs?trace_id={trace_id}"

# Only supervisor decisions (to find ABORT/REPLAN)
curl -H "X-Api-Key: $KEY" "$BASE/debug/logs?event_type=supervisor&limit=20"

# Only tool calls (to find failures)
curl -H "X-Api-Key: $KEY" "$BASE/debug/logs?event_type=tool_call&limit=20"
```

### Quick Reference for Claude Code Sessions

When troubleshooting the live platform from a Claude Code session:
```bash
# Set up (once per session)
KEY=$(grep AGENT_DIAGNOSTIC_API_KEY .env | cut -d= -f2)
BASE="http://localhost:8001/platformadmin/api"

# Health check
curl -s -H "X-Api-Key: $KEY" $BASE/status | python -m json.tool

# Is something broken? Check error rate
curl -s -H "X-Api-Key: $KEY" $BASE/otel-metrics | python -m json.tool

# Find recent failures
curl -s -H "X-Api-Key: $KEY" "$BASE/traces/search?status=ERR&limit=5" | python -m json.tool

# Full investigation of a specific request
curl -s -H "X-Api-Key: $KEY" "$BASE/investigate/TRACE_ID_HERE" | python -m json.tool
```
```

**Also update** the "Key endpoints" list in the MEMORY.md Diagnostic API section to include the new endpoints.

**Files affected:**
- `CLAUDE.md` (MODIFY -- update Diagnostic API section)

---

#### Step 8.2: Update MEMORY.md with new observability notes

**Engineer tasks:**
- Update `/home/magnus/.claude/projects/-home-magnus-dev-ai-agent-platform/memory/MEMORY.md` to add notes about the new observability setup

Add to the "Diagnostic API" section:
```
- New: `/otel-metrics` (counters + computed insights), `/debug/logs` (queryable debug events), `/investigate/{trace_id}` (unified trace + debug logs + summary)
- Debug logs migrated from PostgreSQL to JSONL (`data/debug_logs.jsonl`) -- no more DB writes
- Troubleshooting: status -> otel-metrics -> traces/search?status=ERR -> investigate/{trace_id} -> debug/logs
```

Add to the "Architecture" section:
```
- OTel metrics in `core/observability/metrics.py` -- MeterProvider with in-memory snapshot for dashboard
- Debug logs unified into OTel: `core/observability/debug_logger.py` replaces `core/debug/logger.py` (JSONL, not DB)
- OTLP log bridge: Python WARNING+ logs bridged to OTel LoggerProvider when OTEL_EXPORTER_OTLP_ENDPOINT is set
- SQLAlchemy instrumented for DB query tracing
```

**Files affected:**
- `/home/magnus/.claude/projects/-home-magnus-dev-ai-agent-platform/memory/MEMORY.md` (MODIFY)

---

## 4. Configuration Changes

### New Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | (unset) | OTLP gRPC endpoint. When set, traces, metrics, and logs are exported via OTLP. Point directly at any OTLP-compatible backend. |
| `FORCE_CONSOLE_METRICS` | `false` | Force console metric output even when OTLP is configured |

### No New Settings Fields

The `OTEL_EXPORTER_OTLP_ENDPOINT` is already read from `os.environ` directly in `tracing.py` (this is the standard OTel convention). We follow the same pattern for metrics and logs. No new fields needed in the `Settings` class.

### Database Changes

- **Drop** `debug_logs` table (Alembic migration)
- **Keep** `system_config` table (still stores debug toggle flag and other config)

---

## 5. Testing Strategy

### Unit Tests (Phase 6)

| Test File | Tests | What It Covers |
|-----------|-------|----------------|
| `test_metrics.py` | 12 | No-op instruments, snapshot accumulation, recording functions, measure_duration |
| `test_otel_logging.py` | 3 | Log setup, file handler level, bridge no-op without endpoint |
| `test_debug_logger.py` | ~8 | OTel debug logger: write, no-op when disabled, cache, sanitize, JSONL read, rotation |
| `test_span_rotation.py` | 6 (existing) | Span file rotation |

### Manual Testing

1. **Metrics without collector:** Start the agent normally. Visit `/platformadmin/diagnostics/` -> "Metrics & Insights" tab. Make some requests. Refresh. The "Live Platform Metrics (OTel)" section should show accumulated counters.

2. **SQLAlchemy instrumentation:** Make a request, then check the trace waterfall. You should see new spans for DB queries (e.g., `SELECT`, `INSERT`).

3. **Diagnostic API:** Test the new endpoints:
   ```bash
   # Metrics with computed insights
   curl -H "X-Api-Key: $KEY" http://localhost:8001/platformadmin/api/otel-metrics

   # Query debug logs by trace
   curl -H "X-Api-Key: $KEY" "http://localhost:8001/platformadmin/api/debug/logs?trace_id=abc123"

   # Unified investigation (all signals for one trace)
   curl -H "X-Api-Key: $KEY" http://localhost:8001/platformadmin/api/investigate/abc123

   # Debug stats (now from JSONL)
   curl -H "X-Api-Key: $KEY" http://localhost:8001/platformadmin/api/debug/stats
   ```

4. **Debug log migration:** Enable debug mode, make a request. Visit `/platformadmin/debug/`. Verify:
   - Events appear in the table (read from `data/debug_logs.jsonl`)
   - "View" modal shows full event data
   - Toggle on/off works
   - File rotation: check that `debug_logs.jsonl` rotates at 10MB

5. **Cross-linking:** Click a trace_id in the debug log page -- should navigate to diagnostics trace view. From diagnostics trace detail, click "Debug Logs" -- should navigate to `/platformadmin/debug/?trace_id=XXX`. From "Recent Errors" widget, both links should work.

6. **AI investigation flow:** Use the `/api/investigate/{trace_id}` endpoint. Verify it returns spans, debug logs, and a computed summary with outcome, tools used, LLM call count, and error count in a single response.

---

## 6. Quality Checks

After all phases, the Engineer must ensure:

```bash
stack check
```

This runs Ruff (lint) -> Black (format) -> Mypy (types) -> Pytest (tests).

Specific things to verify:
- No `Any` types in the new `metrics.py` (use concrete types)
- All functions have type hints
- Absolute imports only (no relative imports)
- No blocking I/O in async contexts
- All new test files are in `src/core/observability/tests/`

---

## 7. Security Considerations

1. **No sensitive data in metrics:** Metric attributes must not contain PII, API keys, or user content. Only use generic labels like model name, tool name, status.
2. **OTLP endpoint validation:** The OTLP endpoint is an internal Docker network address. When set, ensure it points to a trusted collector only. No user-facing configuration of this endpoint.
3. **Metric snapshot read-only:** The `get_metric_snapshot()` function returns a copy of the internal dict, preventing mutation by callers.
4. **Admin portal access:** The new `/otel-metrics` endpoints require admin authentication (Entra ID or API key), consistent with existing diagnostic endpoints.
5. **Debug log sanitization:** The `_sanitize_args()` function redacts sensitive keys (password, token, secret, key, credential) before writing to JSONL, preventing accidental credential exposure in log files.

---

## 8. Success Criteria

- [ ] `configure_metrics()` creates OTel instruments that record to OTLP when endpoint is configured
- [ ] Metrics accumulate in the in-memory snapshot and display in the Diagnostics dashboard
- [ ] Diagnostics dashboard shows computed insights (error rate %, avg latency) and "Recent Errors" widget
- [ ] Python logging at WARNING+ is bridged to OTLP when endpoint is configured
- [ ] SQLAlchemy queries appear as spans in the trace waterfall
- [ ] Debug logs write to `data/debug_logs.jsonl` with rotation (no more DB writes)
- [ ] Debug log admin page reads from JSONL, toggle still works
- [ ] Debug log page supports `?trace_id=` pre-filtering and links trace_ids to diagnostics
- [ ] `debug_logs` DB table dropped via Alembic migration
- [ ] `/api/otel-metrics` returns counters + computed insights (error_rate_pct, avg durations, top failing tools)
- [ ] `/api/debug/logs` returns queryable debug log entries (filter by trace_id, event_type)
- [ ] `/api/investigate/{trace_id}` returns unified trace spans + debug logs + computed summary in one call
- [ ] `/api/debug/stats` reads from JSONL (not DB)
- [ ] CLAUDE.md updated with new API endpoints and troubleshooting workflow
- [ ] MEMORY.md updated with observability architecture notes
- [ ] All 23+ new tests pass
- [ ] `stack check` passes with no errors
- [ ] Existing functionality (traces, security events) is unaffected

---

## 9. Agent Delegation

### Engineer (Sonnet) - Implementation
- Create `metrics.py` with all instrument definitions and recording functions
- Modify `app.py` to wire up metrics and log bridge at startup
- Modify `litellm_client.py`, `tool_runner.py`, `executor.py` to record metrics
- Modify `logging.py` to add OTel log bridge function
- Modify `tracing.py` to add SQLAlchemy instrumentation
- Modify `admin_diagnostics.py` to add metrics tab content and endpoint
- Modify `admin_api.py`: add `/api/otel-metrics`, `/api/debug/logs`, `/api/investigate/{trace_id}`, update `/api/debug/stats`
- Add `pyproject.toml` dependency
- Create new `debug_logger.py` (OTel-based replacement)
- Update `service.py` imports to use new DebugLogger
- Rewrite `admin_debug.py` to read from JSONL
- Create Alembic migration to drop `debug_logs` table
- Remove old `DebugLog` model and `core/debug/logger.py`
- Write all test files
- Update CLAUDE.md with new API docs, troubleshooting workflow, and Claude Code quick reference
- Update MEMORY.md with observability architecture notes

### Ops (Haiku - 10x cheaper) - Quality & Deployment
- Run `stack check` after each phase
- Fix simple lint errors (auto-fixable)
- Run `poetry lock` after dependency changes
- Git operations (commit, push, PR)
- Report test results
- Escalate complex type errors to Engineer

### Cost Optimization
Each implementation step follows:
1. Engineer writes/modifies code
2. Ops runs `stack check`
3. Ops reports back (or escalates if complex errors)
4. Repeat for next step

---

## 10. Parallelism Analysis

### File-Level Dependency Map

Each file is only edited by specific phases. Parallel execution is safe when agents touch different files.

| File | Touched By | Notes |
|------|-----------|-------|
| `metrics.py` (NEW) | Phase 1 only | No conflicts |
| `debug_logger.py` (NEW) | Phase 7 only | No conflicts |
| `app.py` | Phase 1.2, Phase 2, Phase 7.4 | **Bottleneck** -- 3 phases add startup lines |
| `litellm_client.py` | Phase 1.3 only | No conflicts |
| `tool_runner.py` | Phase 1.4 only | No conflicts |
| `executor.py` (skills) | Phase 1.5 only | No conflicts |
| `logging.py` | Phase 2 only | No conflicts |
| `tracing.py` | Phase 3 only | No conflicts |
| `service.py` | Phase 7.2 only | No conflicts |
| `admin_debug.py` | Phase 7.3 + Phase 4.5 | Sequential (7 before 4) |
| `admin_diagnostics.py` | Phase 4.1, 4.2, 4.5 | All in Phase 4 -- sequential |
| `admin_api.py` | Phase 4.3, 4.4 | All in Phase 4 -- sequential |
| `models.py` | Phase 7.6 only | No conflicts |
| `pyproject.toml` | Phase 3 only | No conflicts |
| `CLAUDE.md` | Phase 8 only | No conflicts |

### Parallel Execution Groups

```
                        ┌─────────────────────┐
                        │  Group A (core)      │
                        │  Phase 1: metrics.py │
                        │  + app.py wiring     │
                        │  + instrumentation   │
                        │  + Phase 6.1 tests   │
                        └──────────┬───────────┘
                                   │
          ┌────────────────────────┼────────────────────────┐
          │                        │                         │
          ▼                        ▼                         ▼
┌─────────────────┐  ┌──────────────────────┐  ┌──────────────────┐
│  Group B        │  │  Group C             │  │  Group D         │
│  Phase 2: OTLP  │  │  Phase 3: SQLAlchemy │  │  Phase 7: Debug  │
│  log bridge     │  │  instrumentation     │  │  log migration   │
│  + Phase 6.2    │  │  (tracing.py +       │  │  (7.1-7.7)       │
│  tests          │  │  pyproject.toml)     │  │  + app.py wiring │
└────────┬────────┘  └──────────┬───────────┘  └────────┬─────────┘
          │                     │                         │
          └─────────────────────┼─────────────────────────┘
                                │
                                ▼
                   ┌─────────────────────────┐
                   │  Group E (presentation) │
                   │  Phase 4: Dashboard,    │
                   │  API endpoints,         │
                   │  cross-linking          │
                   └────────────┬────────────┘
                                │
                                ▼
                   ┌─────────────────────────┐
                   │  Group F (docs)         │
                   │  Phase 8: CLAUDE.md,    │
                   │  troubleshooting guide  │
                   └─────────────────────────┘
```

### Recommended Execution Strategy

**Sequential within a single Engineer agent** (safest, avoids `app.py` conflicts):

Phase ordering already accounts for this. The Engineer processes steps 1-13 sequentially.

**If using parallel agents** (faster, requires file grouping):

| Parallel Batch | Agents | Files Touched | Wait For |
|---------------|--------|---------------|----------|
| Batch 1 | Agent A: Phase 1 (metrics + app.py + instrumentation) | metrics.py, app.py, litellm_client.py, tool_runner.py, skills/executor.py | -- |
| Batch 2 | Agent B: Phase 2 (OTLP log bridge) | logging.py | Batch 1 (needs app.py done) |
| | Agent C: Phase 3 (SQLAlchemy) | tracing.py, pyproject.toml | -- (independent) |
| | Agent D: Phase 7.1-7.2 (debug_logger.py + service.py) | debug_logger.py, service.py | -- (independent) |
| Batch 3 | Agent E: Phase 7.3-7.4 (admin_debug.py + app.py wiring) | admin_debug.py, app.py | Batch 1 + 2 (app.py done) |
| | Agent F: Phase 7.5-7.7 (migration + cleanup + tests) | alembic, models.py, test files | Batch 2/D (debug_logger exists) |
| Batch 4 | Agent G: Phase 4 (dashboard + API + cross-linking) | admin_diagnostics.py, admin_api.py, admin_debug.py | Batch 3 (all backends ready) |
| Batch 5 | Agent H: Phase 8 (documentation) | CLAUDE.md | Batch 4 (APIs finalized) |

**Practical recommendation:** Use 2-3 agents max. The bottleneck is `app.py` (touched by Phases 1, 2, and 7). Group all `app.py` changes into one agent or do them sequentially within the same agent.

---

## 11. Implementation Order (Sequential)

The recommended sequential order (each step independently testable):

1. **Phase 1, Step 1.1** -- Create `metrics.py` (foundation, no dependencies)
2. **Phase 1, Step 1.2** -- Wire into `app.py` startup
3. **Phase 6, Step 6.1** -- Write metrics tests (verify foundation works)
4. **Phase 1, Steps 1.3-1.5** -- Instrument LLM, tools, skills (can be done together)
5. **Phase 2, Step 2.1** -- OTLP log bridge
6. **Phase 6, Step 6.2** -- Log bridge tests
7. **Phase 3, Step 3.1** -- SQLAlchemy instrumentation
8. **Phase 7, Steps 7.1-7.4** -- Debug log migration (new DebugLogger, update service.py, update admin_debug.py, wire in app.py)
9. **Phase 7, Step 7.5** -- Alembic migration to drop debug_logs table
10. **Phase 7, Steps 7.6-7.7** -- Cleanup old module + tests
11. **Phase 4, Steps 4.1-4.2** -- Diagnostics dashboard (metrics cards, recent errors widget)
12. **Phase 4, Steps 4.3-4.4** -- API endpoints (`/api/otel-metrics`, `/api/debug/logs`, `/api/investigate/{trace_id}`, update `/api/debug/stats`)
13. **Phase 4, Step 4.5** -- Cross-linking between debug page and diagnostics (trace_id links, query param filtering)
14. **Phase 8, Step 8.1** -- Update CLAUDE.md with new API docs and troubleshooting workflows

---

## 12. Architecture Validation Checklist

- [x] No core/ imports from upper layers
- [x] No cross-module imports (modules/X does not import modules/Y)
- [x] No new tools needed (this is infrastructure, not a tool)
- [x] Database change: DROP `debug_logs` table only (Alembic migration with rollback)
- [x] New endpoints have authentication (admin Entra ID or API key)
- [x] No blocking I/O in async contexts
- [x] Protocol-based DI not needed (metrics is infrastructure, not a service interface)
- [x] All new code uses absolute imports
- [x] No `Any` types in new code (except the try/except fallback blocks in tracing.py which already use them)
