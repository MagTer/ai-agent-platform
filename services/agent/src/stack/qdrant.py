"""Qdrant management commands."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import httpx
import typer

from stack import compose, tooling

app = typer.Typer(help="Qdrant schema, backup and restore helpers.")


def _repo_root() -> Path:
    return tooling.resolve_repo_root()


@app.command("ensure-schema")
def ensure_schema(
    host: str = typer.Option("localhost", help="Qdrant host."),
    port: int = typer.Option(6333, help="Qdrant HTTP port."),
    collection: str = typer.Option("memory", help="Collection name."),
    size: int = typer.Option(4096, help="Vector size."),
    distance: str = typer.Option("Cosine", help="Distance metric (Cosine, Euclid, Dot)."),
    recreate: bool = typer.Option(False, help="Drop the collection before ensuring schema."),
    hnsw_m: int = typer.Option(32, help="HNSW index M parameter (graph connectivity)."),
    hnsw_ef_construct: int = typer.Option(256, help="HNSW ef_construct (build quality)."),
) -> None:
    """Ensure the target Qdrant collection exists with the requested schema."""

    base_url = f"http://{host}:{port}"
    client = httpx.Client(timeout=5.0)
    try:
        exists = False
        distance_value = distance.capitalize()
        if distance_value not in {"Cosine", "Euclid", "Dot"}:
            raise typer.BadParameter("distance must be Cosine, Euclid or Dot")
        try:
            response = client.get(f"{base_url}/collections/{collection}")
            exists = response.status_code == 200 and response.json().get("status") == "ok"
        except httpx.HTTPError:
            exists = False

        if exists and recreate:
            client.delete(f"{base_url}/collections/{collection}").raise_for_status()
            exists = False

        if not exists:
            payload = {
                "vectors": {"size": size, "distance": distance_value},
                "hnsw_config": {"m": hnsw_m, "ef_construct": hnsw_ef_construct},
            }
            client.put(f"{base_url}/collections/{collection}", json=payload).raise_for_status()
        typer.echo(f"Collection '{collection}' is ensured at {host}:{port}.")
    finally:
        client.close()


@app.command("backup")
def backup(
    backup_dir: Path = typer.Option(Path("backups"), help="Destination directory for archives."),
    container: str = typer.Option("qdrant", help="Running Qdrant container name."),
) -> None:
    """Create a compressed archive of the Qdrant storage volume."""

    tooling.ensure_docker()
    repo_root = _repo_root()
    tooling.ensure_container_exists(container)
    destination = (repo_root / backup_dir).resolve()
    tooling.ensure_directory(destination)

    archive = destination / f"qdrant-{datetime.now().strftime('%Y%m%d-%H%M%S')}.tgz"
    typer.echo(f"Creating backup → {archive}")
    tooling.run_command(
        [
            "docker",
            "run",
            "--rm",
            "--volumes-from",
            container,
            "-v",
            f"{destination}:/backup",
            "alpine",
            "sh",
            "-lc",
            f"tar czf /backup/{archive.name} /qdrant/storage",
        ]
    )
    typer.echo(f"Backup written: {archive}")


@app.command("restore")
def restore(
    backup_file: Path = typer.Argument(..., help="Path to a previously created archive."),
    container: str = typer.Option("qdrant", help="Running Qdrant container name."),
    compose_file: Path = typer.Option(
        Path("docker-compose.yml"),
        help="Compose file controlling Qdrant.",
    ),
) -> None:
    """Restore a backup created via ``backup``."""

    tooling.ensure_docker()
    repo_root = _repo_root()
    resolved_backup = (repo_root / backup_file).resolve()
    if not resolved_backup.exists():
        raise FileNotFoundError(f"Backup file not found: {resolved_backup}")

    compose_path = compose_file if compose_file.is_absolute() else (repo_root / compose_file)
    tooling.ensure_container_exists(container)

    typer.confirm(
        f"This will stop Qdrant and replace its data with backup '{backup_file.name}'. Continue?",
        abort=True,
    )

    typer.echo("Stopping Qdrant via docker compose…")
    compose.run_compose(["stop", "qdrant"], files_override=[compose_path])

    typer.echo(f"Restoring from {resolved_backup}…")
    tooling.run_command(
        [
            "docker",
            "run",
            "--rm",
            "--volumes-from",
            container,
            "-v",
            f"{resolved_backup.parent}:/backup",
            "alpine",
            "sh",
            "-lc",
            f"rm -rf /qdrant/storage/* && tar xzf /backup/{resolved_backup.name} -C /",
        ],
    )

    typer.echo("Starting Qdrant via docker compose…")
    compose.run_compose(["start", "qdrant"], files_override=[compose_path])
    typer.echo("Restore complete.")


__all__ = ["app"]
