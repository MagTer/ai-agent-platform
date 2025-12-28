#!/usr/bin/env python3
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def get_project_root() -> Path:
    """Return the project root directory."""
    return Path(__file__).resolve().parent.parent


def wait_for_service(url: str, timeout: int = 30) -> bool:
    """Poll the service until it becomes available or timeout expires."""
    start_time = time.time()
    print(f"⏳ Waiting for service at {url}...")
    while time.time() - start_time < timeout:
        try:
            with urllib.request.urlopen(url) as response:  # noqa: S310
                if response.status == 200:
                    print("✅ Service is up!")
                    return True
        except (urllib.error.URLError, ConnectionRefusedError):
            pass
        time.sleep(1)
    print("❌ Service failed to start within timeout.")
    return False


def main() -> None:
    root_dir = get_project_root()
    agent_dir = root_dir / "services" / "agent"

    # Ensure dependencies are available (simplified check)
    if not (agent_dir / "src").exists():
        print(f"❌ Could not find agent source at {agent_dir}")
        sys.exit(1)

    print("🚀 Starting Agent Service for Integration Tests...")

    # Set PYTHONPATH to include the agent implementation
    env = dict(os.environ.copy())
    existing_pythonpath = env.get("PYTHONPATH", "")
    # Add root, agent/src, and agent/src/core to PYTHONPATH to match local dev
    agent_src = agent_dir / "src"

    env["PYTHONPATH"] = f"{root_dir}:{agent_src}:{existing_pythonpath}"
    # Force development mode or specific config if needed
    env["AGENT_ENVIRONMENT"] = "test"

    # Start the service in a subprocess
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "core.core.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        cwd=str(agent_src),  # Run from src so imports work relative to it if needed
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        # Wait for health check
        if not wait_for_service("http://127.0.0.1:8000/healthz"):
            print("❌ Start up failed. Checking logs...")
            # Capture startup output
            stdout, stderr = process.communicate(timeout=5)
            print("STDOUT:", stdout.decode())
            print("STDERR:", stderr.decode())
            sys.exit(1)

        print("\n🧪 Running Integration Tests...")
        # Run pytest
        test_cmd = [
            sys.executable,
            "-m",
            "pytest",
            "services/agent/tests/integration/",
            "-v",
        ]

        # Verify test directory exists
        test_dir = root_dir / "services/agent/tests/integration"
        if not test_dir.exists():
            print(f"❌ Test directory not found: {test_dir}")
            sys.exit(1)

        result = subprocess.run(test_cmd, cwd=str(root_dir), env=env)  # noqa: S603

        # Exit with test status
        sys.exit(result.returncode)

    except KeyboardInterrupt:
        print("\n🛑 Interrupted by user.")
    finally:
        print("\n🧹 Tearing down Agent Service...")
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        print("✅ Service stopped.")


if __name__ == "__main__":
    main()
