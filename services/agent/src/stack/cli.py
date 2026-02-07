from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, TypedDict

import httpx
import typer
from rich.console import Console
from rich.table import Table

from stack import auth, checks, compose, health, qdrant, tooling, utils

console = Console()
app = typer.Typer(help="Manage the local AI agent platform stack.")
dev_app = typer.Typer(help="Development environment commands (isolated from production).")
repo_app = typer.Typer(help="Repository snapshot utilities.")
n8n_app = typer.Typer(help="Import or export n8n workflows.")
openwebui_app = typer.Typer(help="Manage Open WebUI database exports and restores.")
app.add_typer(dev_app, name="dev")
app.add_typer(repo_app, name="repo")
app.add_typer(n8n_app, name="n8n")
app.add_typer(openwebui_app, name="openwebui")
app.add_typer(qdrant.app, name="qdrant")
app.add_typer(auth.app, name="login")


def _ensure_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8")


DEFAULT_LOG_SERVICES = [
    "litellm",
    "open-webui",
    "qdrant",
    "searxng",
    "agent",
    "postgres",
]


def _console_git_printer(args: Sequence[str]) -> None:
    console.print(f"[cyan]git {' '.join(args)}[/cyan]")


def _require_branch_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise typer.BadParameter("Branch name cannot be empty.")
    if cleaned == "main":
        raise typer.BadParameter("Branch 'main' is protected; choose another branch name.")
    return cleaned


def _sanitize_branch_name(value: str) -> str:
    """Clean up whitespace and turn spaces into hyphens."""

    return value.replace(" ", "-")


def _validate_feature_branch_name(value: str) -> str:
    candidate = _sanitize_branch_name(value)
    candidate = _require_branch_name(candidate)
    if not candidate.startswith("feature/"):
        raise typer.BadParameter("Branch name must start with feature/.")
    return candidate


def _prompt_feature_branch_name(default_suffix: str = "stack-save") -> str:
    default = f"feature/{default_suffix}"
    prompt_text = f"Branch name to commit to [{default}]: "
    while True:
        response = input(prompt_text).strip()
        candidate = response or default
        sanitized = _sanitize_branch_name(candidate)
        if candidate and sanitized != candidate:
            console.print(
                f"[yellow]Branch name adjusted to {sanitized} (spaces -> hyphens).[/yellow]"
            )
        try:
            candidate = _validate_feature_branch_name(sanitized)
        except typer.BadParameter as exc:
            console.print(f"[yellow]{exc}[/yellow]")
            continue
        return candidate


class HealthTarget(TypedDict):
    """Typed structure describing a stack health check target."""

    name: str
    container: str
    port: int
    path: str
    mode: Literal["http", "exec"]


class ServiceCheck(TypedDict):
    """Typed structure for preflight service health checks."""

    name: str
    container: str
    port: int
    path: str
    timeout: float


HEALTH_TARGETS: list[HealthTarget] = [
    {
        "name": "searxng",
        "container": "searxng",
        "port": 8080,
        "path": "/",
        "mode": "http",
    },
    {
        "name": "qdrant",
        "container": "qdrant",
        "port": 6333,
        "path": "/healthz",
        "mode": "http",
    },
    {
        "name": "openwebui",
        "container": "openwebui",
        "port": 8080,
        "path": "/",
        "mode": "http",
    },
]


def _repo_root() -> Path:
    return tooling.resolve_repo_root()


def _compose_overrides(bind_mounts: bool) -> list[Path]:
    overrides: list[Path] = []
    if bind_mounts:
        override = _repo_root() / "docker-compose.bind.yml"
        if not override.exists():
            raise FileNotFoundError(f"Bind override not found: {override}")
        overrides.append(override)
    return overrides


def _wait_for_service(
    *,
    name: str,
    container: str,
    port: int,
    path: str,
    timeout: float,
    mode: Literal["http", "exec"] = "http",
) -> None:
    if mode == "http":
        mapped = tooling.get_mapped_port(container, port)
        url = f"http://localhost:{mapped}{path}"
        console.print(f"[cyan]Waiting for {name} at {url}[/cyan]")
        if not tooling.wait_http_ok(url, timeout):
            raise RuntimeError(f"{name} did not become healthy within {timeout} seconds")
    else:
        console.print(f"[cyan]Executing health check inside {container}[/cyan]")

        if name == "Context7":
            # Context7 returns 404 on root, so just check tcp connectivity
            command = f"nc -z localhost {port}"
        else:
            # Use --spider to check for existence without downloading (avoids hanging on streams)
            command = f"wget -q --spider http://localhost:{port}{path}"

        # Use compose exec to handle service names vs container names
        compose.run_compose(["exec", "-T", container, "sh", "-lc", command])


