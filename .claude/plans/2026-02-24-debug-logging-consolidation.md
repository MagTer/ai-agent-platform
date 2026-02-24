# Debug Logging Consolidation into OTel Spans

**Date:** 2026-02-24
**Status:** Draft
**Author:** Architect (Opus)

---

## Feature Overview

Eliminate the separate `data/debug_logs.jsonl` system by emitting all debug events as proper OTel span events (`span.add_event()`). The `_FileSpanExporter` in `tracing.py` will capture these events alongside span attributes, and the Diagnostic API will read debug events from `spans.jsonl` instead of `debug_logs.jsonl`.

Additionally, `context_id` and `conversation_id` will be added to the root span at request start, making spans queryable per context/conversation.

### Why

- **Single source of truth:** Currently debug events live in JSONL (primary) and span attributes (secondary, never queried). This creates two parallel systems.
- **Better correlation:** With events inside spans, they inherit trace_id/span_id automatically and appear in trace waterfalls.
- **Context queryability:** Adding `context_id` to the root span enables per-tenant trace filtering.
- **Reduced disk I/O:** One rotating JSONL file instead of two.

---

## Architecture Decisions

1. **Layer placement:** All changes are within `core/observability/` (Layer 4) and `interfaces/http/` (Layer 1). No cross-layer violations.
2. **No new protocols needed:** This is an internal refactor within existing layers.
3. **Backward compatibility:** The `DebugLogger` class interface stays the same (callers unchanged). Only the internal implementation changes from JSONL-write to `span.add_event()`.
4. **Migration path:** Phase 1 writes to both (dual-write), Phase 2 removes JSONL. This plan implements both phases in one go since the JSONL is ephemeral (not a persistent store).

---

## Implementation Roadmap

### Step 1: Extend `_FileSpanExporter` to capture span events

**Engineer tasks:**

Modify `services/agent/src/core/observability/tracing.py`:

The `_FileSpanExporter.export()` method (line ~230) currently builds a `record` dict with `name`, `context`, `kind`, `attributes`, `start_time`, `end_time`, `duration_ms`, `status`. It does NOT extract `events` from spans.

Add an `events` field to the exported record. OTel SDK spans have an `events` attribute (a tuple of `Event` objects, each with `name`, `attributes`, `timestamp`).

In the `export()` method, after the `record` dict is built (around line 273), add:

```python
# Extract span events (debug events, exceptions, etc.)
raw_events = getattr(span, "events", None) or ()
events_list: list[dict[str, Any]] = []
for evt in raw_events:
    evt_attrs = dict(getattr(evt, "attributes", {}) or {})
    evt_ts = getattr(evt, "timestamp", None)
    evt_iso = (
        datetime.utcfromtimestamp(evt_ts / 1e9).isoformat()
        if evt_ts
        else None
    )
    events_list.append({
        "name": getattr(evt, "name", ""),
        "timestamp": evt_iso,
        "attributes": evt_attrs,
    })

record["events"] = events_list
```

Insert this block right before `records.append(record)` (line ~273).

**Files affected:**
- `services/agent/src/core/observability/tracing.py` (modify)

---

### Step 2: Add `context_id` and `conversation_id` to root span

**Engineer tasks:**

Modify `services/agent/src/core/runtime/service.py`:

In the `execute_stream()` method (line ~1397), the root span is created with:
```python
with start_span(
    "agent.request",
    attributes={
        "conversation_id": conversation_id,
        "input_size": len(request.prompt),
        "prompt": request.prompt[:500] if request.prompt else "",
    },
):
```

After the context is resolved (line ~1414, after `_setup_conversation_and_context` returns), add `context_id` to the span:

```python
# After line 1414 (after _setup_conversation_and_context)
from core.observability.tracing import set_span_attributes
if db_context:
    set_span_attributes({
        "context_id": str(db_context.id),
        "context_name": db_context.name or "",
    })
```

