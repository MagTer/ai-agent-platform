from __future__ import annotations

import os
from pathlib import Path

from pytest import MonkeyPatch

from stack import utils


def test_resolve_compose_files_defaults_to_root(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv(utils.COMPOSE_FILES_ENV, raising=False)
    files = utils.resolve_compose_files()
    assert files == [utils.DEFAULT_COMPOSE_FILE]


def test_resolve_compose_files_honours_env(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    override = tmp_path / "override.yml"
    override.write_text("version: '3'\n", encoding="utf-8")
    monkeypatch.setenv(utils.COMPOSE_FILES_ENV, str(override))
    files = utils.resolve_compose_files(os.environ)
    assert files[0] == utils.DEFAULT_COMPOSE_FILE
    assert override in files


def test_resolve_compose_files_accepts_pathlike(tmp_path: Path) -> None:
    override = tmp_path / "override.yml"
    override.write_text("version: '3'\n", encoding="utf-8")
    files = utils.resolve_compose_files({utils.COMPOSE_FILES_ENV: str(override)})
    assert files[0] == utils.DEFAULT_COMPOSE_FILE
    assert override in files


def test_resolve_compose_files_supports_relative(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    relative = "docker-compose.bind.yml"
    monkeypatch.setenv(utils.COMPOSE_FILES_ENV, relative)
    files = utils.resolve_compose_files(os.environ)
    resolved = utils.PROJECT_ROOT / relative
    assert resolved.resolve() in {path.resolve() for path in files}


def test_resolve_project_name_defaults(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv(utils.PROJECT_NAME_ENV, raising=False)
    assert utils.resolve_project_name({}) == utils.DEFAULT_PROJECT_NAME


def test_resolve_project_name_from_env(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv(utils.PROJECT_NAME_ENV, "my-stack")
    assert utils.resolve_project_name(os.environ) == "my-stack"


def test_resolve_project_name_blanks_fallback(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv(utils.PROJECT_NAME_ENV, "   ")
    assert utils.resolve_project_name(os.environ) == utils.DEFAULT_PROJECT_NAME
