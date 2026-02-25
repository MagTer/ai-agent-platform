"""Tests for context-scoped debug logger helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.observability.debug_logger import (
    _count_skill_executions_for_context,
    _extract_supervisor_events_for_context,
)


@pytest.fixture
def spans_file(tmp_path: Path) -> Path:
    """Create a test spans.jsonl file."""
    spans_path = tmp_path / "spans.jsonl"

    span1 = {
        "name": "agent_request",
        "context": {"trace_id": "trace001", "span_id": "span001"},
        "attributes": {"context_id": "ctx-111"},
        "events": [
            {
                "name": "debug.supervisor",
                "timestamp": "2026-02-20T10:00:00",
                "attributes": {
                    "debug.event_type": "supervisor",
                    "debug.event_data": json.dumps(
                        {
                            "outcome": "REPLAN",
                            "reason": "Tool returned empty",
                            "step_label": "researcher step",
                        }
                    ),
                    "debug.conversation_id": "conv-001",
                },
            },
            {
                "name": "debug.skill_step",
                "timestamp": "2026-02-20T10:00:00",
                "attributes": {
                    "debug.event_type": "skill_step",
                    "debug.event_data": json.dumps(
                        {
                            "skill_name": "researcher",
                            "outcome": "REPLAN",
                        }
                    ),
                },
            },
        ],
    }

    span2 = {
        "name": "agent_request",
        "context": {"trace_id": "trace002", "span_id": "span002"},
        "attributes": {"context_id": "ctx-222"},  # Different context
        "events": [
            {
                "name": "debug.supervisor",
                "timestamp": "2026-02-20T11:00:00",
                "attributes": {
                    "debug.event_type": "supervisor",
                    "debug.event_data": json.dumps(
                        {
                            "outcome": "ABORT",
                            "reason": "Auth failure",
                            "step_label": "backlog step",
                        }
                    ),
                },
            },
        ],
    }

    span3 = {
        "name": "agent_request",
        "context": {"trace_id": "trace003", "span_id": "span003"},
        "attributes": {"context_id": "ctx-111"},
        "events": [
            {
                "name": "debug.skill_step",
                "timestamp": "2026-02-20T12:00:00",
                "attributes": {
                    "debug.event_type": "skill_step",
                    "debug.event_data": json.dumps(
                        {
                            "skill_name": "researcher",
                            "outcome": "SUCCESS",
                        }
                    ),
                },
            },
        ],
    }

    with spans_path.open("w") as f:
        for span in [span1, span2, span3]:
            f.write(json.dumps(span) + "\n")

    return spans_path


def test_extract_supervisor_events_filters_by_context(spans_file: Path) -> None:
    """Supervisor events are filtered by context_id."""
    events = _extract_supervisor_events_for_context(spans_file, "ctx-111", "2026-02-01T00:00:00")
    assert len(events) == 1
    assert events[0]["outcome"] == "REPLAN"
    assert events[0]["trace_id"] == "trace001"


def test_extract_supervisor_events_excludes_other_context(spans_file: Path) -> None:
    """Events from other contexts are excluded."""
    events = _extract_supervisor_events_for_context(spans_file, "ctx-222", "2026-02-01T00:00:00")
    assert len(events) == 1
    assert events[0]["outcome"] == "ABORT"


def test_extract_supervisor_events_respects_time_filter(spans_file: Path) -> None:
    """Events before the cutoff are excluded."""
    events = _extract_supervisor_events_for_context(spans_file, "ctx-111", "2026-02-21T00:00:00")
    assert len(events) == 0


def test_count_skill_executions_for_context(spans_file: Path) -> None:
    """Skill executions are counted per skill per outcome."""
    counts = _count_skill_executions_for_context(spans_file, "ctx-111", "2026-02-01T00:00:00")
    assert "researcher" in counts
    assert counts["researcher"]["total"] == 2
    assert counts["researcher"]["REPLAN"] == 1
    assert counts["researcher"]["SUCCESS"] == 1


def test_count_skill_executions_empty_context(spans_file: Path) -> None:
    """Unknown context returns empty counts."""
    counts = _count_skill_executions_for_context(spans_file, "ctx-999", "2026-02-01T00:00:00")
    assert counts == {}
