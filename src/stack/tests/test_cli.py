from __future__ import annotations

from stack import cli
from typer.testing import CliRunner


def test_status_command(monkeypatch):
    runner = CliRunner()
    called = {"status": False}

    def fake_render():
        called["status"] = True

    monkeypatch.setattr(cli.health, "render_status_table", lambda: fake_render())

    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 0
    assert called["status"]


def test_up_command(monkeypatch):
    runner = CliRunner()
    called = {"up": False}

    def fake_up(detach: bool = True):
        called["up"] = detach

    monkeypatch.setattr(cli.compose, "compose_up", fake_up)

    result = runner.invoke(cli.app, ["up"])
    assert result.exit_code == 0
    assert called["up"] is True
