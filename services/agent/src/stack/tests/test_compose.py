from __future__ import annotations

from pathlib import Path
from typing import Any

from pytest import MonkeyPatch

from stack import compose


def test_compose_command_includes_overrides(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    override = tmp_path / "override.yml"
    override.write_text("version: '3.9'\n", encoding="utf-8")

    def fake_resolve(_env: Any = None) -> list[Path]:
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


def test_compose_command_honours_project_name(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    override = tmp_path / "override.yml"
    override.write_text("version: '3.9'\n", encoding="utf-8")

    def fake_resolve(env: dict[str, str]) -> list[Path]:
        assert env["STACK_PROJECT_NAME"] == "custom-project"
        return [Path("/base.yml"), override]

    monkeypatch.setattr(compose, "resolve_compose_files", fake_resolve)

    command = compose._compose_command(
        ["up"], env={"STACK_PROJECT_NAME": "custom-project"}
    )
    assert command[-3:] == ["-p", "custom-project", "up"]
