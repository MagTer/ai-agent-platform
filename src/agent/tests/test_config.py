from __future__ import annotations

from pathlib import Path

import pytest

from agent.core.config import Settings


def test_settings_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_APP_NAME", "Test Agent")
    monkeypatch.setenv("AGENT_SQLITE_STATE_PATH", str(tmp_path / "state.sqlite"))

    settings = Settings()

    assert settings.app_name == "Test Agent"
    assert settings.sqlite_state_path == tmp_path / "state.sqlite"
