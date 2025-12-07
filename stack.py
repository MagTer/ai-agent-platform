#!/usr/bin/env python3
"""
Wrapper script to run the 'stack' CLI from the root directory.
It adds 'services/agent/src' to sys.path so 'python3 stack.py ...' works.
"""
import sys
import subprocess
from pathlib import Path

# Path to the agent service directory
AGENT_DIR = Path(__file__).parent / "services" / "agent"


def main():
    """Run the stack CLI via poetry in the agent service directory."""

    # Check if poetry is installed
    if subprocess.run(["which", "poetry"], capture_output=True).returncode != 0:
        print("Error: 'poetry' not found. Please install poetry first.")
        sys.exit(1)

    # Construct the command
    # We use 'python -m stack' to run the module directly
    # PYTHONPATH=src ensures the 'stack' package is found within 'services/agent/src'
    cmd = ["poetry", "run", "python", "-m", "stack"] + sys.argv[1:]

    # Add environment variable for PYTHONPATH
    env = {"PYTHONPATH": "src"}

    # Run the command
    try:
        # Pass current environment variables as well, so PATH etc. are preserved
        import os

        full_env = os.environ.copy()
        full_env.update(env)

        subprocess.run(cmd, cwd=AGENT_DIR, env=full_env, check=True)
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
