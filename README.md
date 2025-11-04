# AI Agent Platform (Local, Containerized)

> **For AI assistants (Codex) — Start here.**
> All project documentation lives under [`/docs`](./docs). This root README links the essentials so you can locate **vision, constraints, delivery model, roadmap, capabilities, architecture, operations, and runbooks** in one click.

---

## Quick Start (Local)

```bash
# 1) Copy the environment template and customise as needed
cp .env.template .env

# 2) Install Python dependencies
poetry install

# 3) Launch the stack (idempotent)
python -m stack up

# 4) Check container status
python -m stack status
```

> To run with GPU acceleration or bind-mounted data directories, append
> overrides such as `docker-compose.gpu.yml` or `compose/docker-compose.bind.yml`
> when using `docker compose` directly (for example,
> `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d`).

After the stack reports healthy, open [http://localhost:3000](http://localhost:3000)
for Open WebUI. The UI is wired to the agent by default, so every prompt is
posted to `/v1/chat/completions` on the agent service (which in turn calls
LiteLLM and Ollama). Responses are structured with `steps`, `response`, and
`metadata`, giving the UI a full reasoning trace. You can also exercise the
JSON-native API directly at [http://localhost:8000/v1/agent](http://localhost:8000/v1/agent).

> Linux/macOS users can run the same commands from their shell. Windows users
> should run them inside a Poetry shell (`poetry shell`).

## Stack CLI Summary

The new Python-based orchestration replaces legacy PowerShell scripts and the n8n
workflows. `poetry run stack` (or `python -m stack`) wraps Docker Compose,
merges environment variables from `.env`, and surfaces container health via
Rich tables.

| Command | Description |
|---------|-------------|
| `poetry run stack up` | Start or restart the stack in detached mode. |
| `poetry run stack down` | Stop the stack (safe to run multiple times). |
| `poetry run stack status` | Render container status and health checks. |
| `poetry run stack logs openwebui --tail 100` | Tail logs for selected services. |

## Automation Utilities

Cross-platform Typer scripts live under [`scripts/`](./scripts) and replace the
PowerShell helpers shipped in earlier revisions. The canonical entrypoint is the
installed `stack` CLI (`poetry run stack …`), with thin wrappers kept in
`scripts/` for backwards compatibility.

| Task | Command | Notes |
|------|---------|-------|
| Bring the stack up (waits for health + models) | `poetry run stack up` | Flags: `--check-litellm`, `--build`, `--bind-mounts`. (`python scripts/stack_tools.py` wraps the same command.) |
| Stop the stack | `poetry run stack down` | Add `--remove-volumes` to purge data. |
| Probe service health | `poetry run stack health [service]` | Fails fast when any target is unhealthy. |
| Tail logs | `poetry run stack logs [service …]` | Defaults to the core containers. |
| Snapshot the repo | `poetry run stack repo save` | Validates Compose config then commits with a timestamp. |
| Export/import n8n workflows | `poetry run stack n8n export` / `import` | Supports `--include-credentials`. |
| Export/import Open WebUI DB | `poetry run stack openwebui export` / `import` | Dumps/restores `app.db` via Docker Compose. |
| Ensure Qdrant schema | `poetry run stack qdrant ensure-schema` | Mirrors `Qdrant-EnsureSchema.ps1`. |
| Backup/restore Qdrant | `poetry run stack qdrant backup` / `restore` | Archives `/qdrant/storage` with tar. |

> The stack CLI honours the `STACK_COMPOSE_FILES` environment variable. Set it to a
> path-separated list (e.g., `STACK_COMPOSE_FILES=docker-compose.gpu.yml`) to layer
> overrides such as GPU acceleration when running `python -m stack`.

## Services

| Service | Purpose |
|---------|---------|
| `agent` | FastAPI agent server with LiteLLM + Qdrant integrations. |
| `openwebui` | Web interface proxied through the agent service. |
| `litellm` | Gateway that fans out to Ollama and optional remote models. |
| `ollama` | Local GPU-backed inference runtime. |
| `qdrant` | Vector memory for long-term recall. |
| `embedder` | Sentence-transformer API powering RAG pipelines. |
| `ragproxy` | Retrieval-aware proxy that augments `rag/` chat models. |
| `webfetch` | Headless fetch service exposed to agent tools. |
| `searxng` | Optional metasearch backend for federated search. |
| `n8n` | Automation/workflow engine for advanced integrations. |

## Development Workflow

1. Use Poetry for dependency management (`poetry install`).
2. Before committing, run `poetry run python scripts/code_check.py` to execute the
   same Ruff, Black, mypy, and pytest checks that CI enforces (Ruff and Black will
   auto-fix issues locally).
3. Optionally install the bundled pre-commit hooks with `poetry run pre-commit install`
   to run the formatters and type checker automatically on each commit.
4. Follow the architecture documentation under [`docs/architecture`](./docs/architecture)
   for detailed module overviews, diagrams, and CI guidance.
5. Submit changes via feature branches (e.g., `feature/python-refactor`).

For deeper operational or architectural detail, start with
[`docs/architecture/README.md`](./docs/architecture/README.md).