Note: `set_span_attributes` is already imported at line 38. No new import needed.

**Files affected:**
- `services/agent/src/core/runtime/service.py` (modify)

---

### Step 3: Rewrite `DebugLogger.log_event()` to use `span.add_event()`

**Engineer tasks:**

Modify `services/agent/src/core/observability/debug_logger.py`:

Replace the current `log_event()` implementation (lines 205-240) which writes to JSONL and sets span attributes. The new implementation should ONLY use `span.add_event()`:

```python
async def log_event(
    self,
    trace_id: str,
    event_type: str,
    event_data: dict[str, Any],
    conversation_id: str | None = None,
) -> None:
    """Log a debug event as an OTel span event.

    Args:
        trace_id: Trace ID for correlation.
        event_type: Type of event (request, plan, tool_call, etc.).
        event_data: Event-specific data.
        conversation_id: Optional conversation ID.
    """
    if not await self.is_enabled():
        return

    # Sanitize before attaching to span
    sanitized_data = _sanitize_args(event_data)

    # Build flat attributes dict for OTel event
    # OTel event attributes must be primitive types (str, int, float, bool)
    # or sequences of primitives. Complex dicts must be JSON-serialized.
    attrs: dict[str, Any] = {
        "debug.trace_id": trace_id,
        "debug.event_type": event_type,
    }
    if conversation_id:
        attrs["debug.conversation_id"] = conversation_id

    # Serialize event_data as JSON string (OTel doesn't support nested dicts)
    attrs["debug.event_data"] = json.dumps(sanitized_data, default=str)[:4000]

    # Add as span event on the current active span
    span = trace.get_current_span()
    if span.is_recording():
        span.add_event(f"debug.{event_type}", attributes=attrs)
```

**Important:** Remove the JSONL write (`_debug_log.debug(...)`) and the old span attribute set (`span.set_attribute(...)`).

**Also remove or deprecate:**
- `configure_debug_log_handler()` function (lines 49-79) -- mark as no-op with a deprecation log
- `_read_lines()` function (lines 149-162) -- will be removed in Step 5
- `read_debug_logs()` function (lines 106-146) -- will be replaced in Step 5
- `DEBUG_LOGS_PATH` constant (line 32) -- keep temporarily, remove in Step 5

For now in this step, keep `read_debug_logs()` and `configure_debug_log_handler()` as stubs that return empty results / do nothing, so existing callers don't break while we migrate them in steps 4-5.

```python
def configure_debug_log_handler(
    log_path: str | Path = DEBUG_LOGS_PATH,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 3,
) -> None:
    """No-op. Debug logs are now emitted as OTel span events.

    Kept for backward compatibility during migration.
    """
    logger_mod = logging.getLogger(__name__)
    logger_mod.info("configure_debug_log_handler is deprecated; debug events use OTel span events")


async def read_debug_logs(
    trace_id: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Read debug events from span events in spans.jsonl.

    Args:
        trace_id: Filter by trace ID.
        event_type: Filter by event type.
        limit: Maximum number of entries to return.

    Returns:
        List of debug log entries (newest first).
    """
    return await _read_debug_events_from_spans(
        trace_id=trace_id, event_type=event_type, limit=limit
    )
```

**Files affected:**
- `services/agent/src/core/observability/debug_logger.py` (modify)

---

### Step 4: Implement `_read_debug_events_from_spans()` in debug_logger.py

**Engineer tasks:**

Add a new function to `services/agent/src/core/observability/debug_logger.py` that reads debug events from `spans.jsonl` (the same file `_FileSpanExporter` writes to). This replaces the JSONL file reader.

