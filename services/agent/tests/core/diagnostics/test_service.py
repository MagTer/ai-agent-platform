
import json
import tempfile
from pathlib import Path

from core.core.config import Settings
from core.diagnostics.service import DiagnosticsService


def test_get_system_health_metrics():
    # Create a temporary trace log
    with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8") as tmp:
        # Trace 1: OK
        tmp.write(json.dumps({
            "name": "tool.call.web_search",
            "context": {"trace_id": "t1"},
            "status": "OK",
            "attributes": {}
        }) + "\n")
        
        # Trace 2: ERROR in tool
        tmp.write(json.dumps({
            "name": "tool.call.bad_tool",
            "context": {"trace_id": "t2"},
            "status": "ERROR",
            "attributes": {"error": "Timeout"}
        }) + "\n")
        
        # Trace 3: OK
        tmp.write(json.dumps({
            "name": "llm.call",
            "context": {"trace_id": "t3"},
            "status": "OK", 
            "attributes": {}
        }) + "\n")
        
        # Trace 2: Another span (should not double count request but count failure)
        tmp.write(json.dumps({
            "name": "executor.step_run",
            "context": {"trace_id": "t2"},
            "status": "OK", # mixed status in trace
            "attributes": {}
        }) + "\n")

    try:
        settings = Settings(trace_span_log_path=tmp.name)
        svc = DiagnosticsService(settings)
        
        metrics = svc.get_system_health_metrics(window=10)
        
        print(f"Metrics: {metrics}")
        
        assert metrics["status"] == "DEGRADED" # 1 error out of 3 requests = 33% > 10%
        assert metrics["metrics"]["total_requests"] == 3
        assert metrics["metrics"]["error_count"] == 1
        assert metrics["metrics"]["error_rate"] == 0.33
        assert "tool.call.bad_tool" in metrics["hotspots"]
        assert metrics["hotspots"]["tool.call.bad_tool"] == 1
        
        # Verify Insights
        assert "insights" in metrics
        hotspots_list = metrics["insights"]["hotspots"]
        assert len(hotspots_list) > 0
        hotspot = next(h for h in hotspots_list if h["name"] == "tool.call.bad_tool")
        assert hotspot["count"] == 1
        assert len(hotspot["top_reasons"]) > 0
        assert "Timeout (1)" in hotspot["top_reasons"] or "Timeout" in hotspot["top_reasons"][0]

    finally:
        Path(tmp.name).unlink()
