from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

import httpx
import typer
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from stack import auth, checks, compose, health, qdrant, tooling, utils

console = Console()
app = typer.Typer(help="Manage the local AI agent platform stack.")
dev_app = typer.Typer(help="Development environment commands (isolated from production).")
repo_app = typer.Typer(help="Repository snapshot utilities.")
n8n_app = typer.Typer(help="Import or export n8n workflows.")
openwebui_app = typer.Typer(help="Manage Open WebUI database exports and restores.")
db_app = typer.Typer(help="Database migration commands.")
app.add_typer(dev_app, name="dev")
app.add_typer(repo_app, name="repo")
app.add_typer(n8n_app, name="n8n")
app.add_typer(openwebui_app, name="openwebui")
app.add_typer(db_app, name="db")
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
    },
    {
        "name": "qdrant",
        "container": "qdrant",
        "port": 6333,
        "path": "/healthz",
    },
    {
        "name": "openwebui",
        "container": "openwebui",
        "port": 8080,
        "path": "/",
    },
]


def _repo_root() -> Path:
    return tooling.resolve_repo_root()


def _stack_dir() -> Path:
    """Get or create the .stack directory for deployment metadata."""
    stack_dir = _repo_root() / ".stack"
    stack_dir.mkdir(exist_ok=True)
    return stack_dir


def _deployments_file() -> Path:
    """Get the path to the deployments history file."""
    return _stack_dir() / "deployments.json"


def _record_deployment(branch: str, services: list[str]) -> None:
    """Record a deployment in the deployment history.

    Keeps the last 10 deployments.
    """
    deployments_file = _deployments_file()
    deployments: list[dict[str, object]] = []

    if deployments_file.exists():
        try:
            deployments = json.loads(deployments_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            deployments = []

    deployment: dict[str, object] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "branch": branch,
        "services": services,
        "image_tag": "previous",
    }
    deployments.append(deployment)

    # Keep last 10 deployments
    deployments = deployments[-10:]

    deployments_file.write_text(json.dumps(deployments, indent=2), encoding="utf-8")


def _dev_deployments_file() -> Path:
    """Get the path to the dev deployments history file."""
    return _stack_dir() / "dev-deployments.json"


def _record_dev_deployment(services: list[str]) -> None:
    """Record a dev deployment in the dev deployment history.

    Keeps the last 20 entries (dev deploys are more frequent).
    """
    deployments_file = _dev_deployments_file()
    deployments: list[dict[str, object]] = []

    if deployments_file.exists():
        try:
            deployments = json.loads(deployments_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            deployments = []

    repo_root = _repo_root()
    branch = tooling.current_branch(repo_root) or "unknown"

    deployment: dict[str, object] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "branch": branch,
        "services": services,
        "environment": "development",
    }
    deployments.append(deployment)

    # Keep last 20 entries (dev deploys are more frequent)
    deployments = deployments[-20:]

    deployments_file.write_text(json.dumps(deployments, indent=2), encoding="utf-8")


