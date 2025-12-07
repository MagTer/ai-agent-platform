"""Authentication management commands."""

from __future__ import annotations

import re
from pathlib import Path

import typer
from rich.console import Console

from .utils import PROJECT_ROOT

console = Console()
app = typer.Typer(help="Manage authentication for stack services.")

@app.command("homey")
def login_homey(
    token: str = typer.Option(..., prompt="Enter your Homey API Token", help="The Bearer token for Homey MCP.", hide_input=True),
) -> None:
    """Configure the Homey API token in the .env file."""
    
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        # Copy from template if it doesn't exist
        template_path = PROJECT_ROOT / ".env.template"
        if template_path.exists():
            console.print("[yellow].env file not found. Creating from .env.template...[/yellow]")
            env_path.write_text(template_path.read_text())
        else:
            console.print("[red].env file not found and no template available. Please create .env manually.[/red]")
            raise typer.Exit(code=1)
            
    content = env_path.read_text()
    
    # Regex to replace existing token
    # Handles quoted and unquoted values
    pattern = r"^HOMEY_API_TOKEN=(.*)$\n"
    replacement = f"HOMEY_API_TOKEN=\"{token}\""
    
    match = re.search(pattern, content, re.MULTILINE)
    if match:
        new_content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
    else:
        # Append if not found
        # Ensure we start on a new line if the file doesn't end with one
        prefix = "\n" if content and not content.endswith("\n") else ""
        new_content = content + f"{prefix}{replacement}\n"
        
    env_path.write_text(new_content)
    console.print(f"[green]Successfully updated HOMEY_API_TOKEN in {env_path}[/green]")
    console.print("[cyan]Please restart the stack to apply changes: `stack up -d`[/cyan]")
