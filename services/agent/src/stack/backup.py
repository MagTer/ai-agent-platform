"""Database backup and restore utilities for the stack CLI."""

from __future__ import annotations

import gzip
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console

from stack.utils import DEV_PROJECT_NAME, PROD_PROJECT_NAME, PROJECT_ROOT

console = Console()

# Default backup directory (relative to project root)
BACKUP_DIR = PROJECT_ROOT / "data" / "backups"

# Default number of backups to retain
DEFAULT_RETENTION = 5


def _postgres_container_name(*, prod: bool = False, dev: bool = False) -> str:
    """Return the postgres container name for the given environment.

    Container naming convention: {project_name}-postgres-1
    """
    if prod:
        return f"{PROD_PROJECT_NAME}-postgres-1"
    if dev:
        return f"{DEV_PROJECT_NAME}-postgres-1"
    # Fallback -- should not be used for real deploys
    return f"{PROD_PROJECT_NAME}-postgres-1"


def _env_label(*, prod: bool = False, dev: bool = False) -> str:
    """Return a short environment label for filenames."""
    if prod:
        return "prod"
    if dev:
        return "dev"
    return "unknown"


def _is_container_running(container_name: str) -> bool:
    """Check if a Docker container is running."""
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return False
    result = subprocess.run(  # noqa: S603
        [
            docker_bin,
            "inspect",
            "-f",
            "{{.State.Running}}",
            container_name,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _db_name_for_env(*, prod: bool = False, dev: bool = False) -> str:
    """Return the database name for the given environment."""
    if dev:
        return "agent_db_dev"
    return "agent_db"


def run_backup(
    *,
    prod: bool = False,
    dev: bool = False,
    backup_dir: Path | None = None,
    retention: int = DEFAULT_RETENTION,
) -> Path | None:
    """Run pg_dump inside the postgres container and save compressed backup.

    Returns the path to the backup file, or None if backup failed (non-fatal).
    """
    docker_bin = shutil.which("docker")
    if not docker_bin:
        console.print("[yellow]Warning: Docker not found, skipping backup.[/yellow]")
        return None

    container = _postgres_container_name(prod=prod, dev=dev)
    env = _env_label(prod=prod, dev=dev)
    db_name = _db_name_for_env(prod=prod, dev=dev)
    target_dir = backup_dir or BACKUP_DIR

    # Check if container is running
    if not _is_container_running(container):
        console.print(
            f"[yellow]Warning: Postgres container '{container}' is not running. "
            f"Skipping backup.[/yellow]"
        )
        return None

    # Create backup directory
    target_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    filename = f"{env}_{timestamp}.sql.gz"
    backup_path = target_dir / filename

    console.print(f"[cyan]Backing up {env} database ({db_name})...[/cyan]")

    # Run pg_dump inside container, pipe through gzip, write to host
    # We use docker exec to run pg_dump, then capture stdout and gzip on host
    try:
        dump_proc = subprocess.run(  # noqa: S603
            [
                docker_bin,
                "exec",
                container,
                "pg_dump",
                "-U",
                "postgres",
                "--clean",
                "--if-exists",
                db_name,
            ],
            capture_output=True,
            check=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        console.print(f"[yellow]Warning: pg_dump failed: {stderr}[/yellow]")
        console.print("[yellow]Continuing deploy without backup.[/yellow]")
        return None
    except subprocess.TimeoutExpired:
        console.print("[yellow]Warning: pg_dump timed out after 120s.[/yellow]")
        console.print("[yellow]Continuing deploy without backup.[/yellow]")
        return None

    # Compress with gzip
    try:
        with gzip.open(backup_path, "wb") as f:
            f.write(dump_proc.stdout)
    except OSError as exc:
        console.print(f"[yellow]Warning: Failed to write backup: {exc}[/yellow]")
        return None

    size_mb = backup_path.stat().st_size / (1024 * 1024)
    console.print(f"[green]Backup saved: {backup_path.name} ({size_mb:.1f} MB)[/green]")

    # Prune old backups (keep last N for this environment)
    _prune_backups(target_dir, env_prefix=env, retention=retention)

    return backup_path


def _prune_backups(backup_dir: Path, env_prefix: str, retention: int) -> None:
    """Remove old backups, keeping the most recent `retention` files."""
    pattern = f"{env_prefix}_*.sql.gz"
    backups = sorted(backup_dir.glob(pattern), key=lambda p: p.name)

    if len(backups) <= retention:
        return

    to_remove = backups[: len(backups) - retention]
    for old_backup in to_remove:
        old_backup.unlink()
        console.print(f"[dim]Pruned old backup: {old_backup.name}[/dim]")


def list_backups(backup_dir: Path | None = None) -> list[Path]:
    """List all backup files sorted by name (newest last)."""
    target_dir = backup_dir or BACKUP_DIR
    if not target_dir.exists():
        return []
    return sorted(target_dir.glob("*.sql.gz"), key=lambda p: p.name)


def restore_backup(
    backup_file: Path,
    *,
    prod: bool = False,
    dev: bool = False,
) -> bool:
    """Restore a backup file into the postgres container.

    Returns True on success, False on failure.
    """
    docker_bin = shutil.which("docker")
    if not docker_bin:
        console.print("[red]Docker not found.[/red]")
        return False

    if not backup_file.exists():
        console.print(f"[red]Backup file not found: {backup_file}[/red]")
        return False

    container = _postgres_container_name(prod=prod, dev=dev)
    db_name = _db_name_for_env(prod=prod, dev=dev)

    if not _is_container_running(container):
        console.print(f"[red]Postgres container '{container}' is not running.[/red]")
        return False

    console.print(f"[cyan]Restoring {backup_file.name} to {db_name}...[/cyan]")

    # Decompress and pipe to psql inside container
    try:
        with gzip.open(backup_file, "rb") as f:
            sql_data = f.read()
    except OSError as exc:
        console.print(f"[red]Failed to read backup: {exc}[/red]")
        return False

    try:
        result = subprocess.run(  # noqa: S603
            [
                docker_bin,
                "exec",
                "-i",
                container,
                "psql",
                "-U",
                "postgres",
                "-d",
                db_name,
            ],
            input=sql_data,
            capture_output=True,
            check=True,
            timeout=120,
        )
        # psql outputs notices to stderr -- only fail on non-zero exit
        _ = result
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        console.print(f"[red]Restore failed: {stderr}[/red]")
        return False
    except subprocess.TimeoutExpired:
        console.print("[red]Restore timed out after 120s.[/red]")
        return False

    console.print("[green]Restore complete.[/green]")
    return True


def check_volume_exists(
    volume_name: str,
    *,
    warn_label: str = "",
) -> bool:
    """Check if a Docker volume exists. Warn loudly if it does not.

    A missing expected volume means Docker will create a new empty one,
    which causes data loss.

    Returns True if volume exists, False if missing.
    """
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return True  # Can't check without docker, assume OK

    result = subprocess.run(  # noqa: S603
        [docker_bin, "volume", "inspect", volume_name],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        label = f" ({warn_label})" if warn_label else ""
        console.print(
            f"[bold red]WARNING: Docker volume '{volume_name}' does not exist!{label}[/bold red]"
        )
        console.print(
            "[bold red]A new empty volume will be created. "
            "This may cause DATA LOSS if you had data in a differently-named volume.[/bold red]"
        )
        console.print(
            "[yellow]Check your Docker Compose project name and volume configuration.[/yellow]"
        )
        return False
    return True


def expected_postgres_volume(*, prod: bool = False, dev: bool = False) -> str:
    """Return the expected Docker volume name for the postgres data.

    Volume naming: {project_name}_{volume_name_from_compose}
    - Dev:  ai-agent-platform-dev_postgres_data_dev
    - Prod: ai-agent-platform-prod_postgres_data
    """
    if dev:
        return f"{DEV_PROJECT_NAME}_postgres_data_dev"
    return f"{PROD_PROJECT_NAME}_postgres_data"