```python
from core.runtime.config import get_settings


def _get_spans_path() -> Path:
    """Get the path to the spans JSONL file."""
    try:
        settings = get_settings()
        return Path(str(settings.trace_span_log_path or "data/spans.jsonl"))
    except Exception:
        return Path("data/spans.jsonl")


def _extract_debug_events_from_spans_file(
    spans_path: Path,
    trace_id: str | None,
    event_type: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Extract debug events from span events in spans.jsonl (blocking I/O).

    Each span record in spans.jsonl now has an "events" list. Debug events
    have names starting with "debug." and contain structured attributes.

    Args:
        spans_path: Path to spans.jsonl file.
        trace_id: Optional trace_id filter.
        event_type: Optional event_type filter (e.g., "tool_call").
        limit: Maximum results to return.

    Returns:
        List of debug event dicts, newest first.
    """
    if not spans_path.exists():
        return []

    results: list[dict[str, Any]] = []

    try:
        with spans_path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []

    # Process in reverse (newest spans first)
    for line in reversed(lines):
        if not line.strip():
            continue
        if len(results) >= limit:
            break

        try:
            span_record = json.loads(line)
        except json.JSONDecodeError:
            continue

        span_trace_id = span_record.get("context", {}).get("trace_id", "")

        # If filtering by trace_id and this span doesn't match, skip
        if trace_id and span_trace_id != trace_id:
            continue

        events = span_record.get("events", [])
        for evt in reversed(events):  # newest events first within span
            evt_name = evt.get("name", "")
            if not evt_name.startswith("debug."):
                continue

            evt_attrs = evt.get("attributes", {})
            evt_event_type = evt_attrs.get("debug.event_type", "")

            # Apply event_type filter
            if event_type and evt_event_type != event_type:
                continue

            # Reconstruct the legacy debug log format for API compatibility
            event_data_str = evt_attrs.get("debug.event_data", "{}")
            try:
                event_data = json.loads(event_data_str)
            except (json.JSONDecodeError, TypeError):
                event_data = {}

            result_entry = {
                "trace_id": evt_attrs.get("debug.trace_id", span_trace_id),
                "event_type": evt_event_type,
                "conversation_id": evt_attrs.get("debug.conversation_id"),
                "event_data": event_data,
                "timestamp": evt.get("timestamp", span_record.get("start_time")),
            }
            results.append(result_entry)

            if len(results) >= limit:
                break

    return results


async def _read_debug_events_from_spans(
    trace_id: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Async wrapper for reading debug events from spans.jsonl.

    Args:
        trace_id: Filter by trace ID.
        event_type: Filter by event type.
        limit: Maximum number of entries to return.

    Returns:
        List of debug log entries (newest first).
    """
    spans_path = _get_spans_path()
    return await asyncio.to_thread(
        _extract_debug_events_from_spans_file,
        spans_path,
        trace_id,
        event_type,
        limit,
    )
```

**Files affected:**
- `services/agent/src/core/observability/debug_logger.py` (modify)

---

### Step 5: Update `DiagnosticsService` to extract debug events from span events

**Engineer tasks:**

The `DiagnosticsService` in `services/agent/src/core/diagnostics/service.py` reads `spans.jsonl` to build `TraceGroup` objects. The `TraceSpan` model (line 30) has an `attributes` dict but no `events` field.

Add an `events` field to `TraceSpan`:

```python
class TraceSpan(BaseModel):
    trace_id: str
    span_id: str
    parent_id: str | None = None
    name: str
    start_time: datetime | None = None
    duration_ms: float
    status: str
    attributes: dict[str, Any]
    events: list[dict[str, Any]] = []  # NEW: span events (debug events, etc.)
```

Update `_parse_span()` (around line 1215) to include events:

```python
# In _parse_span(), add after attributes extraction:
events=data.get("events", []),
```

This is in the `return TraceSpan(...)` call around line 1232. Add `events=data.get("events", [])` to the constructor.

**Files affected:**
- `services/agent/src/core/diagnostics/service.py` (modify)

---

### Step 6: Update Diagnostic API endpoints to use span events

**Engineer tasks:**

Modify `services/agent/src/interfaces/http/admin_api.py`:

