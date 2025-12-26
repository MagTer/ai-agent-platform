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


@pytest.mark.asyncio
async def test_run_diagnostics(diagnostics_service):
    import httpx
    import respx

    # Mock settings URLs
    settings = diagnostics_service._settings
    settings.litellm_api_base = "http://litellm:4000"
    settings.qdrant_url = "http://qdrant:6333"
    settings.embedder_url = "http://embedder:8082"

    with respx.mock(base_url=None) as router:
        # Mock successful responses
        router.get("http://ollama:11434/api/tags").mock(return_value=httpx.Response(200))
        router.get("http://qdrant:6333/collections").mock(return_value=httpx.Response(200))
        router.get("http://litellm:4000/health/liveness").mock(return_value=httpx.Response(200))
        router.get("http://embedder:8082/health").mock(
            return_value=httpx.Response(500)
        )  # Simulate failure

        # Mock OpenWebUI probe (Self)
        router.get("http://127.0.0.1:8000/v1/models").mock(
            return_value=httpx.Response(200, json={"object": "list", "data": [{"id": "model-1"}]})
        )

        results = await diagnostics_service.run_diagnostics()

    assert len(results) == 5

    # Check Ollama (OK)
    ollama = next(r for r in results if r.component == "Ollama")
    assert ollama.status == "ok"
    assert ollama.latency_ms >= 0

    # Check Embedder (Fail)
    embedder = next(r for r in results if r.component == "Embedder")
    assert embedder.status == "fail"
    assert "500" in (embedder.message or "")