def _tag_current_image() -> None:
    """Tag the current agent image as 'previous' for rollback capability."""
    docker_bin = shutil.which("docker")
    if not docker_bin:
        console.print("[yellow]Docker not found, skipping image tagging.[/yellow]")
        return

    # Get current image ID
    get_image_id = subprocess.run(  # noqa: S603
        [docker_bin, "images", "ai-agent-platform-agent:latest", "-q"],
        capture_output=True,
        text=True,
        check=False,
    )

    if get_image_id.returncode != 0 or not get_image_id.stdout.strip():
        console.print("[dim]No existing agent image found, skipping tagging.[/dim]")
        return

    image_id = get_image_id.stdout.strip()
    console.print(f"[dim]Tagging current image ({image_id[:12]}) as 'previous'...[/dim]")

    # Tag the current image as previous
    tag_result = subprocess.run(  # noqa: S603
        [
            docker_bin,
            "tag",
            "ai-agent-platform-agent:latest",
            "ai-agent-platform-agent:previous",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if tag_result.returncode != 0:
        console.print(f"[yellow]Warning: Failed to tag image: {tag_result.stderr.strip()}[/yellow]")
    else:
        console.print("[dim]Image tagged successfully.[/dim]")


def _wait_for_prod_services(project_name: str, timeout: float = 60) -> None:
    """Wait for production services to become healthy using docker exec.

    In prod mode, ports aren't exposed to the host (Traefik handles routing),
    so we exec into each container to check health.
    """
    import time as _time

    checks = [
        ("SearxNG", f"{project_name}-searxng-1", 8080, "/"),
        ("Qdrant", f"{project_name}-qdrant-1", 6333, "/healthz"),
        ("Agent", f"{project_name}-agent-1", 8000, "/healthz"),
        ("Open WebUI", f"{project_name}-open-webui-1", 8080, "/"),
    ]
    for name, container, port, path in checks:
        console.print(f"[cyan]Waiting for {name} ({container})…[/cyan]")
        deadline = _time.monotonic() + timeout
        healthy = False
        while _time.monotonic() < deadline:
            try:
                tooling.docker_exec(
                    container,
                    "sh",
                    "-lc",
                    f"curl -sf --max-time 3 http://localhost:{port}{path} > /dev/null",
                )
                healthy = True
                break
            except Exception:  # noqa: BLE001
                _time.sleep(2.0)
        if healthy:
            console.print(f"[green]{name}: healthy[/green]")
        else:
            console.print(f"[yellow]{name}: not healthy after {timeout}s[/yellow]")

    # Verify Open WebUI -> Agent connectivity
    if _verify_openwebui_to_agent(project_name):
        console.print("[green]Open WebUI -> Agent connectivity verified.[/green]")
    else:
        console.print("[yellow]Warning: Open WebUI cannot reach Agent.[/yellow]")


def _verify_openwebui_to_agent(project_name: str) -> bool:
    """Check that Open WebUI can reach the agent's /healthz endpoint.

    Uses ``docker exec`` to run curl inside the Open WebUI container,
    verifying end-to-end connectivity.
    """
    container = f"{project_name}-open-webui-1"
    try:
        tooling.docker_exec(
            container,
            "sh",
            "-lc",
            "curl -sf --max-time 5 http://agent:8000/healthz > /dev/null",
        )
        return True
    except Exception:  # noqa: BLE001
        return False


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
) -> None:
    mapped = tooling.get_mapped_port(container, port)
    url = f"http://localhost:{mapped}{path}"
    console.print(f"[cyan]Waiting for {name} at {url}[/cyan]")
    if not tooling.wait_http_ok(url, timeout):
        raise RuntimeError(f"{name} did not become healthy within {timeout} seconds")


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

    if prod:
        # In prod mode, ports aren't exposed to host (Traefik handles routing).
        # Use exec-based health checks inside the containers instead.
        _wait_for_prod_services("ai-agent-platform-prod")
    else:
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

    if remove_volumes:
        typer.confirm(
            "This will remove all persistent volumes (database, Qdrant, etc.). Continue?",
            abort=True,
        )

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
    all_services: bool = typer.Option(
        False,
        "--all",
        help="Rebuild and recreate all services (use when .env changes affect multiple services).",
    ),
    timeout: float = typer.Option(60.0, help="Health check timeout in seconds."),
) -> None:
    """Build and deploy to dev environment with health verification.

    Rebuilds the specified services (default: agent only) and waits for
    health checks to pass before reporting success.  This is the recommended
    command for deploying code changes to the dev environment.

    Use --all when .env changes affect Open WebUI or other services.
    """
    tooling.ensure_docker()
    services = list(service) if service else ["agent"]

    if all_services:
        console.print("[bold cyan]Building and deploying ALL services...[/bold cyan]")
        args = ["up", "-d", "--build"]
    else:
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
    )
    _wait_for_service(
        name="openwebui",
        container=f"{dev_project}-open-webui-1",
        port=8080,
        path="/",
        timeout=timeout,
    )

    # Verify Open WebUI -> Agent connectivity
    if _verify_openwebui_to_agent(dev_project):
        console.print("[green]Open WebUI -> Agent connectivity verified.[/green]")
    else:
        console.print(
            "[yellow]Warning: Open WebUI cannot reach Agent. "
            "Try: ./stack dev deploy --all[/yellow]"
        )

    # Record dev deployment
    _record_dev_deployment(services)

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
    skip_architecture: bool = typer.Option(
        False,
        "--skip-architecture",
        help="Skip architecture validation (not recommended).",
    ),
    update_baseline: bool = typer.Option(
        False,
        "--update-baseline",
        help="Update architecture baseline with current violations.",
    ),
) -> None:
    """Run all quality checks (architecture, ruff, black, mypy, pytest).

    This is the full quality gate that runs:
    1. Architecture - Validate 4-layer architecture rules
    2. Ruff - Linting with optional auto-fix
    3. Black - Code formatting with optional auto-fix
    4. Mypy - Type checking
    5. Pytest - Unit and integration tests

    Use --no-fix for CI-style check-only mode.
    Use --semantic to include end-to-end tests (requires running agent).
    Use --skip-architecture to bypass architecture validation (not recommended).
    Use --update-baseline to accept current architecture violations as baseline.

    Example:
        stack check                              # Full check with auto-fix
        stack check --no-fix                     # CI mode (no auto-fix)
        stack check --semantic                   # Include all e2e tests
        stack check --semantic-category routing  # Only routing e2e tests
        stack check --skip-architecture          # Skip architecture check (temporary)
        stack check --update-baseline            # Accept current violations as baseline
    """
    checks.ensure_dependencies()

    # If semantic category is provided, it implies semantic=True
    include_semantic = semantic or (semantic_category is not None)

    results = checks.run_all_checks(
        fix=fix,
        include_semantic=include_semantic,
        semantic_category=semantic_category,
        skip_architecture=skip_architecture,
        update_baseline=update_baseline,
        repo_root=_repo_root(),
    )

    if all(r.success for r in results):
        if update_baseline:
            console.print("[bold green]Baseline updated successfully.[/bold green]")
        else:
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
            _run_quality_checks(repo_root, skip_architecture=False)
            console.print("[bold green]✓ All checks passed[/bold green]")
        except Exception as exc:
            console.print(f"[bold red]✗ Quality checks failed: {exc}[/bold red]")
            console.print("[yellow]Use --skip-checks to bypass (not recommended)[/yellow]")
            raise typer.Exit(code=1) from exc

    # Step 2: Determine services to rebuild
    services_to_build = list(service) if service else ["agent"]

    # Step 2.5: Tag current image for rollback capability
    if "agent" in services_to_build:
        _tag_current_image()

    # Step 3: Build
    console.print(f"[bold cyan]Building: {', '.join(services_to_build)}…[/bold cyan]")
    build_args = ["build"] + services_to_build
    compose.run_compose(build_args, prod=True, capture_output=False)

    # Step 4: Rolling restart (recreate only changed containers)
    console.print("[bold cyan]Deploying to production…[/bold cyan]")
    up_args = ["up", "-d", "--no-deps"] + services_to_build
    compose.run_compose(up_args, prod=True, capture_output=False)

    # Step 4.5: Post-deploy health check
    console.print("[bold cyan]Verifying deployment health…[/bold cyan]")
    if not tooling.wait_http_ok("http://localhost:8000/health", timeout=30):
        console.print("[bold red]⚠ Health check failed after deploy![/bold red]")
        console.print("[yellow]Service may still be starting. Check: stack logs agent[/yellow]")
    else:
        console.print("[bold green]✓ Health check passed[/bold green]")

    # Step 4.6: Verify Open WebUI -> Agent connectivity
    prod_project = "ai-agent-platform"
    if _verify_openwebui_to_agent(prod_project):
        console.print("[bold green]✓ Open WebUI -> Agent connectivity verified[/bold green]")
    else:
        console.print(
            "[yellow]⚠ Open WebUI cannot reach Agent. "
            "Consider full restart: ./stack down && ./stack up --prod[/yellow]"
        )

    # Step 5: Record deployment
    # current_branch is guaranteed to be not None here due to early exit check
    assert current_branch is not None
    _record_deployment(current_branch, services_to_build)

    # Step 6: Show status
    console.print("[bold green]✓ Deployment complete![/bold green]")
    result = compose.run_compose(["ps"], prod=True)
    console.print(_ensure_text(result.stdout))


