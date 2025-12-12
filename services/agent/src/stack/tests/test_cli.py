from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pytest import MonkeyPatch
from typer.testing import CliRunner

from stack import cli

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

    monkeypatch.setattr(cli, "_repo_root", lambda: Path(tmp_path))
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
        "_ensure_models",
        lambda repo_root: called.setdefault("models", repo_root),
    )
    monkeypatch.setattr(
        cli,
        "_wait_for_service",
        lambda **_: called.setdefault("wait", True),
    )

    def fake_compose_up(*, detach: bool, build: bool, extra_files: list[Path] | None) -> None:
        called["compose_up"] = {
            "detach": detach,
            "build": build,
            "extra": extra_files,
        }

    monkeypatch.setattr(cli.compose, "compose_up", fake_compose_up)
    monkeypatch.setattr(
        cli.compose,
        "run_compose",
        lambda args, extra_files=None: SimpleNamespace(stdout=b"stack status"),
    )

    result = runner.invoke(cli.app, ["up"])
    assert result.exit_code == 0
    assert called["compose_up"] == {"detach": True, "build": False, "extra": []}
    assert called.get("docker") is True
    assert called.get("secrets") == {"OPENWEBUI_SECRET": "x", "SEARXNG_SECRET": "y"}
    assert called.get("models") == Path(tmp_path)
