from __future__ import annotations

from pathlib import Path

import pytest

from core.runtime.config import Settings


def test_settings_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_APP_NAME", "Test Agent")
    monkeypatch.setenv("AGENT_CONTEXTS_DIR", str(tmp_path / "contexts"))

    settings = Settings()

    assert settings.app_name == "Test Agent"
    assert settings.contexts_dir == tmp_path / "contexts"
