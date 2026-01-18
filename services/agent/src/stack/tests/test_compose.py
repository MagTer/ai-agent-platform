from __future__ import annotations

from pathlib import Path
from typing import Any

from pytest import MonkeyPatch

from stack import compose
from stack.utils import DEV_PROJECT_NAME, PROD_PROJECT_NAME


def test_compose_command_includes_overrides(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    override = tmp_path / "override.yml"
    override.write_text("version: '3.9'\n", encoding="utf-8")

    def fake_resolve(_env: Any = None, *, prod: bool = False, dev: bool = False) -> list[Path]:
        return [Path("/base.yml"), override]

    monkeypatch.setattr(compose, "resolve_compose_files", fake_resolve)

    command = compose._compose_command(["up"], env={})
    expected_sequence = [
        "docker",
        "compose",
        "-f",
        "/base.yml",
        "-f",
        override.as_posix(),
        "-p",
        compose.resolve_project_name({}),
        "up",
    ]
    assert command == expected_sequence


def test_compose_command_honours_project_name(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    override = tmp_path / "override.yml"
    override.write_text("version: '3.9'\n", encoding="utf-8")

    def fake_resolve(env: dict[str, str], *, prod: bool = False, dev: bool = False) -> list[Path]:
        assert env["STACK_PROJECT_NAME"] == "custom-project"
        return [Path("/base.yml"), override]

    monkeypatch.setattr(compose, "resolve_compose_files", fake_resolve)

    command = compose._compose_command(["up"], env={"STACK_PROJECT_NAME": "custom-project"})
    assert command[-3:] == ["-p", "custom-project", "up"]


def test_compose_command_dev_mode_uses_dev_project_name(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """When dev=True, the command should use the dev project name."""
    override = tmp_path / "dev.yml"
    override.write_text("version: '3.9'\n", encoding="utf-8")

    def fake_resolve(_env: Any = None, *, prod: bool = False, dev: bool = False) -> list[Path]:
        return [Path("/base.yml"), override]

    monkeypatch.setattr(compose, "resolve_compose_files", fake_resolve)

    command = compose._compose_command(["up"], env={}, dev=True)
    assert "-p" in command
    project_idx = command.index("-p")
    assert command[project_idx + 1] == DEV_PROJECT_NAME


def test_compose_command_prod_mode_uses_prod_project_name(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """When prod=True, the command should use the prod project name."""
    override = tmp_path / "prod.yml"
    override.write_text("version: '3.9'\n", encoding="utf-8")

    def fake_resolve(_env: Any = None, *, prod: bool = False, dev: bool = False) -> list[Path]:
        return [Path("/base.yml"), override]

    monkeypatch.setattr(compose, "resolve_compose_files", fake_resolve)

    command = compose._compose_command(["up"], env={}, prod=True)
    assert "-p" in command
    project_idx = command.index("-p")
    assert command[project_idx + 1] == PROD_PROJECT_NAME
