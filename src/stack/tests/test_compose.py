from __future__ import annotations

import subprocess

import pytest

from stack import compose


def test_run_compose_requires_secrets(monkeypatch):
    """Compose should fail fast if required secrets are missing."""

    monkeypatch.setattr(compose, "load_environment", lambda: {"OPENWEBUI_SECRET": "", "SEARXNG_SECRET": ""})

    with pytest.raises(compose.ComposeError) as exc:
        compose.run_compose(["ps"])

    assert "OPENWEBUI_SECRET" in str(exc.value)


def test_run_compose_invokes_docker_when_env_valid(monkeypatch):
    calls: dict[str, subprocess.CompletedProcess[bytes] | None] = {"command": None}

    def fake_run(command, check, env, capture_output):  # noqa: ANN001, ANN201
        assert "OPENWEBUI_SECRET" in env
        assert "SEARXNG_SECRET" in env
        calls["command"] = subprocess.CompletedProcess(command, 0, b"ok", b"")
        return calls["command"]

    monkeypatch.setattr(compose, "load_environment", lambda: {
        "OPENWEBUI_SECRET": "secret",
        "SEARXNG_SECRET": "secret",
    })
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = compose.run_compose(["ps"])
    assert result is calls["command"]