@app.command()
def rollback(
    prod: bool = typer.Option(
        True,
        "--prod/--no-prod",
        help="Rollback production deployment (default: yes).",
    ),
) -> None:
    """Rollback to the previous Docker image.

    This command restores the agent container to the previously deployed image.
    The previous image is tagged during deployment via `stack deploy`.

    WARNING: This only rolls back the Docker image, not database migrations.
    Use `stack db rollback` separately if needed.

    Typical workflow:
        stack rollback          # Rollback production
    """
    tooling.ensure_docker()

    if not prod:
        console.print("[red]Rollback is only supported for production deployments.[/red]")
        console.print("[yellow]Use --prod flag or deploy from a different branch.[/yellow]")
        raise typer.Exit(code=1)

    docker_bin = shutil.which("docker")
    if not docker_bin:
        console.print("[red]Docker not found.[/red]")
        raise typer.Exit(code=1)

    # Check if previous image exists
    check_image = subprocess.run(  # noqa: S603
        [docker_bin, "images", "ai-agent-platform-agent:previous", "-q"],
        capture_output=True,
        text=True,
        check=False,
    )

    if check_image.returncode != 0 or not check_image.stdout.strip():
        console.print("[red]No previous image found to rollback to.[/red]")
        console.print("[yellow]Deploy at least once to enable rollback functionality.[/yellow]")
        raise typer.Exit(code=1)

    image_id = check_image.stdout.strip()
    console.print(f"[cyan]Found previous image: {image_id[:12]}[/cyan]")
    console.print("[bold yellow]Rolling back production deployment...[/bold yellow]")

    # Tag previous as latest
    tag_result = subprocess.run(  # noqa: S603
        [docker_bin, "tag", "ai-agent-platform-agent:previous", "ai-agent-platform-agent:latest"],
        capture_output=True,
        text=True,
        check=False,
    )

    if tag_result.returncode != 0:
        console.print(f"[red]Failed to tag image: {tag_result.stderr.strip()}[/red]")
        raise typer.Exit(code=1)

    # Restart agent service
    console.print("[cyan]Restarting agent service...[/cyan]")
    compose.run_compose(["up", "-d", "--no-deps", "agent"], prod=True, capture_output=False)

    # Show status
    console.print("[bold green]✓ Rollback complete![/bold green]")
    result = compose.run_compose(["ps", "agent"], prod=True)
    console.print(_ensure_text(result.stdout))

    console.print("")
    console.print("[yellow]Note: This only rolled back the Docker image.[/yellow]")
    console.print("[yellow]If database migrations were applied, use `stack db rollback`.[/yellow]")


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
        _run_quality_checks(repo_root, skip_architecture=False)

    repo_save(message=message, branch=branch)
    repo_push(remote=remote, set_upstream=set_upstream)
    repo_pr(base=pr_base, draft=pr_draft, title=pr_title, body=pr_body)