@app.command()
def up(
    detach: bool = typer.Option(True, help="Run services in the background.", show_default=True),
    build: bool = typer.Option(False, help="Build images before starting containers."),
    bind_mounts: bool = typer.Option(
        False,
        help="Include docker-compose.bind.yml overrides when starting the stack.",
    ),
    check_litellm: bool = typer.Option(
        False,
        help="Wait for LiteLLM health after bringing the stack up.",
    ),
    prod: bool = typer.Option(
        False,
        "--prod",
        help="Use production configuration (Traefik + SSL). Uses docker-compose.prod.yml.",
    ),
) -> None:
    """Start services defined in docker-compose.yml and confirm core health checks.

    For development (default): Uses docker-compose.yml + docker-compose.override.yml
    For production (--prod): Uses docker-compose.yml + docker-compose.prod.yml
    """
    tooling.ensure_docker()
    env = utils.load_environment()
    try:
        tooling.ensure_secrets(env)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    overrides = _compose_overrides(bind_mounts)

    if prod:
        env_label = "[bold magenta]PRODUCTION[/bold magenta]"
    else:
        env_label = "[bold cyan]DEVELOPMENT[/bold cyan]"
    console.print(f"[bold green]Starting stack ({env_label}) via docker compose…[/bold green]")
    compose.compose_up(detach=detach, build=build, extra_files=overrides, prod=prod)

    service_checks: list[ServiceCheck] = [
        {
            "name": "SearxNG",
            "container": "searxng",
            "port": 8080,
            "path": "/",
            "timeout": 60.0,
        },
        {
            "name": "Qdrant",
            "container": "qdrant",
            "port": 6333,
            "path": "/healthz",
            "timeout": 60.0,
        },
    ]
    for check in service_checks:
        _wait_for_service(
            name=check["name"],
            container=check["container"],
            port=check["port"],
            path=check["path"],
            timeout=check["timeout"],
        )

    if check_litellm:
        try:
            _wait_for_service(
                name="LiteLLM",
                container="litellm",
                port=4000,
                path="/health",
                timeout=120,
            )
        except RuntimeError as exc:  # pragma: no cover - advisory warning
            console.print(f"[yellow]{exc}[/yellow]")

    console.print("[cyan]Probing additional HTTP frontends…[/cyan]")
    _wait_for_service(
        name="SearxNG",
        container="searxng",
        port=8080,
        path="/",
        timeout=30,
    )
    _wait_for_service(
        name="Open WebUI",
        container="openwebui",
        port=8080,
        path="/",
        timeout=30,
    )

    status = compose.run_compose(["ps"], extra_files=overrides, prod=prod)
    console.print("[bold cyan]Stack is running. Current container status:[/bold cyan]")
    console.print(_ensure_text(status.stdout))


@app.command()
def down(
    remove_volumes: bool = typer.Option(
        False,
        help="Remove persistent volumes when stopping containers.",
        show_default=True,
    ),
    bind_mounts: bool = typer.Option(
        False,
        help="Include docker-compose.bind.yml overrides when stopping the stack.",
    ),
    prod: bool = typer.Option(
        False,
        "--prod",
        help="Stop production stack (uses docker-compose.prod.yml).",
    ),
) -> None:
    """Stop the running stack.

    For development (default): Uses docker-compose.yml + docker-compose.override.yml
    For production (--prod): Uses docker-compose.yml + docker-compose.prod.yml
    """
    tooling.ensure_docker()
    overrides = _compose_overrides(bind_mounts)
    if prod:
        env_label = "[bold magenta]PRODUCTION[/bold magenta]"
    else:
        env_label = "[bold cyan]DEVELOPMENT[/bold cyan]"
    console.print(f"[bold yellow]Stopping stack ({env_label})…[/bold yellow]")
    compose.compose_down(remove_volumes=remove_volumes, extra_files=overrides, prod=prod)
    console.print("[bold green]Stack stopped.[/bold green]")


@app.command()
def restart(
    build: bool = typer.Option(False, help="Build images before starting containers."),
    prod: bool = typer.Option(
        False,
        "--prod",
        help="Restart production stack (uses docker-compose.prod.yml).",
    ),
) -> None:
    """Restart the stack (stop then start).

    For development (default): Uses docker-compose.yml + docker-compose.override.yml
    For production (--prod): Uses docker-compose.yml + docker-compose.prod.yml
    """
    tooling.ensure_docker()
    if prod:
        env_label = "[bold magenta]PRODUCTION[/bold magenta]"
    else:
        env_label = "[bold cyan]DEVELOPMENT[/bold cyan]"
    console.print(f"[bold yellow]Restarting stack ({env_label})…[/bold yellow]")
    compose.compose_down(prod=prod)
    compose.compose_up(detach=True, build=build, prod=prod)
    console.print("[bold green]Stack restarted.[/bold green]")


# =============================================================================
# DEVELOPMENT ENVIRONMENT COMMANDS
# =============================================================================


