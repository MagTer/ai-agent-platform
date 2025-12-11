#!/usr/bin/env python3
"""
Single Source of Truth for running quality checks.
"""
import os
import subprocess
import sys
from pathlib import Path

# ANSI Color Codes
GREEN = "\033[92m"
RED = "\033[91m"
BLUE = "\033[94m"
RESET = "\033[0m"

REPO_ROOT = Path(__file__).resolve().parent.parent
# Services with their own pyproject.toml/tests/types
SERVICES = ["services/agent"]
# Configuration source for linters
PYPROJECT_CONFIG = REPO_ROOT / "services" / "agent" / "pyproject.toml"


def check_venv() -> None:
    """Ensure the script is running inside a virtual environment."""
    if os.environ.get("CI") == "true":
        return

    # Check if we are in a virtual environment
    # sys.prefix != sys.base_prefix covers standard venvs
    # VIRTUAL_ENV covers some other cases
    in_venv = (sys.prefix != sys.base_prefix) or (os.environ.get("VIRTUAL_ENV") is not None)

    if not in_venv:
        print(
            f"{BLUE}ℹ️  Not running in a virtual environment. "
            f"Auto-restarting via Poetry...{RESET}"
        )

        # Main project directory where poetry environment is defined
        poetry_project_dir = REPO_ROOT / "services" / "agent"

        if not poetry_project_dir.exists():
            print(f"{RED}❌ Cannot find poetry project at {poetry_project_dir}{RESET}")
            sys.exit(1)

        # Construct command: poetry -C <dir> run python <script> <args>
        script = Path(__file__).resolve()
        # args[0] for execvp must be the executable name
        cmd = ["poetry", "-C", str(poetry_project_dir), "run", "python", str(script)] + sys.argv[1:]

        try:
            # Replace current process with the poetry command
            os.execvp("poetry", cmd)  # noqa: S606, S607
        except FileNotFoundError:
            print(f"{RED}❌ Error: 'poetry' executable not found in PATH.{RESET}")
            sys.exit(1)


def run_command(command: list[str], cwd: Path) -> None:
    """Run a command in the specified directory."""
    cmd_str = " ".join(command)
    print(f"{BLUE}==> Running: {cmd_str}{RESET}")
    print(f"    (in {cwd})")

    # Add REPO_ROOT to PYTHONPATH so tests can import 'services.x.y'
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{REPO_ROOT}:{env.get('PYTHONPATH', '')}"

    try:
        # Use sys.executable to ensure we use the same interpreter
        subprocess.check_call(command, cwd=cwd, env=env)  # noqa: S603
        print(f"{GREEN}✅ Passed{RESET}\n")
    except subprocess.CalledProcessError as e:
        print(f"{RED}❌ Failed with exit code {e.returncode}{RESET}")
        sys.exit(e.returncode)


def main() -> None:
    check_venv()

    print(f"{BLUE}Starting Quality Checks...{RESET}\n")

    # 1. Global Linters
    print(f"{BLUE}--- Global Linters ---{RESET}")

    # Ruff
    run_command(
        [sys.executable, "-m", "ruff", "check", "--fix", ".", "--config", str(PYPROJECT_CONFIG)],
        cwd=REPO_ROOT,
    )

    # Black
    run_command(
        [
            sys.executable,
            "-m",
            "black",
            ".",
            "--config",
            str(PYPROJECT_CONFIG),
            "--extend-exclude",
            "/(data|services/openwebui/data)",
        ],
        cwd=REPO_ROOT,
    )

    # 2. Service-level Checks
    print(f"{BLUE}--- Service Checks ---{RESET}")

    for service in SERVICES:
        service_path = REPO_ROOT / service
        if not service_path.exists():
            print(f"{RED}⚠️ Service directory not found: {service}{RESET}")
            continue

        print(f"{BLUE}Checking service: {service}{RESET}")

        # Mypy
        run_command([sys.executable, "-m", "mypy", "src"], cwd=service_path)

        # Pytest
        run_command([sys.executable, "-m", "pytest"], cwd=service_path)

    print(f"{GREEN}🎉 All quality checks completed successfully.{RESET}")


if __name__ == "__main__":
    main()
