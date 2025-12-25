import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.core.config import Settings
from core.diagnostics.service import DiagnosticsService


@pytest.fixture
def temp_span_file(tmp_path: Path):
    return tmp_path / "spans.jsonl"


@pytest.fixture
def diagnostics_service(temp_span_file: Path):
    settings = Settings(trace_span_log_path=str(temp_span_file))
    return DiagnosticsService(settings)


def test_get_recent_traces_empty(diagnostics_service):
    traces = diagnostics_service.get_recent_traces()
    assert traces == []


def test_get_recent_traces_parsing(diagnostics_service, temp_span_file):
    # Write some dummy spans
    span1 = {
        "name": "test_span",
        "context": {"trace_id": "t1", "span_id": "s1", "parent_id": None},
        "status": "OK",
        "start_time": datetime.utcnow().isoformat(),
        "duration_ms": 100.5,
        "attributes": {"foo": "bar"},
    }
    span2 = {
        "name": "error_span",
        "context": {"trace_id": "t2", "span_id": "s2", "parent_id": "s1"},
        "status": "ERROR",
        "start_time": (datetime.utcnow() + timedelta(seconds=1)).isoformat(),
        "duration_ms": 50.0,
        "attributes": {},
    }

    with open(temp_span_file, "w") as f:
        f.write(json.dumps(span1) + "\n")
        f.write(json.dumps(span2) + "\n")

    traces = diagnostics_service.get_recent_traces(limit=10)
    assert len(traces) == 2

    # Check reversed order (newest first)
    assert traces[0].span_id == "s2"
    assert traces[0].status == "ERROR"
    assert traces[0].parent_id == "s1"

    assert traces[1].span_id == "s1"
    assert traces[1].status == "OK"
    assert traces[1].attributes["foo"] == "bar"


def test_get_recent_traces_limit(diagnostics_service, temp_span_file):
    # Write 10 spans
    with open(temp_span_file, "w") as f:
        for i in range(10):
            span = {
                "name": f"span_{i}",
                "context": {"trace_id": f"t{i}", "span_id": f"s{i}"},
                "status": "OK",
                "start_time": datetime.utcnow().isoformat(),
                "duration_ms": 10.0,
                "attributes": {},
            }
            f.write(json.dumps(span) + "\n")

    # Fetch limit 3
    traces = diagnostics_service.get_recent_traces(limit=3)
    assert len(traces) == 3
    # Newest first, so 9, 8, 7
    assert traces[0].name == "span_9"
    assert traces[2].name == "span_7"


def test_malformed_json_resilience(diagnostics_service, temp_span_file):
    with open(temp_span_file, "w") as f:
        f.write('{"name": "valid"}\n')
        f.write("BROKEN_JSON\n")
        f.write('{"name": "valid_2"}\n')

    traces = diagnostics_service.get_recent_traces()
    assert len(traces) == 2
    assert traces[0].name == "valid_2"
    assert traces[1].name == "valid"
