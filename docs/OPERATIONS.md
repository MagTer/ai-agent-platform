# Operations

Operational runbooks target the Python-first stack managed by the Typer CLI. The
canonical configuration now lives in the root `docker-compose.yml`; optional
overrides (GPU runtime, bind mounts, etc.) can be layered via
`docker-compose.gpu.yml` or files under `compose/`. Commands are idempotent and
safe to repeat.

## Prerequisites
- Copy `.env.template` to `.env` and supply secrets or API keys.
- Install dependencies locally with `poetry install` when running tests outside Docker.
- Ensure Docker Desktop (or compatible engine) is running with GPU access for Ollama if required.
- Append overrides with `docker compose -f docker-compose.yml -f <override>.yml …`
  when applying bind mounts or GPU runtime settings manually.
  When using the Python CLI, set `STACK_COMPOSE_FILES` (for example,
  `STACK_COMPOSE_FILES=docker-compose.gpu.yml`) to apply the same overrides.

## Stack Lifecycle
```bash
# Start or update the full stack (builds images when needed)
poetry run stack up
# Alternative: python scripts/stack_tools.py up --check-litellm

# Stop containers but keep volumes and models
poetry run stack down
# Alternative: python scripts/stack_tools.py down --remove-volumes

# View service status and health checks
poetry run stack status
# Alternative: python scripts/stack_tools.py health

# Tail logs for a specific service
poetry run stack logs agent
# Alternative: python scripts/stack_tools.py logs agent
```

All commands read `.env`; rerunning `up` is safe because the CLI diff-checks the Compose configuration before recreating containers.

## Automation Catalogue

Python scripts under `scripts/` supersede the original PowerShell helpers. They
wrap Docker commands with the same guardrails (environment discovery, health
waits, idempotent behaviour) while working across macOS, Linux, and Windows.

| Task | Command | Notes |
|------|---------|-------|
| Bring the stack up (waits for models + health) | `poetry run stack up` | Supports `--check-litellm`, `--build`, `--bind-mounts`. |
| Stop the stack | `poetry run stack down` | Add `--remove-volumes` to delete persistent data. |
| Probe service health | `poetry run stack health [service]` | Mirrors `Stack-Health.ps1`. |
| Tail logs | `poetry run stack logs [service …]` | Accepts multiple services (defaults to core set). |
| Snapshot the repository | `poetry run stack repo save` | Validates Compose config then commits changes with a timestamped message. |
| Manage n8n workflows | `poetry run stack n8n export` / `import` | Includes `--include-credentials` to handle secrets metadata. |
| Manage Open WebUI database | `poetry run stack openwebui export` / `import` | Uses Docker Compose exec/cp under the hood. |
| Ensure Qdrant schema | `poetry run stack qdrant ensure-schema` | Creates or recreates the configured collection. |
| Backup/restore Qdrant | `poetry run stack qdrant backup` / `restore` | Archives `/qdrant/storage` and restarts the service safely. |

## Health Checks
```bash
# Agent service
curl -s http://localhost:8000/healthz | jq

# Qdrant
curl -s http://localhost:6333/collections | jq '.result | keys'

# Webfetch
curl -s http://localhost:8081/health | jq

# Embedder
curl -s http://localhost:8082/health | jq
```

> LiteLLM is still covered by Docker Compose healthchecks and `python -m stack status`.
> Manual probes are intentionally omitted to avoid GPU spin-up on developer machines.

## Smoke Tests
```bash
# Minimal agent completion
curl -sS -X POST http://localhost:8000/v1/agent \
  -H 'Content-Type: application/json' \
  -d '{
        "prompt": "Summarise the stack services in one sentence.",
        "metadata": {"source": "operations-smoke"}
      }' | jq '.response'

# Open WebUI pathway (agent proxies to LiteLLM/Ollama)
curl -sS -X POST http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
        "model": "ollama/llama3",
        "messages": [
          {"role": "system", "content": "You are a helpful agent."},
          {"role": "user", "content": "Confirm that requests flow through the agent."}
        ]
      }' | jq '.choices[0].message.metadata'

# Tool-assisted research via webfetch
curl -sS -X POST http://localhost:8000/v1/agent \
  -H 'Content-Type: application/json' \
  -d '{
        "prompt": "Use the web_fetch tool to gather two recent headlines about Qdrant.",
        "metadata": {
          "tools": ["web_fetch"],
          "tool_calls": [
            {"name": "web_fetch", "args": {"url": "https://qdrant.tech"}}
          ]
        }
      }' | jq '.response'
```

Expected behaviour: both endpoints return a `conversation_id`, echo the prompt
within `messages`, and expose metadata (including tool execution) in the
response payload. The `steps` array should list the orchestration trace in the
order memory → tools → completion.

## Maintenance
- **Model management**: use `ollama run qwen2.5:14b-instruct-q4_K_M` inside the `ollama` container to warm models.
- **Database**: the agent stores conversation metadata in `./data/agent_state.sqlite`. Back up or prune the file as part of maintenance.
- **LiteLLM configuration**: adjust routing or budgets via environment variables in `.env` or `docker-compose.yml`, then run `poetry run stack up` to reload.
- **Qdrant backups**: `poetry run stack qdrant backup --backup-dir backups` creates timestamped archives; restore with `poetry run stack qdrant restore backups/<file>.tgz`.
- **Qdrant schema**: `poetry run stack qdrant ensure-schema --collection agent-memories --size 1536` ensures collections exist before ingestion.
- **n8n exports/imports**: `poetry run stack n8n export --include-credentials` captures workflows locally; `poetry run stack n8n import` pushes them back.
- **Open WebUI database**: `poetry run stack openwebui export` dumps `app.db`; restore with the matching `import` command after editing outside the container.
- **Repository snapshots**: run `poetry run stack repo save --message "chore: ops snapshot"` to validate Compose and commit changes.
- **Dependency updates**: when `scripts/deps_check.py` flags new versions, validate
  changes by running the stack smoke tests in this guide and `poetry run pytest`.
  Merge only after both checks succeed.

### Qdrant memory ID migration

Older deployments used composite point IDs (`conversation_id:index`) for semantic memories. The
service now assigns opaque UUIDs to avoid collisions across batches while keeping the
`conversation_id` in the payload for filtering. To migrate existing data, run the helper script
once per environment:

```bash
poetry run python scripts/migrate_qdrant_memory_ids.py \
  --url "http://localhost:6333" \
  --collection "agent-memories"
```

The script scrolls through the configured collection, re-inserts each point with a new UUID, and
deletes the original identifiers so historical conversations remain discoverable.

## Troubleshooting
- **Service unhealthy**: `python -m stack logs <service>` to inspect container output.
- **Port conflicts**: review overrides in `.env`; adjust `AGENT_PORT`, `LITELLM_PORT`, etc., then rerun `python -m stack up`.
- **Dependency mismatch**: run `poetry lock --check` to ensure the lock file is current before rebuilding images.

## Operational Checklist Before Release
1. Stack commands succeed twice consecutively (`up` ➜ `down` ➜ `up`).
2. Smoke tests above return HTTP 200 with valid JSON.
3. `poetry run ruff check .` and `poetry run pytest -v` succeed locally or in CI.
4. Documentation updates merged into `docs/architecture/` and this runbook.
