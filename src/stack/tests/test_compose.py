from __future__ import annotations

from pathlib import Path

from stack import compose


def test_compose_command_includes_overrides(monkeypatch, tmp_path):
    override = tmp_path / "override.yml"
    override.write_text("version: '3.9'\n", encoding="utf-8")

    def fake_resolve(_env=None):
        return [Path("/base.yml"), override]

    monkeypatch.setattr(compose, "resolve_compose_files", fake_resolve)

    command = compose._compose_command(["up"], env={})
    expected_sequence = [
        "docker",
        "compose",
        "-f",
        "/base.yml",
        "-f",
        str(override),
        "-p",
        compose.DEFAULT_PROJECT_NAME,
        "up",
    ]
    assert command == expected_sequence