def _run_quality_checks(repo_root: Path, *, skip_architecture: bool = False) -> None:
    if skip_architecture:
        msg = "Running local quality checks (ruff, black, mypy, pytest)..."
    else:
        msg = "Running local quality checks (architecture, ruff, black, mypy, pytest)..."
    console.print(f"[cyan]{msg}[/cyan]")
    checks.ensure_dependencies()
    results = checks.run_all_checks(
        fix=True,
        include_semantic=False,
        skip_architecture=skip_architecture,
        update_baseline=False,
        repo_root=repo_root,
    )
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

    if not Confirm.ask(
        "[yellow]This will import workflows into n8n. Continue?[/yellow]",
        default=False,
    ):
        console.print("[dim]Import cancelled.[/dim]")
        return

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

    if not Confirm.ask(
        "[yellow]This will REPLACE the current Open WebUI database. Continue?[/yellow]",
        default=False,
    ):
        console.print("[dim]Import cancelled.[/dim]")
        return

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


# =============================================================================
# DATABASE COMMANDS
# =============================================================================


@db_app.command("migrate")
def db_migrate(
    prod: bool = typer.Option(
        False,
        "--prod",
        help="Run migration in production stack.",
    ),
    dev: bool = typer.Option(
        False,
        "--dev",
        help="Run migration in development stack.",
    ),
) -> None:
    """Run Alembic migrations to upgrade the database to the latest version.

    This executes `alembic upgrade head` inside the running agent container.
    Database migrations are applied automatically on container startup.
    """
    tooling.ensure_docker()
    if prod:
        env_label = "[bold magenta]PRODUCTION[/bold magenta]"
    elif dev:
        env_label = "[bold cyan]DEVELOPMENT[/bold cyan]"
    else:
        env_label = "[bold]BASE[/bold]"

    console.print(f"[cyan]Running database migrations ({env_label})...[/cyan]")
    compose.run_compose(
        ["exec", "-T", "agent", "alembic", "upgrade", "head"],
        prod=prod,
        dev=dev,
        capture_output=False,
    )
    console.print("[bold green]Database migration complete.[/bold green]")


