# Architecture

The platform is centred on a Python agent service that coordinates LiteLLM, Ollama, Qdrant, and Open WebUI. The legacy n8n orchestrator has been removed in favour of a FastAPI application with an explicit tool layer and a Typer-based stack CLI.

## Service Map
- **agent** – FastAPI service (`src/agent/`) exposing `/healthz`, `/v1/agent`,
  and an OpenAI-compatible `/v1/chat/completions` endpoint that orchestrates
  memory, tools, and LiteLLM calls while returning structured responses
  (`steps`, `response`, `metadata`).
- **stack CLI** – `python -m stack <command>` controls Docker Compose lifecycle, health checks, and log streaming.
- **litellm** – LLM gateway that proxies requests to Ollama or configured remote providers via environment variables in `docker-compose.yml`.
- **ollama** – Local GPU-backed inference engine hosting Qwen 2.5 models for English/Swedish presets.
- **openwebui** – Front-end for reasoning, research, and tool invocation modes.
- **qdrant** – Vector store used for semantic memory, running alongside a lightweight SQLite conversation state.
- **webfetch** – HTTP microservice that performs outbound search and extraction, used by the `web_fetch` tool.

Refer to `docs/architecture/00_overview.md` for the ASCII diagram and deeper walkthroughs of each subsystem:

| File | Focus |
| --- | --- |
| `00_overview.md` | Purpose, ASCII architecture diagram, and data flow summary. |
| `01_stack.md` | Docker Compose services, volumes, and stack CLI usage. |
| `02_agent.md` | Agent modules, request lifecycle, and dependency graph. |
| `03_tools.md` | Tool registry, configuration (`config/tools.yaml`), and testing patterns. |
| `04_dev_practices.md` | Coding standards, Poetry workflow, linting, and typing. |
| `05_ci.md` | GitHub Actions pipeline (lint + test jobs). |

## Configuration & Data
- `.env` (root) – loaded by both the stack CLI and FastAPI via `python-dotenv`.
- `config/tools.yaml` – declarative registration of tool classes exposed to the agent.
- Volumes: `ollama-models` and `qdrant-data` persist models and vector storage.
- SQLite database defaults to `data/agent_state.sqlite` (mounted into the agent container) for conversation metadata.

## Security Notes
- Secrets remain in the uncommitted `.env` file; sample keys belong in `.env.template`.
- Internal services communicate over the Docker network; expose only Open WebUI or the agent externally with proper auth.
- Keep LiteLLM routing rules minimal by default; document any premium provider usage in `docs/architecture/03_tools.md`.

## Update Checklist
When changing any service or contract, synchronise the relevant `docs/architecture/*.md` file, update `docs/OPERATIONS.md` with new commands, and review CI expectations in `docs/architecture/05_ci.md`.
