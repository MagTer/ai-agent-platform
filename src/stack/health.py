"""Health and status reporting utilities."""

from __future__ import annotations

import datetime as dt

import docker
from rich.console import Console
from rich.table import Table

from .utils import DEFAULT_PROJECT_NAME


def fetch_container_states() -> list[dict[str, str]]:
    """Return a summary of containers belonging to the stack project."""

    client = docker.from_env()
    containers = client.containers.list(
        all=True,
        filters={"label": f"com.docker.compose.project={DEFAULT_PROJECT_NAME}"},
    )

    status_rows: list[dict[str, str]] = []
    for container in containers:
        attrs = container.attrs
        state = attrs.get("State", {})
        health = state.get("Health", {}).get("Status")
        started_at = state.get("StartedAt")
        started_at_fmt = ""
        if started_at and started_at != "0001-01-01T00:00:00Z":
            try:
                started_at_fmt = dt.datetime.fromisoformat(
                    started_at.replace("Z", "+00:00")
                ).strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                started_at_fmt = started_at
        status_rows.append(
            {
                "name": container.name,
                "status": state.get("Status", "unknown"),
                "health": health or "n/a",
                "started": started_at_fmt,
            }
        )
    return status_rows


def render_status_table() -> None:
    """Pretty-print the container status using Rich."""

    rows = fetch_container_states()
    console = Console()
    table = Table(title="AI Agent Platform")
    table.add_column("Container")
    table.add_column("Status")
    table.add_column("Health")
    table.add_column("Started")
    for row in rows:
        table.add_row(row["name"], row["status"], row["health"], row["started"])
    console.print(table)


__all__ = ["fetch_container_states", "render_status_table"]
