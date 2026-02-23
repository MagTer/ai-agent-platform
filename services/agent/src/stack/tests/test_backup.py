"""Tests for the stack backup module."""

from __future__ import annotations

import gzip
from pathlib import Path
from unittest.mock import MagicMock, patch

from stack.backup import (
    BACKUP_DIR,
    DEFAULT_RETENTION,
    _db_name_for_env,
    _env_label,
    _postgres_container_name,
    _prune_backups,
    expected_postgres_volume,
    list_backups,
    run_backup,
)
from stack.utils import DEV_PROJECT_NAME, PROD_PROJECT_NAME


def test_postgres_container_name_dev() -> None:
    name = _postgres_container_name(dev=True)
    assert name == f"{DEV_PROJECT_NAME}-postgres-1"


def test_postgres_container_name_prod() -> None:
    name = _postgres_container_name(prod=True)
    assert name == f"{PROD_PROJECT_NAME}-postgres-1"


def test_postgres_container_name_default() -> None:
    name = _postgres_container_name()
    assert name == f"{PROD_PROJECT_NAME}-postgres-1"


def test_env_label() -> None:
    assert _env_label(dev=True) == "dev"
    assert _env_label(prod=True) == "prod"
    assert _env_label() == "unknown"


def test_db_name_for_env() -> None:
    assert _db_name_for_env(dev=True) == "agent_db_dev"
    assert _db_name_for_env(prod=True) == "agent_db"
    assert _db_name_for_env() == "agent_db"


def test_expected_postgres_volume_dev() -> None:
    vol = expected_postgres_volume(dev=True)
    assert vol == f"{DEV_PROJECT_NAME}_postgres_data_dev"


def test_expected_postgres_volume_prod() -> None:
    vol = expected_postgres_volume(prod=True)
    assert vol == f"{PROD_PROJECT_NAME}_postgres_data"


def test_prune_backups_keeps_retention(tmp_path: Path) -> None:
    # Create 7 fake backup files
    for i in range(7):
        (tmp_path / f"dev_2026020{i}_120000.sql.gz").write_bytes(b"fake")

    _prune_backups(tmp_path, env_prefix="dev", retention=5)

    remaining = list(tmp_path.glob("dev_*.sql.gz"))
    assert len(remaining) == 5


def test_prune_backups_noop_when_under_retention(tmp_path: Path) -> None:
    for i in range(3):
        (tmp_path / f"prod_2026020{i}_120000.sql.gz").write_bytes(b"fake")

    _prune_backups(tmp_path, env_prefix="prod", retention=5)

    remaining = list(tmp_path.glob("prod_*.sql.gz"))
    assert len(remaining) == 3


def test_list_backups_empty(tmp_path: Path) -> None:
    result = list_backups(backup_dir=tmp_path)
    assert result == []


def test_list_backups_returns_sorted(tmp_path: Path) -> None:
    (tmp_path / "dev_20260201_120000.sql.gz").write_bytes(b"a")
    (tmp_path / "prod_20260202_120000.sql.gz").write_bytes(b"b")
    (tmp_path / "dev_20260203_120000.sql.gz").write_bytes(b"c")

    result = list_backups(backup_dir=tmp_path)
    assert len(result) == 3
    assert result[0].name == "dev_20260201_120000.sql.gz"
    assert result[2].name == "prod_20260202_120000.sql.gz"


def test_list_backups_nonexistent_dir() -> None:
    result = list_backups(backup_dir=Path("/nonexistent/path"))
    assert result == []


@patch("stack.backup.shutil.which", return_value=None)
def test_run_backup_no_docker(mock_which: MagicMock, tmp_path: Path) -> None:
    result = run_backup(dev=True, backup_dir=tmp_path)
    assert result is None


@patch("stack.backup._is_container_running", return_value=False)
@patch("stack.backup.shutil.which", return_value="/usr/bin/docker")
def test_run_backup_container_not_running(
    mock_which: MagicMock,
    mock_running: MagicMock,
    tmp_path: Path,
) -> None:
    result = run_backup(dev=True, backup_dir=tmp_path)
    assert result is None


@patch("stack.backup.subprocess.run")
@patch("stack.backup._is_container_running", return_value=True)
@patch("stack.backup.shutil.which", return_value="/usr/bin/docker")
def test_run_backup_success(
    mock_which: MagicMock,
    mock_running: MagicMock,
    mock_subprocess: MagicMock,
    tmp_path: Path,
) -> None:
    # Mock successful pg_dump
    mock_subprocess.return_value = MagicMock(
        returncode=0,
        stdout=b"-- PostgreSQL dump\nCREATE TABLE test;",
        stderr=b"",
    )

    result = run_backup(dev=True, backup_dir=tmp_path)

    assert result is not None
    assert result.exists()
    assert result.name.startswith("dev_")
    assert result.name.endswith(".sql.gz")

    # Verify it's valid gzip
    with gzip.open(result, "rb") as f:
        content = f.read()
    assert b"CREATE TABLE test" in content


def test_backup_dir_constant() -> None:
    assert BACKUP_DIR.name == "backups"
    assert BACKUP_DIR.parent.name == "data"


def test_default_retention() -> None:
    assert DEFAULT_RETENTION == 5
