import json
import sys
import urllib.request

TRACE_ID = sys.argv[1] if len(sys.argv) > 1 else "88de3807f95030b31ceb6c0a31d23457"

try:
    print(f"Fetching traces to find {TRACE_ID}...")
    with urllib.request.urlopen("http://localhost:8000/diagnostics/traces?limit=1000") as response:
        data = json.loads(response.read().decode())

        target = next((t for t in data if t.get("trace_id") == TRACE_ID), None)

        if not target:
            print(f"Trace {TRACE_ID} not found in recent traces.")
            sys.exit(1)

        print(f"Trace Found: {target.get('trace_id')}")
        print(f"Total Duration: {target.get('total_duration_ms')} ms")
        print(f"Status: {target.get('status')}")

        root = target.get("root_span", {})
        print(f"Root Span: {root.get('name')} | Attributes: {root.get('attributes')}")

        spans = target.get("spans", [])
        print(f"\nSpans ({len(spans)}):")

        # Sort by start time
        spans.sort(key=lambda x: x.get("start_time"))

        for s in spans:
            name = s.get("name")
            dur = s.get("duration_ms")
            # Highlight long spans
            mark = "!!!" if dur > 1000 else ""
            print(f" - [{dur:6.0f}ms] {name} {mark}")
            # Print attributes for interesting spans
            if "dispatcher" in name.lower() or "llm" in name.lower():
                print(f"     Attrs: {s.get('attributes')}")

except Exception as e:
    print(f"Error: {e}")