The following endpoints currently import and call `read_debug_logs()` from `debug_logger.py`. Since Step 3 already rewired `read_debug_logs()` to read from spans, **these endpoints should work without changes**. However, verify and test:

1. **`/status` (line 302-306):** Calls `read_debug_logs(event_type="supervisor", limit=20)` -- should work as-is.
2. **`/debug/logs` (line 656-683):** Calls `read_debug_logs(trace_id=..., event_type=..., limit=...)` -- should work as-is.
3. **`/debug/stats` (line 538-615):** Calls `read_debug_logs(limit=10000)` -- should work as-is.
4. **`/investigate/{trace_id}` (line 686-769):** Calls `read_debug_logs(trace_id=..., limit=500)` -- should work as-is. Additionally, the `spans` section of this endpoint now has events embedded, so the summary can also use span events directly.
5. **`/tools/stats` (line 900-972):** Calls `read_debug_logs(event_type="tool_call", limit=10000)` -- should work as-is.
6. **`/skills/stats` (line 975-1063):** Calls `read_debug_logs(event_type="skill_step", limit=10000)` -- should work as-is.

Also update the `SpanDetail` model (line 243) to include events:

```python
class SpanDetail(BaseModel):
    """Detail of a single span within a trace."""

    span_id: str
    parent_id: str | None
    name: str
    start_time: str | None
    duration_ms: float
    status: str
    attributes: dict[str, Any]
    events: list[dict[str, Any]] = []  # NEW
```

And in `get_trace_detail()` (line 861), pass events through:

```python
SpanDetail(
    span_id=span.span_id,
    parent_id=span.parent_id,
    name=span.name,
    start_time=span.start_time.isoformat() if span.start_time else None,
    duration_ms=span.duration_ms,
    status=span.status,
    attributes=span.attributes,
    events=span.events,  # NEW
)
```

Similarly in `investigate_trace()` (line 722-731), add events to span output:

```python
trace_spans = [
    {
        "name": s.name,
        "duration_ms": s.duration_ms,
        "status": s.status,
        "attributes": s.attributes,
        "start_time": s.start_time,
        "events": s.events,  # NEW
    }
    for s in tg.spans
]
```

**Files affected:**
- `services/agent/src/interfaces/http/admin_api.py` (modify)

---

### Step 7: Update `admin_mcp.py` MCP activity endpoint

**Engineer tasks:**

Modify `services/agent/src/interfaces/http/admin_mcp.py` (line 745):

The `get_mcp_activity()` function calls `read_debug_logs(limit=200)` and filters for `mcp_connect`/`mcp_error` event types. Since `read_debug_logs()` is already rewired (Step 3), this should work without changes. Verify by reading the code.

**Files affected:**
- `services/agent/src/interfaces/http/admin_mcp.py` (verify only, likely no changes needed)

---

### Step 8: Update `admin_diagnostics.py` imports

**Engineer tasks:**

Modify `services/agent/src/interfaces/http/admin_diagnostics.py` (line 16):

The import `from core.observability.debug_logger import DebugLogger, read_debug_logs` should still work since we kept both symbols. Check if `DebugLogger` or `read_debug_logs` is used in this file's body. If `read_debug_logs` is used, it already points to the new implementation.

