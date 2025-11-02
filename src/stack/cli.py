"""Entry-point for the stack management CLI."""

from __future__ import annotations

import typer
from rich.console import Console

from . import compose, health

app = typer.Typer(help="Manage the local AI agent platform stack.")
console = Console()
SERVICE_ARGUMENT = typer.Argument(None, help="Optional services to filter.")


@app.command()
def up(
    detach: bool = typer.Option(
        True,
        "--detach/--no-detach",
        help="Run services in the background.",
    )
) -> None:
    """Start all services defined in docker-compose.yml."""

    console.print("[bold green]Starting stack...[/bold green]")
    compose.compose_up(detach=detach)
    console.print("[bold green]Stack is running.[/bold green]")


@app.command()
def down(
    remove_volumes: bool = typer.Option(
        False,
        "--volumes/--no-volumes",
        help="Remove persistent volumes.",
    )
) -> None:
    """Stop all running services."""

    console.print("[bold yellow]Stopping stack...[/bold yellow]")
    compose.compose_down(remove_volumes=remove_volumes)
    console.print("[bold yellow]Stack stopped.[/bold yellow]")


@app.command()
def logs(
    service: list[str] = SERVICE_ARGUMENT,
    tail: int = typer.Option(200, help="Number of log lines to return."),
) -> None:
    """Show container logs."""

    output = compose.compose_logs(service, tail)
    console.print(output)


@app.command()
def status() -> None:
    """Display the container status table."""

    health.render_status_table()


if __name__ == "__main__":  # pragma: no cover
    app()
