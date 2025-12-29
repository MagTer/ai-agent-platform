
path = "services/agent/src/core/core/service.py"
with open(path, "r") as f:
    lines = f.readlines()

new_lines = []
indent_mode = False
try_line_idx = -1

for i, line in enumerate(lines):
    # Find the try line
    if "attributes={" in lines[i-5:i] and 'root_span' in lines[i-1] and 'try:' in line:
         try_line_idx = i
         new_lines.append(line)
         indent_mode = True
         print(f"Found try: at line {i+1}")
         continue
    
    # Detect where indentation should stop (the except block)
    if indent_mode and "except Exception as e:" in line:
        indent_mode = False
        print(f"Found except: at line {i+1}")
        new_lines.append(line)
        continue

    if indent_mode:
        # Check if it's already indented (unlikely given the tool output)
        if line.strip() == "":
            new_lines.append(line)
        else:
            new_lines.append("    " + line)
    else:
        new_lines.append(line)

with open(path, "w") as f:
    f.writelines(new_lines)
print("Done.")