Check `services/agent/src/interfaces/http/app.py` (line 31): The import `from core.observability.debug_logger import configure_debug_log_handler` should still work (it's now a no-op).

**Files affected:**
- `services/agent/src/interfaces/http/admin_diagnostics.py` (verify, likely no changes)
- `services/agent/src/interfaces/http/app.py` (verify, likely no changes -- the call to `configure_debug_log_handler()` becomes a no-op)

---

### Step 9: Clean up dead code

**Engineer tasks:**

In `services/agent/src/core/observability/debug_logger.py`, remove:
- `_read_lines()` helper function (no longer needed)
- `RawJsonFormatter` class inside `configure_debug_log_handler` (already removed via no-op)
- `_debug_log` logger and `DEBUG_LOGS_PATH` constant (no longer needed)
- The `RotatingFileHandler` import (no longer needed)
- The `logging.handlers` import (no longer needed)

Keep:
- `DebugLogger` class (interface unchanged)
- `_sanitize_args()` (still used for event data sanitization)
- `read_debug_logs()` (now delegates to `_read_debug_events_from_spans`)
- `is_enabled()` and cache (still needed for debug toggle)
- `invalidate_debug_cache()` (still used by admin_api.py toggle endpoint)
- `configure_debug_log_handler()` as no-op (backward compat)

**Files affected:**
- `services/agent/src/core/observability/debug_logger.py` (modify)

---

### Step 10: Update tests

**Engineer tasks:**

Modify `services/agent/src/core/observability/tests/test_debug_logger.py`:

The existing tests mock JSONL file I/O. They need to be updated:

1. **`test_log_event_writes_to_file_when_enabled`** -- Change to verify `span.add_event()` is called instead of file write. Mock `trace.get_current_span()` to return a mock span, assert `add_event` was called with correct args.

2. **`test_log_event_is_noop_when_disabled`** -- Keep as-is but verify no `add_event` call.

3. **`test_is_enabled_caches_result`** -- No changes needed.

4. **`test_sanitize_args_*`** -- No changes needed.

5. **`test_read_debug_logs_*`** -- Rewrite to create a mock `spans.jsonl` with embedded events. The test data format changes from flat JSONL to span records with `events` lists.

6. **`test_log_tool_call_sanitizes_args`** -- Update to verify sanitization in span event attributes.

Example updated test for `test_log_event_writes_to_file_when_enabled`:

```python
@pytest.mark.asyncio
async def test_log_event_adds_span_event_when_enabled() -> None:
    """When enabled, log_event should add an OTel span event."""
    session = AsyncMock(spec=AsyncSession)
    mock_execute = AsyncMock()
    mock_result = MagicMock()
    mock_config = MagicMock()
    mock_config.value = "true"
    mock_result.scalar_one_or_none.return_value = mock_config
    mock_execute.return_value = mock_result
    session.execute = mock_execute

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
```

Example updated test for `test_read_debug_logs_filters_by_trace_id`:

```python
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
```

**Files affected:**
- `services/agent/src/core/observability/tests/test_debug_logger.py` (modify)

---

### Step 11: Add `_NoOpSpan.events` property for test compatibility

**Engineer tasks:**

In `services/agent/src/core/observability/tracing.py`, the `_NoOpSpan` class (line 78) already has `add_event()` as a no-op. Add an `events` property so that `_FileSpanExporter.export()` doesn't fail when processing NoOp spans:

```python
class _NoOpSpan:
    """Span placeholder implementing the methods we use."""

    name: str = "noop"
    kind: Any = _OtelSpanKind.INTERNAL
    attributes: dict[str, Any] = {}
    start_time: int | None = None
    end_time: int | None = None
    events: tuple[Any, ...] = ()  # NEW: empty events tuple
```

**Files affected:**
- `services/agent/src/core/observability/tracing.py` (modify)

---

## Configuration Changes

- No new environment variables needed.
- `SPAN_LOG_PATH` (existing) becomes the single source of truth for both trace spans and debug events.
- The `data/debug_logs.jsonl` file will stop receiving new writes. It can be deleted after deployment.

---

## Testing Strategy

### Unit Tests (Step 10)
- `test_log_event_adds_span_event_when_enabled` -- verify span.add_event() called
- `test_log_event_is_noop_when_disabled` -- verify no span.add_event() call
- `test_read_debug_logs_filters_by_trace_id` -- read from mock spans.jsonl
- `test_read_debug_logs_filters_by_event_type` -- read from mock spans.jsonl
- `test_read_debug_logs_respects_limit` -- verify limit enforcement
- `test_sanitize_args_*` -- unchanged
- `test_log_tool_call_sanitizes_args` -- verify via span event attributes

### Integration Testing (Manual)
1. Start dev environment: `./stack dev deploy`
2. Send a test message through the agent
3. Check `data/spans.jsonl` -- verify span records contain `events` with `debug.*` entries
4. Hit `/platformadmin/api/debug/logs` -- verify it returns debug events
5. Hit `/platformadmin/api/investigate/{trace_id}` -- verify debug_logs section populated
6. Hit `/platformadmin/api/tools/stats` -- verify tool stats computed from span events
7. Hit `/platformadmin/api/status` -- verify recent_errors populated

---

## Quality Checks

```bash
./stack check
```

Key areas to watch:
- **Mypy:** The `events` field additions must be typed correctly. `list[dict[str, Any]]` for Pydantic models.
- **Ruff:** No unused imports after cleanup (remove `RotatingFileHandler`, `logging.handlers`).
- **Pytest:** All existing tests in `test_debug_logger.py` must pass with updated mocks.

---

## Security Considerations

1. **Sensitive data in span events:** `_sanitize_args()` is still applied before `add_event()`. Passwords, tokens, secrets are redacted.
2. **Span event size:** OTel event attributes are truncated to 4000 chars (same as current span attribute limit).
3. **No new attack surface:** The Diagnostic API authentication is unchanged.
4. **`_SanitizingSpanProcessor`** in tracing.py sanitizes span attributes but does NOT sanitize events. Since we pre-sanitize in `DebugLogger.log_event()`, this is acceptable. However, consider adding event sanitization to `_SanitizingSpanProcessor` for defense-in-depth (optional, not blocking).

---

## Success Criteria

1. `data/debug_logs.jsonl` is no longer written to (no new entries after deployment)
2. All Diagnostic API endpoints return the same data structure as before
3. `spans.jsonl` contains span records with embedded `events` arrays
4. Root spans have `context_id` and `conversation_id` attributes
5. `./stack check` passes
6. All existing tests pass (with updated mocks)

---

## Agent Delegation

### Engineer (Sonnet) - Implementation
- Steps 1-6, 9, 11: Modify source files
- Step 10: Rewrite tests
- Steps 7-8: Verify no changes needed

### Ops (Haiku) - Quality and Deployment
- Run `./stack check` after each logical group of steps
- Run quality checks after Steps 1-4 (core changes)
- Run quality checks after Steps 5-6 (API changes)
- Run quality checks after Steps 9-11 (cleanup + tests)
- Commit and create PR when all checks pass

### Cost Optimization
Implementation order for efficient work:
1. Engineer: Steps 1, 2, 3, 4, 11 (core observability changes)
2. Ops: Run `./stack check`
3. Engineer: Steps 5, 6 (API layer changes)
4. Ops: Run `./stack check`
5. Engineer: Steps 7, 8, 9, 10 (cleanup, tests, verification)
6. Ops: Run `./stack check`, commit, create PR

---

## Implementation Notes for Engineer

### Import changes summary for debug_logger.py

**Remove these imports:**
```python
from logging.handlers import RotatingFileHandler
```

**Keep these imports:**
```python
import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opentelemetry import trace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import SystemConfig
```

### Key constraint: OTel event attribute types

OTel span event attributes MUST be primitive types or sequences of primitives. You cannot pass nested dicts. That's why `event_data` is JSON-serialized as a string:

```python
attrs["debug.event_data"] = json.dumps(sanitized_data, default=str)[:4000]
```

### The `_FileSpanExporter` receives ReadableSpan objects

The OTel SDK `ReadableSpan` has:
- `events`: tuple of `Event` objects
- Each `Event` has: `name: str`, `attributes: types.Attributes`, `timestamp: int` (nanoseconds)

The `getattr(span, "events", None) or ()` pattern handles both real spans and NoOp spans gracefully.
