#!/usr/bin/env python3
"""
Quality Assurance Script for AI Agent Platform.

This script serves as the single source of truth for running quality checks (linting, formatting,
type checking, testing) both locally and in CI environments.

Features:
- Auto-detection of CI environment (disables fixes, enables strict checks).
- Auto-restart via Poetry if not running in a virtual environment.
- Global checks (Ruff, Black) run on the repository root.
- Service-specific checks (Mypy, Pytest) run in their respective service directories.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

# --- Configuration ---
REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_SERVICE_DIR = REPO_ROOT / "services" / "agent"

# The central configuration for tools (Ruff, Black, Mypy, Pytest) is in the agent service
PYPROJECT_CONFIG = AGENT_SERVICE_DIR / "pyproject.toml"

# List of service directories to run service-level checks on
SERVICE_DIRS = [
    AGENT_SERVICE_DIR,
]

# --- Styling ---
GREEN = "\033[92m"
RED = "\033[91m"
BLUE = "\033[94m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"


def print_step(msg: str) -> None:
    print(f"\n{BLUE}{BOLD}==> {msg}{RESET}")


def print_success(msg: str) -> None:
    print(f"{GREEN}✅ {msg}{RESET}")


def print_error(msg: str) -> None:
    print(f"{RED}❌ {msg}{RESET}")


def print_info(msg: str) -> None:
    print(f"{YELLOW}ℹ️  {msg}{RESET}")


# --- Environment & Context ---


def is_ci() -> bool:
    """Detect if running in a CI environment."""
    return os.environ.get("CI", "").lower() in ("true", "1", "yes")


def ensure_virtual_environment() -> None:
    """
    Ensure the script is running inside a virtual environment.
    If not, attempt to restart the script using 'poetry run'.
    """
    # Check if we are in a virtual environment
    # sys.prefix != sys.base_prefix covers standard venvs
    # VIRTUAL_ENV covers some other cases (like poetry shell)
    in_venv = (sys.prefix != sys.base_prefix) or (os.environ.get("VIRTUAL_ENV") is not None)

    if is_ci():
        print_info("CI Environment detected. Skipping virtual environment check.")
        return

    if in_venv:
        return

    print_info("Not running in a virtual environment. Attempting to auto-restart via Poetry...")

    # Validate that the poetry project exists where we expect it
    if not AGENT_SERVICE_DIR.exists():
        print_error(f"Cannot find poetry project at {AGENT_SERVICE_DIR}")
        print_error("Please ensure you are running this script from the repository.")
        sys.exit(1)

    # Check if poetry is available
    poetry_bin = shutil.which("poetry")
    if not poetry_bin:
        print_error("Poetry executable not found in PATH.")
        print_error("Please install Poetry or activate a virtual environment manually.")
        sys.exit(1)

    # Construct the command to restart self
    # poetry -C <dir> run python <script> <args>
    script_path = Path(__file__).resolve()
    cmd = [
        poetry_bin,
        "-C",
        str(AGENT_SERVICE_DIR),
        "run",
        "python",
        str(script_path),
    ] + sys.argv[1:]

    print_info(f"Restarting: {' '.join(cmd)}")
    try:
        # execvp replaces the current process
        os.execvp(poetry_bin, cmd)  # noqa: S606
    except OSError as e:
        print_error(f"Failed to restart via Poetry: {e}")
        sys.exit(1)


def run_cmd(args: list[str], cwd: Path) -> None:
    """
    Run a subprocess command in a specific directory.

    Args:
        args: Command arguments list.
        cwd: Directory to execute the command in.
    """
    # Use the current interpreter executable if 'python' is requested
    # This ensures we stick to the active virtual environment
    final_args = list(args)
    if final_args[0] == "python":
        final_args[0] = sys.executable

    cmd_str = " ".join(final_args)
    print(f"{BLUE}$ {cmd_str}{RESET}")
    print(f"  (cwd: {cwd})")

    # Inject REPO_ROOT into PYTHONPATH.
    # This is critical for tests and type checking to resolve imports like 'services.agent...'
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{REPO_ROOT}:{current_pythonpath}"

    try:
        subprocess.run(final_args, cwd=cwd, env=env, check=True)  # noqa: S603
    except subprocess.CalledProcessError as e:
        print_error(f"Command failed with exit code {e.returncode}")
        sys.exit(e.returncode)


# --- Check Implementations ---


def run_global_checks() -> None:
    """Run formatters and linters on the entire repository."""
    print_step("Running Global Linters")

    ci_mode = is_ci()

    # 1. Ruff
    # Runs on REPO_ROOT but uses config from services/agent/pyproject.toml
    ruff_cmd = ["python", "-m", "ruff", "check", ".", "--config", str(PYPROJECT_CONFIG)]
    if not ci_mode:
        ruff_cmd.insert(4, "--fix")
        print_info("Ruff: Auto-fix enabled")
    else:
        print_info("Ruff: CI Mode (Check only)")

    run_cmd(ruff_cmd, cwd=REPO_ROOT)

    # 2. Black
    black_cmd = [
        "python",
        "-m",
        "black",
        ".",
        "--config",
        str(PYPROJECT_CONFIG),
        # Explicitly exclude data directories that might not be in .gitignore or black config
        "--extend-exclude",
        "/(data|services/openwebui/data)|.*\\.venv",
    ]
    if ci_mode:
        black_cmd.append("--check")
        print_info("Black: CI Mode (Check only)")
    else:
        print_info("Black: Auto-formatting enabled")

    run_cmd(black_cmd, cwd=REPO_ROOT)


def run_service_checks() -> None:
    """Run type checking and tests for each service."""
    print_step("Running Service-Level Checks")

    for service_dir in SERVICE_DIRS:
        if not service_dir.exists():
            print_error(f"Service directory not found: {service_dir}")
            continue

        service_name = service_dir.relative_to(REPO_ROOT)
        print(f"\n{BOLD}👉 Service: {service_name}{RESET}")

        # 1. Mypy
        # Runs inside the service directory.
        # It relies on pyproject.toml in that directory for configuration.
        run_cmd(["python", "-m", "mypy"], cwd=service_dir)

        # 2. Pytest
        # Runs inside the service directory.
        run_cmd(["python", "-m", "pytest"], cwd=service_dir)


def main() -> None:
    ensure_virtual_environment()

    print(f"\n{BOLD}🚀 Starting Quality Assurance Checks...{RESET}")
    print(f"   Root: {REPO_ROOT}")
    print(f"   Mode: {'CI' if is_ci() else 'Local'}")

    run_global_checks()
    run_service_checks()

    print_success("All quality checks completed successfully.")


if __name__ == "__main__":
    main()
