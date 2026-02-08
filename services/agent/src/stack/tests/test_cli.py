from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pytest import MonkeyPatch
from typer.testing import CliRunner

from stack import checks, cli

runner = CliRunner()


def test_status_command(monkeypatch: MonkeyPatch) -> None:
    called = {"status": False}

    def fake_render() -> None:
        called["status"] = True

    monkeypatch.setattr(cli.health, "render_status_table", fake_render)

    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 0
    assert called["status"]


def test_up_command(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    called: dict[str, Any] = {}

    monkeypatch.setattr(
        cli.tooling,
        "ensure_docker",
        lambda: called.setdefault("docker", True),
    )
    monkeypatch.setattr(
        cli.utils,
        "load_environment",
        lambda: {"OPENWEBUI_SECRET": "x", "SEARXNG_SECRET": "y"},
    )
    monkeypatch.setattr(
        cli.tooling,
        "ensure_secrets",
        lambda env: called.setdefault("secrets", env),
    )
    monkeypatch.setattr(
        cli,
        "_wait_for_service",
        lambda **_: called.setdefault("wait", True),
    )

    def fake_compose_up(
        *, detach: bool, build: bool, extra_files: list[Path] | None, prod: bool = False
    ) -> None:
        called["compose_up"] = {
            "detach": detach,
            "build": build,
            "extra": extra_files,
            "prod": prod,
        }

    monkeypatch.setattr(cli.compose, "compose_up", fake_compose_up)
    monkeypatch.setattr(
        cli.compose,
        "run_compose",
        lambda args, extra_files=None, prod=False: SimpleNamespace(stdout=b"stack status"),
    )

    result = runner.invoke(cli.app, ["up"])
    assert result.exit_code == 0
    assert called["compose_up"] == {"detach": True, "build": False, "extra": [], "prod": False}
    assert called.get("docker") is True
    assert called.get("secrets") == {"OPENWEBUI_SECRET": "x", "SEARXNG_SECRET": "y"}


# --- Lint command tests ---


def test_lint_command_success(monkeypatch: MonkeyPatch) -> None:
    """Test that lint command runs ruff and black."""
    called: dict[str, Any] = {}

    monkeypatch.setattr(cli.checks, "ensure_dependencies", lambda: None)

    def fake_run_lint(*, fix: bool, repo_root: Path | None) -> list[checks.CheckResult]:
        called["fix"] = fix
        called["repo_root"] = repo_root
        return [
            checks.CheckResult(success=True, name="ruff"),
            checks.CheckResult(success=True, name="black"),
        ]

    monkeypatch.setattr(cli.checks, "run_lint", fake_run_lint)

    result = runner.invoke(cli.app, ["lint"])
    assert result.exit_code == 0
    assert "passed" in result.output.lower()
    assert called["fix"] is True


def test_lint_command_no_fix(monkeypatch: MonkeyPatch) -> None:
    """Test that lint --no-fix disables auto-fix."""
    called: dict[str, Any] = {}

    monkeypatch.setattr(cli.checks, "ensure_dependencies", lambda: None)

    def fake_run_lint(*, fix: bool, repo_root: Path | None) -> list[checks.CheckResult]:
        called["fix"] = fix
        return [
            checks.CheckResult(success=True, name="ruff"),
            checks.CheckResult(success=True, name="black"),
        ]

    monkeypatch.setattr(cli.checks, "run_lint", fake_run_lint)

    result = runner.invoke(cli.app, ["lint", "--no-fix"])
    assert result.exit_code == 0
    assert called["fix"] is False


def test_lint_command_failure(monkeypatch: MonkeyPatch) -> None:
    """Test that lint command exits with code 1 on failure."""
    monkeypatch.setattr(cli.checks, "ensure_dependencies", lambda: None)

    def fake_run_lint(*, fix: bool, repo_root: Path | None) -> list[checks.CheckResult]:
        return [checks.CheckResult(success=False, name="ruff", message="Lint errors")]

    monkeypatch.setattr(cli.checks, "run_lint", fake_run_lint)

    result = runner.invoke(cli.app, ["lint"])
    assert result.exit_code == 1
    assert "failed" in result.output.lower()


# --- Typecheck command tests ---


def test_typecheck_command_success(monkeypatch: MonkeyPatch) -> None:
    """Test that typecheck command runs mypy."""
    monkeypatch.setattr(cli.checks, "ensure_dependencies", lambda: None)
    monkeypatch.setattr(
        cli.checks,
        "run_mypy",
        lambda repo_root: checks.CheckResult(success=True, name="mypy"),
    )

    result = runner.invoke(cli.app, ["typecheck"])
    assert result.exit_code == 0
    assert "passed" in result.output.lower()


def test_typecheck_command_failure(monkeypatch: MonkeyPatch) -> None:
    """Test that typecheck command exits with code 1 on failure."""
    monkeypatch.setattr(cli.checks, "ensure_dependencies", lambda: None)
    monkeypatch.setattr(
        cli.checks,
        "run_mypy",
        lambda repo_root: checks.CheckResult(success=False, name="mypy"),
    )

    result = runner.invoke(cli.app, ["typecheck"])
    assert result.exit_code == 1
    assert "failed" in result.output.lower()


# --- Test command tests ---


def test_test_command_success(monkeypatch: MonkeyPatch) -> None:
    """Test that test command runs pytest."""
    monkeypatch.setattr(cli.checks, "ensure_dependencies", lambda: None)
    monkeypatch.setattr(
        cli.checks,
        "run_pytest",
        lambda repo_root: checks.CheckResult(success=True, name="pytest"),
    )

    result = runner.invoke(cli.app, ["test"])
    assert result.exit_code == 0
    assert "passed" in result.output.lower()


def test_test_command_with_semantic(monkeypatch: MonkeyPatch) -> None:
    """Test that test --semantic also runs semantic tests."""
    called: dict[str, Any] = {}

    monkeypatch.setattr(cli.checks, "ensure_dependencies", lambda: None)
    monkeypatch.setattr(
        cli.checks,
        "run_pytest",
        lambda repo_root: checks.CheckResult(success=True, name="pytest"),
    )

    def fake_run_semantic(
        *, repo_root: Path | None, category: str | None = None
    ) -> checks.CheckResult:
        called["semantic"] = True
        called["category"] = category
        return checks.CheckResult(success=True, name="semantic")

    monkeypatch.setattr(cli.checks, "run_semantic_tests", fake_run_semantic)

    result = runner.invoke(cli.app, ["test", "--semantic"])
    assert result.exit_code == 0
    assert called.get("semantic") is True


# --- Check command tests ---


def test_check_command_success(monkeypatch: MonkeyPatch) -> None:
    """Test that check command runs all checks."""
    called: dict[str, Any] = {}

    monkeypatch.setattr(cli.checks, "ensure_dependencies", lambda: None)

    def fake_run_all(
        *,
        fix: bool,
        include_semantic: bool,
        semantic_category: str | None = None,
        skip_architecture: bool = False,
        update_baseline: bool = False,
        repo_root: Path | None,
    ) -> list[checks.CheckResult]:
        called["fix"] = fix
        called["include_semantic"] = include_semantic
        called["semantic_category"] = semantic_category
        called["update_baseline"] = update_baseline
        return [
            checks.CheckResult(success=True, name="ruff"),
            checks.CheckResult(success=True, name="black"),
            checks.CheckResult(success=True, name="mypy"),
            checks.CheckResult(success=True, name="pytest"),
        ]

    monkeypatch.setattr(cli.checks, "run_all_checks", fake_run_all)

    result = runner.invoke(cli.app, ["check"])
    assert result.exit_code == 0
    assert "passed" in result.output.lower()
    assert called["fix"] is True
    assert called["include_semantic"] is False
    assert called["update_baseline"] is False


def test_check_command_no_fix(monkeypatch: MonkeyPatch) -> None:
    """Test that check --no-fix disables auto-fix."""
    called: dict[str, Any] = {}

    monkeypatch.setattr(cli.checks, "ensure_dependencies", lambda: None)

    def fake_run_all(
        *,
        fix: bool,
        include_semantic: bool,
        semantic_category: str | None = None,
        skip_architecture: bool = False,
        update_baseline: bool = False,
        repo_root: Path | None,
    ) -> list[checks.CheckResult]:
        called["fix"] = fix
        return [checks.CheckResult(success=True, name="ruff")]

    monkeypatch.setattr(cli.checks, "run_all_checks", fake_run_all)

    result = runner.invoke(cli.app, ["check", "--no-fix"])
    assert result.exit_code == 0
    assert called["fix"] is False


def test_check_command_failure(monkeypatch: MonkeyPatch) -> None:
    """Test that check command exits with code 1 on failure."""
    monkeypatch.setattr(cli.checks, "ensure_dependencies", lambda: None)

    def fake_run_all(
        *,
        fix: bool,
        include_semantic: bool,
        semantic_category: str | None = None,
        skip_architecture: bool = False,
        update_baseline: bool = False,
        repo_root: Path | None,
    ) -> list[checks.CheckResult]:
        return [
            checks.CheckResult(success=True, name="ruff"),
            checks.CheckResult(success=False, name="mypy", message="Type errors"),
        ]

    monkeypatch.setattr(cli.checks, "run_all_checks", fake_run_all)

    result = runner.invoke(cli.app, ["check"])
    assert result.exit_code == 1
    assert "failed" in result.output.lower()


# --- Dev command tests ---


def test_dev_up_command(monkeypatch: MonkeyPatch) -> None:
    """Test that dev up uses dev project name and compose files."""
    called: dict[str, Any] = {}

    monkeypatch.setattr(cli.tooling, "ensure_docker", lambda: None)
    monkeypatch.setattr(
        cli.utils,
        "load_environment",
        lambda: {"OPENWEBUI_SECRET": "x", "SEARXNG_SECRET": "y", "POSTGRES_PASSWORD": "test"},
    )
    monkeypatch.setattr(cli.tooling, "ensure_secrets", lambda env: None)

    def fake_compose_up(*, detach: bool, build: bool, dev: bool = False, **kwargs: Any) -> None:
        called["compose_up"] = {"detach": detach, "build": build, "dev": dev}

    monkeypatch.setattr(cli.compose, "compose_up", fake_compose_up)
    monkeypatch.setattr(
        cli.compose,
        "run_compose",
        lambda args, dev=False, **kwargs: SimpleNamespace(stdout=b"dev status"),
    )

    result = runner.invoke(cli.app, ["dev", "up"])
    assert result.exit_code == 0
    assert called["compose_up"]["dev"] is True
    assert "development" in result.output.lower()


def test_dev_down_command(monkeypatch: MonkeyPatch) -> None:
    """Test that dev down stops dev environment."""
    called: dict[str, Any] = {}

    monkeypatch.setattr(cli.tooling, "ensure_docker", lambda: None)

    def fake_compose_down(
        *, remove_volumes: bool = False, dev: bool = False, **kwargs: Any
    ) -> None:
        called["compose_down"] = {"remove_volumes": remove_volumes, "dev": dev}

    monkeypatch.setattr(cli.compose, "compose_down", fake_compose_down)

    result = runner.invoke(cli.app, ["dev", "down"])
    assert result.exit_code == 0
    assert called["compose_down"]["dev"] is True


def test_dev_status_command(monkeypatch: MonkeyPatch) -> None:
    """Test that dev status shows dev environment status."""
    monkeypatch.setattr(cli.tooling, "ensure_docker", lambda: None)

    def fake_run_compose(args: list[str], dev: bool = False, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(stdout=b"NAME  STATUS\ndev-agent  running")

    monkeypatch.setattr(cli.compose, "run_compose", fake_run_compose)

    result = runner.invoke(cli.app, ["dev", "status"])
    assert result.exit_code == 0
    assert "development" in result.output.lower()


# --- Deploy command tests ---


def test_deploy_blocks_non_main_branch(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Test that deploy refuses to run from non-main branch."""
    (tmp_path / "docker-compose.yml").write_text("# fake")

    monkeypatch.setattr(cli.tooling, "ensure_docker", lambda: None)
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli.tooling, "current_branch", lambda path: "feature/test-branch")

    result = runner.invoke(cli.app, ["deploy"])
    assert result.exit_code == 1
    assert "cannot deploy" in result.output.lower()
    assert "main" in result.output.lower()


def test_deploy_allows_main_branch(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Test that deploy allows deployment from main branch."""
    called: dict[str, Any] = {}

    (tmp_path / "docker-compose.yml").write_text("# fake")

    monkeypatch.setattr(cli.tooling, "ensure_docker", lambda: None)
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli.tooling, "current_branch", lambda path: "main")

    # Mock quality checks
    monkeypatch.setattr(cli.checks, "ensure_dependencies", lambda: None)
    monkeypatch.setattr(
        cli.checks,
        "run_all_checks",
        lambda **kwargs: [checks.CheckResult(success=True, name="all")],
    )

    def fake_run_compose(args: list[str], **kwargs: Any) -> SimpleNamespace:
        called.setdefault("compose_calls", []).append(args)
        return SimpleNamespace(stdout=b"status output")

    monkeypatch.setattr(cli.compose, "run_compose", fake_run_compose)

    result = runner.invoke(cli.app, ["deploy"])
    assert result.exit_code == 0
    assert "deployment complete" in result.output.lower()


def test_deploy_force_allows_feature_branch(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Test that deploy --force allows deployment from feature branch."""
    (tmp_path / "docker-compose.yml").write_text("# fake")

    monkeypatch.setattr(cli.tooling, "ensure_docker", lambda: None)
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli.tooling, "current_branch", lambda path: "feature/test-branch")

    # Mock quality checks
    monkeypatch.setattr(cli.checks, "ensure_dependencies", lambda: None)
    monkeypatch.setattr(
        cli.checks,
        "run_all_checks",
        lambda **kwargs: [checks.CheckResult(success=True, name="all")],
    )

    monkeypatch.setattr(
        cli.compose,
        "run_compose",
        lambda args, **kwargs: SimpleNamespace(stdout=b"status"),
    )

    result = runner.invoke(cli.app, ["deploy", "--force"])
    assert result.exit_code == 0
    assert "warning" in result.output.lower()


def test_deploy_skip_checks(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Test that deploy --skip-checks skips quality checks."""
    quality_check_called = {"called": False}

    (tmp_path / "docker-compose.yml").write_text("# fake")

    monkeypatch.setattr(cli.tooling, "ensure_docker", lambda: None)
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(cli.tooling, "current_branch", lambda path: "main")

    def fake_run_quality_checks(repo_root: Path) -> None:
        quality_check_called["called"] = True

    monkeypatch.setattr(cli, "_run_quality_checks", fake_run_quality_checks)
    monkeypatch.setattr(
        cli.compose,
        "run_compose",
        lambda args, **kwargs: SimpleNamespace(stdout=b"status"),
    )

    result = runner.invoke(cli.app, ["deploy", "--skip-checks"])
    assert result.exit_code == 0
    assert quality_check_called["called"] is False
