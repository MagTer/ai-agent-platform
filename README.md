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
workflows. `python -m stack` wraps Docker Compose, merges environment variables
from `.env`, and surfaces container health via Rich tables.

| Command | Description |
|---------|-------------|
| `python -m stack up` | Start or restart the stack in detached mode. |
| `python -m stack down` | Stop the stack (safe to run multiple times). |
| `python -m stack status` | Render container status and health checks. |
| `python -m stack logs openwebui --tail 100` | Tail logs for selected services. |

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
