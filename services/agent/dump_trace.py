import json
import sys
import urllib.request

if len(sys.argv) < 2:
    print("Usage: python dump_trace.py <trace_id>")
    sys.exit(1)

TRACE_ID = sys.argv[1]

try:
    with urllib.request.urlopen("http://localhost:8000/diagnostics/traces?limit=1000") as response:
        data = json.loads(response.read().decode())
        target = next((t for t in data if t.get("trace_id") == TRACE_ID), None)

        if not target:
            print("Trace not found")
            sys.exit(1)

        # Print detailed hierarchy
        # Map span_id -> span
        spans_by_id = {s["span_id"]: s for s in target["spans"]}

        # Find children
        children = {}
        for s in target["spans"]:
            pid = s.get("parent_id")
            if pid not in children:
                children[pid] = []
            children[pid].append(s)

        def print_tree(span_id, depth=0):
            span = spans_by_id.get(span_id)
            if not span:
                return

            prefix = "  " * depth
            name = span.get("name")
            dur = span.get("duration_ms", 0)
            print(f"{prefix}- {name} ({dur}ms)")

            # Print attributes for skills/tools
            attrs = span.get("attributes", {})
            if "skill" in name or "tool" in name or "expert" in str(attrs):
                print(f"{prefix}  ATTRS: {attrs}")

            for child in sorted(children.get(span_id, []), key=lambda x: x.get("start_time")):
                print_tree(child["span_id"], depth + 1)

        # Find root (span with no parent or parent not in this trace)
        # The trace object has 'root_span' but let's use the explicit structure
        root_span = target.get("root_span")
        if root_span:
            print_tree(root_span["span_id"])
        else:
            # Fallback
            for s in target["spans"]:
                if not s.get("parent_id"):
                    print_tree(s["span_id"])

except Exception as e:
    print(f"Error: {e}")
