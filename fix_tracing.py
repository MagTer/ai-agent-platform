
path = "services/agent/src/core/observability/tracing.py"
with open(path, "r") as f:
    content = f.read()

bad_str = ")).StatusCode.UNSET)"
good_str = "))"

if bad_str in content:
    print(f"Found bad string. Replacing...")
    new_content = content.replace(bad_str, good_str)
    with open(path, "w") as f:
        f.write(new_content)
    print("Fixed.")
else:
    print("Bad string not found in strict match.")
    # Try fuzzy or look around line 311
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if "set_status" in line and "UNSET" in line:
            print(f"Potential match on line {i+1}: {line}")
            # Fix it blindly if it looks like the culprit
            if "span.set_status" in line:
                 lines[i] = '            span.set_status(_otel_trace.Status(status_code, description=description))'
                 print("Replaced line.")
    
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