@db_app.command("status")
def db_status(
    prod: bool = typer.Option(
        False,
        "--prod",
        help="Check status in production stack.",
    ),
    dev: bool = typer.Option(
        False,
        "--dev",
        help="Check status in development stack.",
    ),
) -> None:
    """Show the current database migration revision.

    This executes `alembic current` inside the running agent container.
    """
    tooling.ensure_docker()
    if prod:
        env_label = "[bold magenta]PRODUCTION[/bold magenta]"
    elif dev:
        env_label = "[bold cyan]DEVELOPMENT[/bold cyan]"
    else:
        env_label = "[bold]BASE[/bold]"

    console.print(f"[cyan]Checking database status ({env_label})...[/cyan]")
    compose.run_compose(
        ["exec", "-T", "agent", "alembic", "current"],
        prod=prod,
        dev=dev,
        capture_output=False,
    )


@db_app.command("rollback")
def db_rollback(
    prod: bool = typer.Option(
        False,
        "--prod",
        help="Rollback in production stack.",
    ),
    dev: bool = typer.Option(
        False,
        "--dev",
        help="Rollback in development stack.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompt.",
    ),
) -> None:
    """Rollback the last database migration.

    This executes `alembic downgrade -1` inside the running agent container.
    WARNING: This can result in data loss if the migration modified schema.
    """
    tooling.ensure_docker()
    if prod:
        env_label = "[bold magenta]PRODUCTION[/bold magenta]"
    elif dev:
        env_label = "[bold cyan]DEVELOPMENT[/bold cyan]"
    else:
        env_label = "[bold]BASE[/bold]"

    if not yes:
        response = input(f"This will rollback the last migration in {env_label}. Continue? [y/N]: ")
        if response.strip().lower() != "y":
            console.print("[yellow]Rollback cancelled.[/yellow]")
            return

    console.print(f"[cyan]Rolling back database migration ({env_label})...[/cyan]")
    compose.run_compose(
        ["exec", "-T", "agent", "alembic", "downgrade", "-1"],
        prod=prod,
        dev=dev,
        capture_output=False,
    )
    console.print("[bold green]Database rollback complete.[/bold green]")


@db_app.command("history")
def db_history(
    prod: bool = typer.Option(
        False,
        "--prod",
        help="Show history from production stack.",
    ),
    dev: bool = typer.Option(
        False,
        "--dev",
        help="Show history from development stack.",
    ),
) -> None:
    """Show the migration history.

    This executes `alembic history --verbose` inside the running agent container.
    """
    tooling.ensure_docker()
    if prod:
        env_label = "[bold magenta]PRODUCTION[/bold magenta]"
    elif dev:
        env_label = "[bold cyan]DEVELOPMENT[/bold cyan]"
    else:
        env_label = "[bold]BASE[/bold]"

    console.print(f"[cyan]Showing migration history ({env_label})...[/cyan]")
    compose.run_compose(
        ["exec", "-T", "agent", "alembic", "history", "--verbose"],
        prod=prod,
        dev=dev,
        capture_output=False,
    )


__all__ = ["app"]
