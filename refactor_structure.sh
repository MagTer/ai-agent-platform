#!/bin/bash
set -e

echo "Starting Refactoring Process..."

# 1. Create new directories
echo "Creating directory structure..."
mkdir -p src/interfaces/http
mkdir -p src/orchestrator
mkdir -p src/core
mkdir -p skills/general
mkdir -p skills/dev

# 2. Move existing agent logic to core
echo "Moving src/agent/* to src/core/..."
# Check if src/agent is not empty
if [ -d "src/agent" ]; then
    # We use rsync to merge if src/core already had content (though it shouldn't), or mv.
    # mv is safer for moving everything including hidden files if glob is set, but simple * misses hidden.
    # using find or just mv *
    mv src/agent/* src/core/ 2>/dev/null || true
else
    echo "Warning: src/agent does not exist, skipping move."
fi

# 3. Create __init__.py files
echo "Creating __init__.py files..."
touch src/interfaces/__init__.py
touch src/interfaces/http/__init__.py
touch src/orchestrator/__init__.py
# src/core/__init__.py should exist if moved from src/agent, but ensure it exists
touch src/core/__init__.py

# 4. Create placeholder skill
echo "Creating example skill..."
cat > skills/general/hello_world.md <<EOF
---
name: "hello-world"
description: "A simple greeting skill"
inputs: []
permission: "read"
---
You are a helpful assistant. Say Hello to the user!
EOF

echo "Refactoring structure complete."
echo "IMPORTANT: You must manually update imports in src/core/ (formerly src/agent/) to reflect the new location."
echo "Example: 'from agent.core.models' -> 'from core.core.models' or similar, depending on your path configuration."
