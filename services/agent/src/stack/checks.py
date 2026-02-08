"""Quality check functions for the AI Agent Platform.

This module provides modular quality check functions that can be composed
into different workflows for QA agents, Engineers, and CI pipelines.

Functions:
    run_architecture: Run architecture validator
    run_ruff: Run Ruff linter with optional auto-fix
    run_black: Run Black formatter with optional auto-fix
    run_mypy: Run Mypy type checker
    run_pytest: Run pytest test suite
    run_semantic_tests: Run end-to-end semantic tests (local only)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from core.validators.architecture import validate_architecture

# --- Configuration ---

# Repository root: stack/checks.py -> src -> agent -> services -> repo
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
AGENT_SERVICE_DIR = REPO_ROOT / "services" / "agent"
PYPROJECT_CONFIG = AGENT_SERVICE_DIR / "pyproject.toml"
ARCHITECTURE_BASELINE = AGENT_SERVICE_DIR / ".architecture-baseline.json"


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


def _load_baseline(baseline_path: Path) -> set[str]:
    """Load architecture violation baseline from JSON file.

    Args:
        baseline_path: Path to baseline JSON file.

    Returns:
        Set of known violation strings.
    """
    if not baseline_path.exists():
        return set()

    try:
        with baseline_path.open("r", encoding="utf-8") as f:
            baseline_list = json.load(f)
            return set(baseline_list)
    except (json.JSONDecodeError, OSError) as e:
        _print_info(f"Failed to load baseline: {e}")
        return set()


def _save_baseline(baseline_path: Path, violations: list[str]) -> None:
    """Save architecture violations to baseline JSON file.

    Args:
        baseline_path: Path to baseline JSON file.
        violations: List of violation strings to save.
    """
    # Sort for deterministic output
    sorted_violations = sorted(violations)

    try:
        with baseline_path.open("w", encoding="utf-8") as f:
            json.dump(sorted_violations, f, indent=2, ensure_ascii=False)
            f.write("\n")  # Add trailing newline
        _print_success(f"Baseline saved to {baseline_path}")
    except OSError as e:
        _print_error(f"Failed to save baseline: {e}")


def _normalize_violation(violation: str) -> str:
    """Normalize violation string for comparison.

    Extracts the file path and imported module to create a stable identifier.
    Line numbers are ignored to handle code changes.

    Args:
        violation: Full violation string from validator.

    Returns:
        Normalized violation identifier (e.g., "interfaces/http/admin.py modules.rag")
    """
    # Parse first line: "path/to/file.py:123 - description"
    first_line = violation.split("\n")[0]
    if " - " in first_line:
        location = first_line.split(" - ")[0].strip()
    else:
        location = first_line.strip()

    # Remove line number from location (keep only file path)
    if ":" in location:
        file_path = location.split(":")[0]
    else:
        file_path = location

    # Extract import statement from "  Import: module.name" line
    import_line = ""
    for line in violation.split("\n"):
        if line.strip().startswith("Import:"):
            import_line = line.split("Import:")[1].strip()
            break

    # Return normalized key: "file import"
    if import_line:
        return f"{file_path} {import_line}"
    return file_path


def run_architecture(
    *, repo_root: Path | None = None, update_baseline: bool = False
) -> CheckResult:
    """Run architecture validator with baseline support.

    Args:
        repo_root: Repository root path. Defaults to auto-detected.
        update_baseline: If True, update baseline with current violations.

    Returns:
        CheckResult indicating success or failure.
    """
    root = repo_root or REPO_ROOT
    src_dir = root / "services" / "agent" / "src"
    baseline_path = ARCHITECTURE_BASELINE

    _print_step("Running Architecture Validator")

    passed, violations = validate_architecture(src_dir)

    if passed:
        _print_success("Architecture validation passed - no violations found")
        return CheckResult(success=True, name="architecture")

    # Normalize violations for comparison
    normalized_current = {_normalize_violation(v): v for v in violations}

    # Update baseline mode
    if update_baseline:
        _save_baseline(baseline_path, violations)
        _print_info(f"Baseline updated with {len(violations)} violation(s)")
        return CheckResult(success=True, name="architecture")

    # Load baseline
    baseline = _load_baseline(baseline_path)
    normalized_baseline = {_normalize_violation(v) for v in baseline}

    # Compare violations
    new_violations = set(normalized_current.keys()) - normalized_baseline
    fixed_violations = normalized_baseline - set(normalized_current.keys())

    # Report results
    if new_violations:
        print(f"\n{RED}[FAIL] Found {len(new_violations)} NEW architecture violation(s):{RESET}\n")
        for key in sorted(new_violations):
            print(f"{RED}{normalized_current[key]}{RESET}\n")
        _print_error(
            f"Architecture check failed: {len(new_violations)} new violation(s). "
            "Run './stack check --update-baseline' to accept these violations."
        )
        return CheckResult(success=False, name="architecture")

    # Known violations (baseline)
    known_count = len(normalized_current) - len(new_violations)
    if known_count > 0:
        print(f"\n{YELLOW}[WARN] Found {known_count} known (baselined) violation(s){RESET}")
        if fixed_violations:
            fixed_count = len(fixed_violations)
            msg = f"{GREEN}[GOOD] Fixed {fixed_count} violation(s) since baseline!{RESET}\n"
            print(msg)
            for key in sorted(fixed_violations):
                print(f"  {GREEN}✓ {key}{RESET}")
        _print_info(
            f"Architecture check passed: {known_count} known violation(s) baselined, "
            f"{len(new_violations)} new violations"
        )
        return CheckResult(success=True, name="architecture")

    # All violations are new (no baseline exists)
    _print_success("Architecture validation passed")
    return CheckResult(success=True, name="architecture")


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
    skip_architecture: bool = False,
    update_baseline: bool = False,
    repo_root: Path | None = None,
) -> list[CheckResult]:
    """Run all quality checks in sequence.

    Stops at first failure to save time.

    Args:
        fix: If True, auto-fix linting issues. If False, check only.
        include_semantic: If True, include semantic e2e tests.
        semantic_category: Optional category filter for semantic tests.
        skip_architecture: If True, skip architecture validation.
        update_baseline: If True, update architecture baseline with current violations.
        repo_root: Repository root path. Defaults to auto-detected.

    Returns:
        List of CheckResults for each tool run.
    """
    root = repo_root or REPO_ROOT

    print(f"\n{BOLD}Starting Quality Checks...{RESET}")
    print(f"   Root: {root}")
    print(f"   Mode: {'Check only' if not fix else 'Auto-fix enabled'}")

    results: list[CheckResult] = []

    # Architecture validation (FIRST - catches structural issues)
    if not skip_architecture:
        arch_result = run_architecture(repo_root=root, update_baseline=update_baseline)
        results.append(arch_result)
        if update_baseline:
            # Stop after updating baseline
            return results
    else:
        _print_info("Skipping architecture validation")

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
    "run_architecture",
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
