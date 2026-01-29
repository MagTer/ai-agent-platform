"""Quality check functions for the AI Agent Platform.

This module provides modular quality check functions that can be composed
into different workflows for QA agents, Engineers, and CI pipelines.

Functions:
    run_ruff: Run Ruff linter with optional auto-fix
    run_black: Run Black formatter with optional auto-fix
    run_mypy: Run Mypy type checker
    run_pytest: Run pytest test suite
    run_semantic_tests: Run end-to-end semantic tests (local only)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# --- Configuration ---

# Repository root: stack/checks.py -> src -> agent -> services -> repo
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
AGENT_SERVICE_DIR = REPO_ROOT / "services" / "agent"
PYPROJECT_CONFIG = AGENT_SERVICE_DIR / "pyproject.toml"


@dataclass
class CheckResult:
    """Result of a quality check."""

    success: bool
    name: str
    message: str = ""


# --- Styling ---

GREEN = "\033[92m"
RED = "\033[91m"
BLUE = "\033[94m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"


def _print_step(msg: str) -> None:
    print(f"\n{BLUE}{BOLD}==> {msg}{RESET}")


def _print_success(msg: str) -> None:
    print(f"{GREEN}[OK] {msg}{RESET}")


def _print_error(msg: str) -> None:
    print(f"{RED}[FAIL] {msg}{RESET}")


def _print_info(msg: str) -> None:
    print(f"{YELLOW}[INFO] {msg}{RESET}")


def _run_cmd(
    args: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run a subprocess command.

    Args:
        args: Command arguments list.
        cwd: Directory to execute the command in.
        env: Optional environment variables.

    Returns:
        CompletedProcess with return code and output.
    """
    # Use the current interpreter if 'python' is requested
    final_args = list(args)
    if final_args[0] == "python":
        final_args[0] = sys.executable

    cmd_str = " ".join(final_args)
    print(f"{BLUE}$ {cmd_str}{RESET}")
    print(f"  (cwd: {cwd})")

    # Inject REPO_ROOT into PYTHONPATH
    run_env = (env or os.environ).copy()
    current_pythonpath = run_env.get("PYTHONPATH", "")
    run_env["PYTHONPATH"] = f"{REPO_ROOT}:{current_pythonpath}"

    return subprocess.run(
        final_args,
        cwd=cwd,
        env=run_env,
        capture_output=False,
    )


def run_ruff(*, fix: bool = True, repo_root: Path | None = None) -> CheckResult:
    """Run Ruff linter.

    Args:
        fix: If True, auto-fix issues. If False, check only.
        repo_root: Repository root path. Defaults to auto-detected.

    Returns:
        CheckResult indicating success or failure.
    """
    root = repo_root or REPO_ROOT
    config = root / "services" / "agent" / "pyproject.toml"

    _print_step("Running Ruff")

    ruff_cmd = ["python", "-m", "ruff", "check", ".", "--config", str(config)]
    if fix:
        ruff_cmd.insert(4, "--fix")
        _print_info("Auto-fix enabled")
    else:
        _print_info("Check only (CI mode)")

    result = _run_cmd(ruff_cmd, cwd=root)

    if result.returncode == 0:
        _print_success("Ruff passed")
        return CheckResult(success=True, name="ruff")
    else:
        _print_error("Ruff failed")
        return CheckResult(success=False, name="ruff", message="Linting errors found")


def run_black(*, fix: bool = True, repo_root: Path | None = None) -> CheckResult:
    """Run Black formatter.

    Args:
        fix: If True, format files. If False, check only.
        repo_root: Repository root path. Defaults to auto-detected.

    Returns:
        CheckResult indicating success or failure.
    """
    root = repo_root or REPO_ROOT
    config = root / "services" / "agent" / "pyproject.toml"

    _print_step("Running Black")

    black_cmd = [
        "python",
        "-m",
        "black",
        ".",
        "--config",
        str(config),
        "--extend-exclude",
        "/(data|services/openwebui/data)|.*\\.venv",
    ]

    if fix:
        _print_info("Auto-formatting enabled")
    else:
        black_cmd.append("--check")
        _print_info("Check only (CI mode)")

    result = _run_cmd(black_cmd, cwd=root)

    if result.returncode == 0:
        _print_success("Black passed")
        return CheckResult(success=True, name="black")
    else:
        _print_error("Black failed")
        return CheckResult(success=False, name="black", message="Formatting issues found")


def run_mypy(*, repo_root: Path | None = None) -> CheckResult:
    """Run Mypy type checker.

    Args:
        repo_root: Repository root path. Defaults to auto-detected.

    Returns:
        CheckResult indicating success or failure.
    """
    root = repo_root or REPO_ROOT
    service_dir = root / "services" / "agent"

    _print_step("Running Mypy")

    result = _run_cmd(["python", "-m", "mypy"], cwd=service_dir)

    if result.returncode == 0:
        _print_success("Mypy passed")
        return CheckResult(success=True, name="mypy")
    else:
        _print_error("Mypy failed")
        return CheckResult(success=False, name="mypy", message="Type errors found")


