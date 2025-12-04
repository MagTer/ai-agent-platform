#!/usr/bin/env python3
"""
Wrapper script to run the 'stack' CLI from the root directory.
It adds 'services/agent/src' to sys.path so 'python3 stack.py ...' works.
"""
import sys
import os
from pathlib import Path

# Add services/agent/src to the python path
agent_src = Path(__file__).parent / "services" / "agent" / "src"
sys.path.insert(0, str(agent_src))

try:
    from stack.cli import app
except ImportError as e:
    print(f"Error importing stack CLI: {e}")
    print(f"Ensure that {agent_src} exists and contains the stack package.")
    sys.exit(1)

if __name__ == "__main__":
    app()
