"""Tests for OpenTelemetry metrics module."""

from __future__ import annotations

from core.observability.metrics import (
    _increment_snapshot,
    _metric_snapshot,
    _NoOpCounter,
    _NoOpHistogram,
    _NoOpUpDownCounter,
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