def _connect_traefik_to_dev_network() -> None:
    """Connect Traefik to dev network for external routing.

    This allows Traefik (running in prod stack) to route traffic to dev containers.
    Silently ignored if Traefik is not running.
    """
    dev_network = "ai-agent-platform-dev_default"
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return

    result = subprocess.run(  # noqa: S603, S607
        [docker_bin, "network", "connect", dev_network, "traefik"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        console.print(f"[dim]Connected Traefik to {dev_network}[/dim]")
    elif "already exists" in result.stderr.lower():
        console.print(f"[dim]Traefik already connected to {dev_network}[/dim]")
    # Silently ignore other errors (Traefik not running, etc.)


@dev_app.command("up")
def dev_up(
    build: bool = typer.Option(False, help="Build images before starting containers."),
) -> None:
    """Start the development environment.

    Uses docker-compose.yml + docker-compose.dev.yml with isolated:
    - Project name (ai-agent-platform-dev)
    - Ports (3001, 8001, 5433, 6334, 4001, 8081)
    - Database volumes (postgres_data_dev)

    This allows running dev alongside production without conflicts.
    """
    tooling.ensure_docker()
    env = utils.load_environment()
    try:
        tooling.ensure_secrets(env)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print("[bold cyan]Starting DEVELOPMENT environment…[/bold cyan]")
    console.print("[dim]Project: ai-agent-platform-dev[/dim]")
    console.print("[dim]Open WebUI: http://localhost:3001[/dim]")
    console.print("[dim]Agent API:  http://localhost:8001[/dim]")
    compose.compose_up(detach=True, build=build, dev=True)

    # Connect Traefik to dev network for external routing (if Traefik is running)
    _connect_traefik_to_dev_network()

    # Show status
    status = compose.run_compose(["ps"], dev=True)
    console.print("[bold cyan]Development stack is running:[/bold cyan]")
    console.print(_ensure_text(status.stdout))


@dev_app.command("down")
def dev_down(
    remove_volumes: bool = typer.Option(
        False,
        "--volumes",
        "-v",
        help="Remove dev volumes (deletes dev database!).",
    ),
) -> None:
    """Stop the development environment.

    This only affects the dev stack (ai-agent-platform-dev).
    Production remains running.
    """
    tooling.ensure_docker()
    console.print("[bold yellow]Stopping DEVELOPMENT environment…[/bold yellow]")
    compose.compose_down(remove_volumes=remove_volumes, dev=True)
    console.print("[bold green]Development stack stopped.[/bold green]")


@dev_app.command("logs")
def dev_logs(
    service: list[str] | None = typer.Argument(
        None,
        help="Optional services to tail; defaults to all.",
    ),
    follow: bool = typer.Option(
        False,
        "--follow",
        "-f",
        help="Continue streaming logs.",
    ),
    tail: int = typer.Option(
        100,
        "--tail",
        "-t",
        help="Number of lines to show.",
    ),
) -> None:
    """View logs from the development environment."""
    tooling.ensure_docker()
    args = ["logs"]
    if follow:
        args.append("-f")
    if tail > 0:
        args.append(f"--tail={tail}")
    if service:
        args.extend(service)
    compose.run_compose(args, dev=True, capture_output=False)


@dev_app.command("status")
def dev_status() -> None:
    """Show status of the development environment."""
    tooling.ensure_docker()
    result = compose.run_compose(["ps"], dev=True)
    console.print("[bold cyan]Development environment status:[/bold cyan]")
    console.print(_ensure_text(result.stdout))


@dev_app.command("restart")
def dev_restart(
    build: bool = typer.Option(False, help="Build images before restarting."),
) -> None:
    """Restart the development environment."""
    tooling.ensure_docker()
    console.print("[bold yellow]Restarting DEVELOPMENT environment…[/bold yellow]")
    compose.compose_down(dev=True)
    compose.compose_up(detach=True, build=build, dev=True)

    # Connect Traefik to dev network for external routing (if Traefik is running)
    _connect_traefik_to_dev_network()

    console.print("[bold green]Development stack restarted.[/bold green]")


@dev_app.command("deploy")
def dev_deploy(
    service: list[str] | None = typer.Argument(
        None,
        help="Services to deploy (default: agent).",
    ),
    timeout: float = typer.Option(60.0, help="Health check timeout in seconds."),
) -> None:
    """Build and deploy to dev environment with health verification.

    Rebuilds the specified services (default: agent only) and waits for
    health checks to pass before reporting success.  This is the recommended
    command for deploying code changes to the dev environment.
    """
    tooling.ensure_docker()
    services = list(service) if service else ["agent"]

    console.print(f"[bold cyan]Building and deploying: {', '.join(services)}...[/bold cyan]")
    args = ["up", "-d", "--no-deps", "--build"] + services
    compose.run_compose(args, dev=True, capture_output=False)
    _connect_traefik_to_dev_network()

    # Health checks - use full dev container names for port mapping
    dev_project = "ai-agent-platform-dev"
    console.print("[cyan]Waiting for services to become healthy...[/cyan]")
    _wait_for_service(
        name="agent",
        container=f"{dev_project}-agent-1",
        port=8000,
        path="/healthz",
        timeout=timeout,
        mode="http",
    )
    _wait_for_service(
        name="openwebui",
        container=f"{dev_project}-open-webui-1",
        port=8080,
        path="/",
        timeout=timeout,
        mode="http",
    )

    console.print("[bold green]Dev deploy complete - all services healthy.[/bold green]")


# =============================================================================
# DEPLOYMENT COMMAND
# =============================================================================


@app.command()
def lint(
    fix: bool = typer.Option(
        True,
        "--fix/--no-fix",
        help="Auto-fix linting errors (default: yes).",
    ),
) -> None:
    """Run linting checks (Ruff + Black).

    This is the fast quality check - recommended for QA agents.
    Only runs formatters/linters, no type checking or tests.

    Example:
        stack lint           # Run with auto-fix (default)
        stack lint --no-fix  # Check only
    """
    checks.ensure_dependencies()
    results = checks.run_lint(fix=fix, repo_root=_repo_root())

    if all(r.success for r in results):
        console.print("[bold green]Linting passed.[/bold green]")
    else:
        failed = [r for r in results if not r.success]
        console.print(f"[bold red]Linting failed: {failed[0].name}[/bold red]")
        raise typer.Exit(code=1)


@app.command()
def typecheck() -> None:
    """Run Mypy type checker.

    Checks for type errors without running tests.
    Use this to see type issues separately from test failures.

    Example:
        stack typecheck
    """
    checks.ensure_dependencies()
    result = checks.run_mypy(repo_root=_repo_root())

    if result.success:
        console.print("[bold green]Type checking passed.[/bold green]")
    else:
        console.print("[bold red]Type checking failed.[/bold red]")
        raise typer.Exit(code=1)


@app.command()
def test(
    semantic: bool = typer.Option(
        False,
        "--semantic",
        help="Include semantic end-to-end tests (requires running agent).",
    ),
    semantic_category: str | None = typer.Option(
        None,
        "--semantic-category",
        "-sc",
        help="Run only semantic tests in this category.",
    ),
) -> None:
    """Run pytest test suite.

    Runs unit and integration tests. Use --semantic to also run
    end-to-end tests (requires a running agent).

    Example:
        stack test                           # Unit + integration tests
        stack test --semantic                # Include all e2e tests
        stack test --semantic-category routing  # Only routing e2e tests
    """
    checks.ensure_dependencies()
    result = checks.run_pytest(repo_root=_repo_root())

    if not result.success:
        console.print("[bold red]Tests failed.[/bold red]")
        raise typer.Exit(code=1)

    # Run semantic tests if requested (either flag or category implies semantic)
    if semantic or semantic_category:
        semantic_result = checks.run_semantic_tests(
            repo_root=_repo_root(),
            category=semantic_category,
        )
        if not semantic_result.success:
            console.print("[bold red]Semantic tests failed.[/bold red]")
            raise typer.Exit(code=1)

    console.print("[bold green]All tests passed.[/bold green]")


@app.command()
def check(
    fix: bool = typer.Option(
        True,
        "--fix/--no-fix",
        help="Auto-fix linting errors (default: yes).",
    ),
    semantic: bool = typer.Option(
        False,
        "--semantic",
        help="Include semantic end-to-end tests (requires running agent).",
    ),
    semantic_category: str | None = typer.Option(
        None,
        "--semantic-category",
        "-sc",
        help="Run only semantic tests in this category.",
    ),
) -> None:
    """Run all quality checks (ruff, black, mypy, pytest).

    This is the full quality gate that runs:
    1. Ruff - Linting with optional auto-fix
    2. Black - Code formatting with optional auto-fix
    3. Mypy - Type checking
    4. Pytest - Unit and integration tests

    Use --no-fix for CI-style check-only mode.
    Use --semantic to include end-to-end tests (requires running agent).

    Example:
        stack check                              # Full check with auto-fix
        stack check --no-fix                     # CI mode (no auto-fix)
        stack check --semantic                   # Include all e2e tests
        stack check --semantic-category routing  # Only routing e2e tests
    """
    checks.ensure_dependencies()

    # If semantic category is provided, it implies semantic=True
    include_semantic = semantic or (semantic_category is not None)

    results = checks.run_all_checks(
        fix=fix,
        include_semantic=include_semantic,
        semantic_category=semantic_category,
        repo_root=_repo_root(),
    )

    if all(r.success for r in results):
        console.print("[bold green]All quality checks passed.[/bold green]")
    else:
        failed = [r for r in results if not r.success]
        console.print(f"[bold red]Quality checks failed at: {failed[0].name}[/bold red]")
        raise typer.Exit(code=1)


@app.command()
def deploy(
    skip_checks: bool = typer.Option(
        False,
        "--skip-checks",
        help="Skip running quality checks before deploying.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force deploy even if not on main branch (dangerous!).",
    ),
    service: list[str] | None = typer.Argument(
        None,
        help="Specific services to rebuild (default: agent only).",
    ),
) -> None:
    """Deploy changes to production.

    This command:
    1. Verifies you're on the main branch (safety check)
    2. Runs quality checks (ruff, black, mypy, pytest)
    3. Rebuilds the agent container (or specified services)
    4. Restarts the production stack with zero downtime

    Typical workflow:
        git checkout main
        git merge feature/my-feature
        stack deploy
    """
    tooling.ensure_docker()
    repo_root = _repo_root()

    # Step 0: Branch safety check
    current_branch = tooling.current_branch(repo_root)
    if current_branch != "main":
        if force:
            console.print(
                f"[bold yellow]⚠ WARNING: Deploying from branch '{current_branch}' "
                f"(not main)[/bold yellow]"
            )
        else:
            console.print(f"[bold red]✗ Cannot deploy from branch '{current_branch}'[/bold red]")
            console.print("[yellow]Production deployments must be from 'main' branch.[/yellow]")
            console.print("")
            console.print("[dim]To deploy from main:[/dim]")
            console.print("  git checkout main")
            console.print("  git merge feature/your-branch")
            console.print("  stack deploy")
            console.print("")
            console.print("[dim]To force deploy anyway (dangerous!):[/dim]")
            console.print("  stack deploy --force")
            raise typer.Exit(code=1)

    # Step 1: Quality checks
    if not skip_checks:
        console.print("[bold cyan]Running quality checks…[/bold cyan]")
        try:
            _run_quality_checks(repo_root)
            console.print("[bold green]✓ All checks passed[/bold green]")
        except Exception as exc:
            console.print(f"[bold red]✗ Quality checks failed: {exc}[/bold red]")
            console.print("[yellow]Use --skip-checks to bypass (not recommended)[/yellow]")
            raise typer.Exit(code=1) from exc

    # Step 2: Determine services to rebuild
    services_to_build = list(service) if service else ["agent"]

    # Step 3: Build
    console.print(f"[bold cyan]Building: {', '.join(services_to_build)}…[/bold cyan]")
    build_args = ["build"] + services_to_build
    compose.run_compose(build_args, prod=True, capture_output=False)

    # Step 4: Rolling restart (recreate only changed containers)
    console.print("[bold cyan]Deploying to production…[/bold cyan]")
    up_args = ["up", "-d", "--no-deps"] + services_to_build
    compose.run_compose(up_args, prod=True, capture_output=False)

    # Step 5: Show status
    console.print("[bold green]✓ Deployment complete![/bold green]")
    result = compose.run_compose(["ps"], prod=True)
    console.print(_ensure_text(result.stdout))


@app.command()
def logs(
    service: list[str] | None = typer.Argument(
        None,
        help="Optional services to tail; defaults to core stack containers.",
    ),
    since: str = typer.Option(
        "5m",
        help="Time window passed to docker logs --since.",
    ),
    tail: int = typer.Option(
        100,
        "--tail",
        "-t",
        help="Number of lines to show from the end of the log (pass 0 to disable).",
        show_default=True,
    ),
    follow: bool = typer.Option(
        False,
        "--follow",
        "-f",
        help="Continue streaming logs until interrupted (not recommended for unattended actors).",
        show_default=True,
    ),
) -> None:
    """Tail container logs for the requested services."""

    tooling.ensure_docker()
    services = service or DEFAULT_LOG_SERVICES
    console.print(
        f"[cyan]Gathering logs (tail={tail}, follow={'on' if follow else 'off'}) for: "
        f"{', '.join(services)}[/cyan]"
    )
    tooling.tail_logs(services, since=since, tail=tail, follow=follow)


@app.command("health")
def health_check(
    service: str | None = typer.Argument(None, help="Optional service filter."),
) -> None:
    """Probe core services and exit non-zero when any check fails."""

    tooling.ensure_docker()
    targets = HEALTH_TARGETS
    if service:
        targets = [t for t in targets if t["name"] == service]
        if not targets:
            raise typer.BadParameter(f"Unknown service: {service}")

    table = Table()
    table.add_column("Service")
    table.add_column("Status")
    table.add_column("Detail")
    overall_ok = True

    for target in targets:
        name = target["name"]
        mode = target["mode"]
        if mode == "http":
            mapped = tooling.get_mapped_port(target["container"], target["port"])
            url = f"http://localhost:{mapped}{target['path']}"
            try:
                response = httpx.get(url, timeout=5.0)
            except Exception as exc:  # noqa: BLE001
                overall_ok = False
                table.add_row(name, "[red]error[/red]", str(exc))
            else:
                if 200 <= response.status_code < 300:
                    table.add_row(name, "[green]ok[/green]", str(response.status_code))
                else:
                    overall_ok = False
                    table.add_row(name, "[yellow]warn[/yellow]", f"HTTP {response.status_code}")
        else:
            try:
                # Use --spider to check for existence without downloading
                command = f"wget -q --spider http://localhost:{target['port']}{target['path']}"
                tooling.docker_exec(target["container"], "sh", "-lc", command, user=None)
                table.add_row(name, "[green]ok[/green]", "exec")
            except Exception as exc:  # noqa: BLE001
                overall_ok = False
                table.add_row(name, "[red]error[/red]", str(exc))

    console.print(table)
    if not overall_ok:
        raise typer.Exit(code=1)


@app.command("status")
def status_command() -> None:
    """Display the container status table."""

    health.render_status_table()


@repo_app.command("save")
def repo_save(
    message: str = typer.Option("chore: publish snapshot", help="Base commit message."),
    branch: str | None = typer.Option(
        None,
        "--branch",
        "-b",
        help="Branch to switch to or create before committing.",
    ),
) -> None:
    """Stage all changes and create a timestamped commit when needed."""

    repo_root = _repo_root()
    if not tooling.git_available():
        raise RuntimeError("git is required to snapshot the repository")

    git_dir = repo_root / ".git"
    if not git_dir.exists():
        console.print("[yellow]Initialising git repository…[/yellow]")
        tooling.run_command(
            ["git", "-c", "init.defaultBranch=main", "init"],
            cwd=repo_root,
        )

    git_printer = _console_git_printer

    if branch:
        requested_branch = _validate_feature_branch_name(branch)
    else:
        console.print("[yellow]Repository requires a feature branch for this snapshot.[/yellow]")
        requested_branch = _prompt_feature_branch_name()
    console.print(f"[cyan]Switching to branch {requested_branch}[/cyan]")
    tooling.ensure_branch(repo_root, requested_branch, printer=git_printer)
    working_branch = requested_branch

    compose_file = repo_root / "docker-compose.yml"
    if compose_file.exists():
        try:
            tooling.ensure_docker()
            console.print("[cyan]Validating docker-compose configuration…[/cyan]")
            compose.run_compose(["config"])
        except FileNotFoundError:
            console.print("[yellow]Docker not available; skipping compose validation.[/yellow]")

    committed = tooling.stage_and_commit(repo_root, message, printer=git_printer)
    if committed:
        console.print(f"[green]Saved changes on {working_branch}:[/green] {committed}")
        console.print(
            "[cyan]Run `poetry run stack repo push` to push the branch and "
            "`poetry run stack repo pr` to create the pull request.[/cyan]"
        )
    else:
        console.print("[cyan]No changes to commit.[/cyan]")


@repo_app.command("push")
def repo_push(
    remote: str = typer.Option("origin", help="Remote name to push to."),
    set_upstream: bool = typer.Option(
        True,
        help="Run `git push --set-upstream` so future pushes track the remote branch.",
    ),
) -> None:
    """Push the current branch to GitHub."""

    repo_root = _repo_root()
    if not tooling.git_available():
        raise RuntimeError("git is required to push branches")

    current = tooling.current_branch(repo_root)
    if not current or current == "HEAD":
        raise typer.BadParameter("No branch is currently checked out.")

    console.print(f"[cyan]Pushing branch {current} to {remote}[/cyan]")
    args = (
        ["push", "--set-upstream", remote, current] if set_upstream else ["push", remote, current]
    )
    tooling.run_git_command(
        args,
        repo_root=repo_root,
        printer=_console_git_printer,
        capture_output=False,
    )
    console.print("[green]Push complete.[/green]")


@repo_app.command("pr")
def repo_pr(
    base: str = typer.Option("main", help="Target branch for the pull request."),
    draft: bool = typer.Option(False, help="Create the PR as a draft."),
    title: str | None = typer.Option(None, help="Override the PR title."),
    body: str | None = typer.Option(None, help="Override the PR body."),
) -> None:
    """Open a pull request for the current branch using GitHub CLI."""

    repo_root = _repo_root()
    current = tooling.current_branch(repo_root)
    if not current or current == "HEAD":
        raise typer.BadParameter("No branch is currently checked out.")

    if shutil.which("gh") is None:
        raise RuntimeError(
            "GitHub CLI (`gh`) is required to create pull requests. Install it from https://github.com/cli/cli."
        )

    gh_args = ["pr", "create", "--base", base, "--head", current]
    if draft:
        gh_args.append("--draft")
    if title:
        gh_args.extend(["--title", title])
    if body:
        gh_args.extend(["--body", body])
    if not title and not body:
        gh_args.append("--fill")

    console.print(f"[cyan]gh {' '.join(gh_args)}[/cyan]")
    tooling.run_command(["gh", *gh_args], cwd=repo_root, capture_output=False)


@repo_app.command("publish")
def repo_publish(
    message: str = typer.Option("chore: publish snapshot", help="Base commit message."),
    branch: str | None = typer.Option(
        None,
        "--branch",
        "-b",
        help="Branch to switch to or create before committing.",
    ),
    remote: str = typer.Option("origin", help="Remote name to push to."),
    set_upstream: bool = typer.Option(
        True,
        help="Run `git push --set-upstream` so future pushes track the remote branch.",
    ),
    pr_base: str = typer.Option("main", help="Target branch for the pull request."),
    pr_draft: bool = typer.Option(False, help="Create the PR as a draft."),
    pr_title: str | None = typer.Option(None, help="Override the PR title."),
    pr_body: str | None = typer.Option(None, help="Override the PR body."),
    skip_checks: bool = typer.Option(
        False,
        "--skip-checks",
        help="Skip running the local quality checks.",
    ),
) -> None:
    """Save changes, push the branch, and open a pull request."""

    repo_root = _repo_root()
    if not skip_checks:
        _run_quality_checks(repo_root)

    repo_save(message=message, branch=branch)
    repo_push(remote=remote, set_upstream=set_upstream)
    repo_pr(base=pr_base, draft=pr_draft, title=pr_title, body=pr_body)


def _run_quality_checks(repo_root: Path) -> None:
    console.print("[cyan]Running local quality checks (ruff, black, mypy, pytest)...[/cyan]")
    checks.ensure_dependencies()
    results = checks.run_all_checks(fix=True, include_semantic=False, repo_root=repo_root)
    if not all(r.success for r in results):
        failed = [r for r in results if not r.success]
        raise tooling.CommandError(f"Quality check failed: {failed[0].name}")


@n8n_app.command("export")
def n8n_export(
    include_credentials: bool = typer.Option(
        False,
        help="Export credentials.json alongside workflows.",
    ),
    container: str = typer.Option("n8n", help="Docker container name."),
    flows_dir: Path = typer.Option(Path("flows"), help="Destination directory for exports."),
) -> None:
    """Export workflows from the running n8n container."""

    repo_root = _repo_root()
    tooling.ensure_container_exists(container)
    target_dir = (repo_root / flows_dir).resolve()
    tooling.ensure_directory(target_dir)

    tmp_export = "/home/node/n8n_export"
    console.print(f"[cyan]Preparing export directory {tmp_export}[/cyan]")
    cleanup_cmd = f"rm -rf {tmp_export} && mkdir -p {tmp_export}"
    tooling.docker_exec(container, "sh", "-lc", cleanup_cmd, user="0")
    tooling.docker_exec(container, "sh", "-lc", f"chown -R node:node {tmp_export}", user="0")

    console.print("[cyan]Running n8n export…[/cyan]")
    workflow_export_cmd = (
        "n8n export:workflow --all --pretty --separate " "--output '/home/node/n8n_export' || true"
    )
    tooling.docker_exec(container, "sh", "-lc", workflow_export_cmd)

    if include_credentials:
        console.print("[cyan]Exporting credentials (without decrypting secrets)…[/cyan]")
        credentials_export_cmd = (
            "n8n export:credentials --all --pretty --output "
            "'/home/node/n8n_export/credentials.json' || true"
        )
        tooling.docker_exec(container, "sh", "-lc", credentials_export_cmd)

    workflows_dest = target_dir / "workflows"
    if workflows_dest.exists():
        shutil.rmtree(workflows_dest)
    tooling.ensure_directory(workflows_dest)

    try:
        tooling.docker_cp(f"{container}:{tmp_export}", str(workflows_dest))
    except tooling.CommandError:
        console.print("[yellow]No workflows exported from the container.[/yellow]")

    exported = list(workflows_dest.rglob("*.json"))
    if exported:
        summary = f"Exported {len(exported)} workflow file(s) → {workflows_dest}"
        console.print(f"[green]{summary}[/green]")
    else:
        console.print(f"[yellow]No workflow JSON files found in {workflows_dest}.[/yellow]")

    combined_path = target_dir / "workflows.json"
    combined: list[dict[str, object]] = []
    for path in exported:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            combined.append(loaded)
    combined_path.write_text(json.dumps(combined, indent=2), encoding="utf-8")
    console.print(f"[green]Wrote combined export → {combined_path}[/green]")

    if include_credentials:
        creds_path = target_dir / "credentials.json"
        try:
            tooling.docker_cp(f"{container}:{tmp_export}/credentials.json", str(creds_path))
            console.print(f"[green]Exported credentials → {creds_path}[/green]")
        except tooling.CommandError:
            console.print("[yellow]No credentials exported.[/yellow]")


@n8n_app.command("import")
def n8n_import(
    include_credentials: bool = typer.Option(
        False,
        help="Import credentials.json when present.",
    ),
    container: str = typer.Option("n8n", help="Docker container name."),
    flows_dir: Path = typer.Option(Path("flows"), help="Source directory for imports."),
) -> None:
    """Import workflows from the repository into the running n8n container."""

    repo_root = _repo_root()
    tooling.ensure_container_exists(container)
    source_root = (repo_root / flows_dir).resolve()
    workflows_dir = source_root / "workflows"
    import_source = workflows_dir if workflows_dir.exists() else source_root
    json_files = list(import_source.rglob("*.json"))
    if not json_files:
        raise RuntimeError(f"No JSON workflows found under {import_source}")

    tmp_import = "/home/node/n8n_import"
    console.print(f"[cyan]Preparing import directory {tmp_import}[/cyan]")
    prep_cmd = f"rm -rf {tmp_import} && mkdir -p {tmp_import}"
    tooling.docker_exec(container, "sh", "-lc", prep_cmd, user="0")
    tooling.docker_exec(container, "sh", "-lc", f"chown -R node:node {tmp_import}", user="0")

    console.print(f"[cyan]Copying workflows from {import_source}…[/cyan]")
    tooling.docker_cp(str(import_source), f"{container}:{tmp_import}")
    tooling.docker_exec(container, "sh", "-lc", f"chown -R node:node {tmp_import}", user="0")
    flatten_cmd = (
        "if [ -d '/home/node/n8n_import/workflows' ]; then "
        "mv /home/node/n8n_import/workflows/* /home/node/n8n_import/ && "
        "rmdir /home/node/n8n_import/workflows; "
        "fi"
    )
    tooling.docker_exec(container, "sh", "-lc", flatten_cmd, user="0")

    import_cmd = (
        "if [ -d '/home/node/n8n_import/export' ]; then "
        "n8n import:workflow --separate --input '/home/node/n8n_import/export'; "
        "else "
        "n8n import:workflow --separate --input '/home/node/n8n_import'; "
        "fi"
    )
    tooling.docker_exec(container, "sh", "-lc", import_cmd)
    console.print("[green]Workflow import completed.[/green]")

    if include_credentials:
        credentials_file = source_root / "credentials.json"
        if credentials_file.exists():
            console.print(f"[cyan]Importing credentials from {credentials_file}…[/cyan]")
            tooling.docker_cp(str(credentials_file), f"{container}:{tmp_import}/credentials.json")
            chown_cmd = "chown node:node /home/node/n8n_import/credentials.json"
            tooling.docker_exec(container, "sh", "-lc", chown_cmd, user="0")
            credentials_import_cmd = (
                "n8n import:credentials --input " "'/home/node/n8n_import/credentials.json'"
            )
            tooling.docker_exec(container, "sh", "-lc", credentials_import_cmd)
            console.print("[green]Credentials import completed.[/green]")
        else:
            console.print(
                "[yellow]No credentials.json found; skipping credentials import.[/yellow]"
            )


@openwebui_app.command("export")
def openwebui_export(
    compose_file: Path = typer.Option(
        Path("docker-compose.yml"),
        help="Compose file controlling Open WebUI.",
    ),
    service: str = typer.Option("openwebui", help="Docker Compose service name."),
    dump_path: Path = typer.Option(
        Path("openwebui/export/app.db.sql"),
        help="Destination SQL dump.",
    ),
) -> None:
    """Dump the Open WebUI SQLite database via docker compose exec."""

    tooling.ensure_docker()
    repo_root = _repo_root()
    compose_path = compose_file if compose_file.is_absolute() else (repo_root / compose_file)
    dump_path = (repo_root / dump_path).resolve()
    tooling.ensure_directory(dump_path.parent)

    python_script = """
import sqlite3, sys, pathlib
db_path = pathlib.Path('/app/backend/data/app.db')
conn = sqlite3.connect(db_path)
try:
    for line in conn.iterdump():
        sys.stdout.write(f"{line}\n")
finally:
    conn.close()
"""

    exec_script = f"python - <<'PY'\n{python_script}\nPY"
    exec_args = [
        "exec",
        "-T",
        service,
        "sh",
        "-lc",
        exec_script,
    ]
    result = compose.run_compose(exec_args, files_override=[compose_path])
    dump_path.write_text(_ensure_text(result.stdout), encoding="utf-8")
    console.print(f"[green]Exported Open WebUI database → {dump_path}[/green]")


@openwebui_app.command("import")
def openwebui_import(
    compose_file: Path = typer.Option(
        Path("docker-compose.yml"),
        help="Compose file controlling Open WebUI.",
    ),
    service: str = typer.Option("openwebui", help="Docker Compose service name."),
    dump_path: Path = typer.Option(
        Path("openwebui/export/app.db.sql"),
        help="Source SQL dump.",
    ),
) -> None:
    """Restore the Open WebUI SQLite database inside the container."""

    tooling.ensure_docker()
    repo_root = _repo_root()
    resolved_dump = (repo_root / dump_path).resolve()
    if not resolved_dump.exists():
        raise FileNotFoundError(f"Dump file not found: {resolved_dump}")

    compose_path = compose_file if compose_file.is_absolute() else (repo_root / compose_file)

    container_target = f"{service}:/tmp/openwebui.sql"
    compose.run_compose(["cp", str(resolved_dump), container_target], files_override=[compose_path])

    python_script = """
import os, sqlite3, pathlib
db_path = pathlib.Path('/app/backend/data/app.db')
tmp_path = pathlib.Path('/tmp/openwebui.sql')
if not tmp_path.exists():
    raise SystemExit('Temporary SQL dump missing inside container')
db_path.parent.mkdir(parents=True, exist_ok=True)
if db_path.exists():
    os.remove(db_path)
with tmp_path.open('r', encoding='utf-8') as handle:
    sql = handle.read()
conn = sqlite3.connect(db_path)
try:
    conn.executescript(sql)
finally:
    conn.close()
os.remove(tmp_path)
"""

    exec_script = f"python - <<'PY'\n{python_script}\nPY"
    exec_args = [
        "exec",
        "-T",
        service,
        "sh",
        "-lc",
        exec_script,
    ]
    compose.run_compose(exec_args, files_override=[compose_path])
    console.print(f"[green]Imported Open WebUI database from {resolved_dump}[/green]")


__all__ = ["app"]