def run_pytest(*, repo_root: Path | None = None) -> CheckResult:
    """Run pytest test suite.

    Args:
        repo_root: Repository root path. Defaults to auto-detected.

    Returns:
        CheckResult indicating success or failure.
    """
    root = repo_root or REPO_ROOT
    service_dir = root / "services" / "agent"

    _print_step("Running Pytest")

    result = _run_cmd(["python", "-m", "pytest"], cwd=service_dir)

    if result.returncode == 0:
        _print_success("Pytest passed")
        return CheckResult(success=True, name="pytest")
    else:
        _print_error("Pytest failed")
        return CheckResult(success=False, name="pytest", message="Test failures")


def run_semantic_tests(
    *,
    repo_root: Path | None = None,
    category: str | None = None,
) -> CheckResult:
    """Run semantic end-to-end tests.

    These tests require a running agent and make real LLM calls.
    They are skipped if the agent is not available.

    Args:
        repo_root: Repository root path. Defaults to auto-detected.
        category: Optional category filter (routing, skills, tools, planning, error, regression).

    Returns:
        CheckResult indicating success or failure.
    """
    root = repo_root or REPO_ROOT
    service_dir = root / "services" / "agent"
    semantic_test_dir = service_dir / "tests" / "semantic"

    if not semantic_test_dir.exists():
        _print_info("No semantic tests found")
        return CheckResult(success=True, name="semantic", message="Skipped - no tests")

    _print_step("Running Semantic Tests (Golden Queries)")
    _print_info("These tests require a running agent and make real LLM calls")

    # Use the golden query runner instead of pytest
    cmd = ["python", "scripts/run_semantic_eval.py"]
    if category:
        cmd.extend(["--category", category])
        _print_info(f"Category filter: {category}")

    result = _run_cmd(cmd, cwd=service_dir)

    if result.returncode == 0:
        _print_success("Semantic tests passed")
        return CheckResult(success=True, name="semantic")
    else:
        _print_error("Semantic tests failed")
        return CheckResult(success=False, name="semantic", message="Golden query test failures")


def run_lint(*, fix: bool = True, repo_root: Path | None = None) -> list[CheckResult]:
    """Run linting checks (Ruff + Black).

    This is the recommended check for QA agents - fast and auto-fixable.

    Args:
        fix: If True, auto-fix issues. If False, check only.
        repo_root: Repository root path. Defaults to auto-detected.

    Returns:
        List of CheckResults for each tool.
    """
    results = []
    results.append(run_ruff(fix=fix, repo_root=repo_root))
    if results[-1].success:
        results.append(run_black(fix=fix, repo_root=repo_root))
    return results


def run_all_checks(
    *,
    fix: bool = True,
    include_semantic: bool = False,
    semantic_category: str | None = None,
    repo_root: Path | None = None,
) -> list[CheckResult]:
    """Run all quality checks in sequence.

    Stops at first failure to save time.

    Args:
        fix: If True, auto-fix linting issues. If False, check only.
        include_semantic: If True, include semantic e2e tests.
        semantic_category: Optional category filter for semantic tests.
        repo_root: Repository root path. Defaults to auto-detected.

    Returns:
        List of CheckResults for each tool run.
    """
    root = repo_root or REPO_ROOT

    print(f"\n{BOLD}Starting Quality Checks...{RESET}")
    print(f"   Root: {root}")
    print(f"   Mode: {'Check only' if not fix else 'Auto-fix enabled'}")

    results: list[CheckResult] = []

    # Linting (Ruff + Black)
    ruff_result = run_ruff(fix=fix, repo_root=root)
    results.append(ruff_result)
    if not ruff_result.success:
        return results

    black_result = run_black(fix=fix, repo_root=root)
    results.append(black_result)
    if not black_result.success:
        return results

    # Type checking
    mypy_result = run_mypy(repo_root=root)
    results.append(mypy_result)
    if not mypy_result.success:
        return results

    # Tests
    pytest_result = run_pytest(repo_root=root)
    results.append(pytest_result)
    if not pytest_result.success:
        return results

    # Semantic tests (optional, local only)
    if include_semantic:
        semantic_result = run_semantic_tests(repo_root=root, category=semantic_category)
        results.append(semantic_result)

    return results


def ensure_in_virtualenv() -> bool:
    """Check if running inside a virtual environment.

    Returns:
        True if in a virtualenv, False otherwise.
    """
    return sys.prefix != sys.base_prefix or os.environ.get("VIRTUAL_ENV") is not None


def ensure_dependencies() -> None:
    """Ensure dev dependencies are installed."""
    # Check if ruff is available as a proxy for dev dependencies
    if shutil.which("ruff"):
        return

    try:
        subprocess.run(
            [sys.executable, "-m", "ruff", "--version"],
            capture_output=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        _print_info("Dependencies missing. Running 'poetry install'...")
        poetry_bin = shutil.which("poetry")
        if not poetry_bin:
            _print_error("Poetry not found. Cannot install dependencies.")
            sys.exit(1)

        subprocess.run([poetry_bin, "install"], cwd=AGENT_SERVICE_DIR, check=True)
        _print_success("Dependencies installed.")


__all__ = [
    "CheckResult",
    "run_ruff",
    "run_black",
    "run_mypy",
    "run_pytest",
    "run_semantic_tests",
    "run_lint",
    "run_all_checks",
    "ensure_in_virtualenv",
    "ensure_dependencies",
    "REPO_ROOT",
    "AGENT_SERVICE_DIR",
]
