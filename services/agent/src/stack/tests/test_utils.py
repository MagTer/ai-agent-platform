from __future__ import annotations

import os

from stack import utils


def test_resolve_compose_files_defaults_to_root(tmp_path, monkeypatch):
    monkeypatch.delenv(utils.COMPOSE_FILES_ENV, raising=False)
    files = utils.resolve_compose_files()
    assert files == [utils.DEFAULT_COMPOSE_FILE]


def test_resolve_compose_files_honours_env(monkeypatch, tmp_path):
    override = tmp_path / "override.yml"
    override.write_text("version: '3'\n", encoding="utf-8")
    monkeypatch.setenv(utils.COMPOSE_FILES_ENV, str(override))
    files = utils.resolve_compose_files(os.environ)
    assert files[0] == utils.DEFAULT_COMPOSE_FILE
    assert override in files


def test_resolve_compose_files_accepts_pathlike(tmp_path):
    override = tmp_path / "override.yml"
    override.write_text("version: '3'\n", encoding="utf-8")
    files = utils.resolve_compose_files({utils.COMPOSE_FILES_ENV: override})
    assert files[0] == utils.DEFAULT_COMPOSE_FILE
    assert override in files


def test_resolve_compose_files_supports_relative(monkeypatch, tmp_path):
    relative = "docker-compose.bind.yml"
    monkeypatch.setenv(utils.COMPOSE_FILES_ENV, relative)
    files = utils.resolve_compose_files(os.environ)
    resolved = utils.PROJECT_ROOT / relative
    assert resolved.resolve() in {path.resolve() for path in files}


def test_resolve_project_name_defaults(monkeypatch):
    monkeypatch.delenv(utils.PROJECT_NAME_ENV, raising=False)
    assert utils.resolve_project_name({}) == utils.DEFAULT_PROJECT_NAME


def test_resolve_project_name_from_env(monkeypatch):
    monkeypatch.setenv(utils.PROJECT_NAME_ENV, "my-stack")
    assert utils.resolve_project_name(os.environ) == "my-stack"


def test_resolve_project_name_blanks_fallback(monkeypatch):
    monkeypatch.setenv(utils.PROJECT_NAME_ENV, "   ")
    assert utils.resolve_project_name(os.environ) == utils.DEFAULT_PROJECT_NAME
